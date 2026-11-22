"""The identity store: bind-before-mint, the era read, and the owned equivalence.

ADR-0056's first increment, store half (DESIGN-2026-11-08 sections 1, 2.3, 2.7, 2.8).
The decisions are the vendored library's pure functions (declaration_ref, decide,
check_equivalence, era, lookup_key, mint_identity); this module supplies the SQL and
the resolver values `decide` is handed. It imports asyncpg but not faust, so it is
testable on its own.

Rules kept here (section 1.2): no UPDATE of identity, asset_id, value or lookup_key, and
no DELETE. The one column ever updated is asset_alias.valid_to, in declare_equivalence.
Nothing here touches asset_registry; db.upsert_observation is untouched (section 2.6).
"""
from __future__ import annotations

import json
import logging
import uuid
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Mapping, Optional

import asyncpg

from . import db
from .vendor.openddil_identity import (
    GENERATOR,
    GENERATOR_VERSION,
    Alias,
    Bind,
    Binding,
    Conflict,
    Identity,
    Mint,
    Ok,
    Refused,
    Replay,
    as_iri,
    check_equivalence,
    declaration_ref,
    decide,
    lookup_key,
    mint_identity,
)

log = logging.getLogger(__name__)

# The deployment's own asset_id scheme, seeded by the migration (section 1.1).
ASSET_ID_SCHEME = "asset_id"
IDENTITY_SCHEME = "identity"
MAX_ATTEMPTS = 3


@dataclass(frozen=True)
class Outcome:
    """What one declaration did. kind: replay, bind, mint, conflict, retries_exhausted."""

    kind: str
    declaration: str
    identity: Optional[uuid.UUID] = None
    reason: str = ""
    attempts: int = 1


class EquivalenceRefused(ValueError):
    """declare_equivalence refused; `reason` is the library's refusal code."""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


def parse_declaration(raw: "bytes | str") -> Optional[dict]:
    """The record as a dict, or None when it is not JSON, not an object, or has no asset_id."""
    try:
        record = json.loads(raw)
    except (ValueError, TypeError):
        return None
    if not isinstance(record, dict):
        return None
    asset_id = record.get("asset_id")
    if not isinstance(asset_id, str) or not asset_id:
        return None
    return record


def _bad_record(exc: BaseException) -> bool:
    """A failure caused by the record's content, never by the store being away."""
    if isinstance(exc, (ValueError, TypeError)):
        return True
    state = getattr(exc, "sqlstate", None)
    return isinstance(exc, asyncpg.PostgresError) and isinstance(state, str) and state.startswith("22")


async def handle_declaration(raw: "bytes | str", *, tier: str, pool: Any = None) -> Optional[Outcome]:
    """One topic record. A bad record is logged and skipped, never raised, so the
    agent cannot crash-loop on it. A store failure still raises: the offset must not
    advance past a declaration that was not written."""
    record = parse_declaration(raw)
    if record is None:
        log.warning("declaration skipped: not a JSON object with an asset_id")
        return None
    try:
        return await process_declaration(record, tier=tier, pool=pool)
    except Exception as exc:  # noqa: BLE001
        if _bad_record(exc):
            log.warning("declaration for %r skipped: %s: %s", record.get("asset_id"), type(exc).__name__, exc)
            return None
        raise


async def process_declaration(
    record: Mapping[str, Any], *, tier: str, pool: Any = None, max_attempts: int = MAX_ATTEMPTS
) -> Outcome:
    """Section 2.3, in one transaction per attempt. A unique violation (two
    declarations racing on one alias or one asset_id) rolls back and redoes from step 1;
    the second pass finds the first's identity. Bounded: after max_attempts the failure
    is logged and reported, never retried again."""
    pool = pool or db._pool_required()
    ref = declaration_ref(record)
    for attempt in range(1, max_attempts + 1):
        try:
            async with pool.acquire() as conn:
                async with conn.transaction():
                    out = await _apply(conn, record, tier)
            return Outcome(out.kind, out.declaration, out.identity, out.reason, attempt)
        except asyncpg.UniqueViolationError as exc:
            log.info("declaration %s: unique violation on attempt %d (%s)", ref[:12], attempt, exc.constraint_name)
    log.error("declaration %s: still racing after %d attempts; not written", ref[:12], max_attempts)
    return Outcome("retries_exhausted", ref, attempts=max_attempts)


def _carried(record: Mapping[str, Any]) -> list[tuple[str, str]]:
    """(scheme, value) per carried alias: asset_id always, serial and uii when non-empty."""
    out = [(ASSET_ID_SCHEME, record["asset_id"])]
    for scheme in ("serial", "uii"):
        value = record.get(scheme)
        if value is None or value == "":
            continue
        if not isinstance(value, str):
            raise TypeError(f"{scheme} must be a string")
        out.append((scheme, value))
    return out


async def _apply(conn: Any, record: Mapping[str, Any], tier: str) -> Outcome:
    # Everything `decide` needs is read here, in this transaction, because `decide`
    # takes plain callables: the registered schemes, the binding of this asset_id, and
    # the identity (if any) holding each carried alias's open row, locked FOR UPDATE.
    schemes = {
        r["scheme"]: r["namespace"] for r in await conn.fetch('SELECT scheme, namespace FROM public.alias_scheme')
    }
    asset_id = record["asset_id"]
    brow = await conn.fetchrow(
        'SELECT identity, declaration FROM public.asset_binding WHERE asset_id = $1', asset_id
    )
    binding = Binding(Identity(brow["identity"]), brow["declaration"]) if brow else None

    found: dict[tuple[str, uuid.UUID], Identity] = {}
    keys = []
    for scheme, value in _carried(record):
        ns = schemes.get(scheme)
        if ns is not None:
            keys.append((scheme, lookup_key(ns, value).value))
    for scheme, lk in sorted(keys):  # a fixed lock order
        row = await conn.fetchrow(
            'SELECT identity FROM public.asset_alias '
            'WHERE scheme = $1 AND lookup_key = $2 AND valid_to IS NULL FOR UPDATE',
            scheme,
            lk,
        )
        if row is not None:
            found[(scheme, lk)] = Identity(row["identity"])

    decision = decide(
        record,
        asset_id_scheme=ASSET_ID_SCHEME,
        registered_schemes=schemes,
        resolve=lambda scheme, key: found.get((scheme, key.value)),
        current_binding=lambda _asset_id: binding,
    )
    for scheme in getattr(decision, "skipped", ()):
        log.warning("declaration %s: alias scheme %r is not registered; skipped", decision.declaration[:12], scheme)

    who = f"registry:{tier}"
    if isinstance(decision, Replay):
        return Outcome("replay", decision.declaration)
    if isinstance(decision, Conflict):
        await conn.execute(
            'INSERT INTO public.identity_conflict (declaration, asset_id, identities, raised_at) '
            'SELECT $1::text, $2::text, $3::uuid[], now() '
            'WHERE NOT EXISTS (SELECT 1 FROM public.identity_conflict WHERE declaration = $1)',
            decision.declaration,
            decision.asset_id,
            [i.value for i in decision.identities],
        )
        return Outcome("conflict", decision.declaration, reason=decision.reason)
    if isinstance(decision, Mint):
        ident = mint_identity()
        await conn.execute(
            'INSERT INTO public.asset_identity '
            '(identity, minted_at, minting_tier, generator, generator_version, declaration) '
            'VALUES ($1, now(), $2, $3, $4, $5)',
            ident.value,
            tier,
            GENERATOR,
            GENERATOR_VERSION,
            decision.declaration,
        )
        await _insert_aliases(conn, decision.aliases, ident, who, decision.declaration)
        await _insert_binding(conn, asset_id, ident, who, decision.declaration)
        return Outcome("mint", decision.declaration, ident.value)
    assert isinstance(decision, Bind)
    await _insert_aliases(conn, decision.new_aliases, decision.identity, who, decision.declaration)
    if not decision.binding_exists:
        await _insert_binding(conn, asset_id, decision.identity, who, decision.declaration)
    return Outcome("bind", decision.declaration, decision.identity.value)


async def _insert_aliases(conn: Any, aliases, ident: Identity, who: str, declaration: str) -> None:
    for a in aliases:
        assert isinstance(a, Alias)
        await conn.execute(
            'INSERT INTO public.asset_alias '
            '(scheme, value, lookup_key, identity, authority, valid_from, valid_to, declaration) '
            'VALUES ($1, $2, $3, $4, $5, now(), NULL, $6)',
            a.scheme,
            a.value,
            a.lookup_key.value,
            ident.value,
            who,
            declaration,
        )


async def _insert_binding(conn: Any, asset_id: str, ident: Identity, who: str, declaration: str) -> None:
    # bound_at is the write time (now() of this transaction), never the declaration's time.
    await conn.execute(
        'INSERT INTO public.asset_binding (asset_id, identity, bound_at, bound_by, declaration) '
        'VALUES ($1, $2, now(), $3, $4)',
        asset_id,
        ident.value,
        who,
        declaration,
    )


async def asset_id_era(asset_id: str, first_observed: datetime, *, pool: Any = None) -> int:
    """Section 2.7: calls the SQL function of the same name. The inputs are an asset_id
    and a time, never an id. Nothing calls this in this increment except tests."""
    pool = pool or db._pool_required()
    async with pool.acquire() as conn:
        return int(await conn.fetchval("SELECT public.asset_id_era($1, $2)", asset_id, first_observed))


async def declare_equivalence(
    survivor: uuid.UUID,
    merged: uuid.UUID,
    owner: str,
    reason: str,
    declaration: str,
    *,
    pool: Any = None,
) -> None:
    """Section 2.8: the only writer of identity_equivalence. In one transaction it writes
    the equivalence row, closes `merged`'s open alias rows, and records `merged` as a
    closed alias of `survivor` under the `identity` scheme. It raises EquivalenceRefused
    for an empty owner or reason, survivor == merged, or a merged already merged."""
    pool = pool or db._pool_required()
    async with pool.acquire() as conn:
        async with conn.transaction():
            already = await conn.fetchval(
                "SELECT EXISTS (SELECT 1 FROM public.identity_equivalence WHERE merged = $1)", merged
            )
            verdict = check_equivalence(Identity(survivor), Identity(merged), owner, reason, bool(already))
            if isinstance(verdict, Refused):
                raise EquivalenceRefused(verdict.reason)
            assert isinstance(verdict, Ok)
            await conn.execute(
                "INSERT INTO public.identity_equivalence "
                "(survivor, merged, owner, declared_at, reason, declaration) "
                "VALUES ($1, $2, $3, now(), $4, $5)",
                survivor,
                merged,
                owner,
                reason,
                declaration,
            )
            await conn.execute(
                "UPDATE public.asset_alias SET valid_to = now() WHERE identity = $1 AND valid_to IS NULL",
                merged,
            )
            ns = await conn.fetchval("SELECT namespace FROM public.alias_scheme WHERE scheme = $1", IDENTITY_SCHEME)
            if ns is None:
                raise RuntimeError("the identity alias scheme is not registered")
            value = as_iri(Identity(merged))
            await conn.execute(
                "INSERT INTO public.asset_alias "
                "(scheme, value, lookup_key, identity, authority, valid_from, valid_to, declaration) "
                "VALUES ($1, $2, $3, $4, $5, now(), now(), $6)",
                IDENTITY_SCHEME,
                value,
                lookup_key(ns, value).value,
                survivor,
                owner,
                declaration,
            )
