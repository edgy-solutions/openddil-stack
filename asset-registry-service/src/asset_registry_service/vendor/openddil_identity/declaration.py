"""The bind-before-mint decision, as a pure function.

Implements first-increment spec sections 1.3 and 2.3 steps 1-5
(decisions/DESIGN-2026-11-08-adr0056-first-increment.md). Nothing calls this
module yet. No randomness and no minting here: the caller mints for ``Mint``.
"""

from __future__ import annotations

import hashlib
import json
import uuid
from collections.abc import Callable, Mapping
from dataclasses import dataclass

from .ids import Identity, LookupKey, lookup_key


def declaration_ref(record: Mapping) -> str:
    """SHA-256 hex of the record's canonical JSON (spec 1.3)."""
    text = json.dumps(
        record,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    )
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class Alias:
    scheme: str
    value: str
    lookup_key: LookupKey


@dataclass(frozen=True)
class Binding:
    """The existing binding row for an asset_id."""

    identity: Identity
    declaration: str


@dataclass(frozen=True)
class Replay:
    declaration: str


@dataclass(frozen=True)
class Bind:
    declaration: str
    identity: Identity
    new_aliases: tuple[Alias, ...]
    binding_exists: bool
    skipped: tuple[str, ...]


@dataclass(frozen=True)
class Mint:
    declaration: str
    aliases: tuple[Alias, ...]
    skipped: tuple[str, ...]


@dataclass(frozen=True)
class Conflict:
    declaration: str
    asset_id: str
    identities: tuple[Identity, ...]
    reason: str
    skipped: tuple[str, ...]


def _sorted_ids(ids) -> tuple[Identity, ...]:
    return tuple(sorted(ids, key=lambda i: str(i.value)))


def decide(
    declaration: Mapping,
    *,
    asset_id_scheme: str,
    registered_schemes: Mapping[str, uuid.UUID],
    resolve: Callable[[str, LookupKey], Identity | None],
    current_binding: Callable[[str], Binding | None],
) -> Replay | Bind | Mint | Conflict:
    asset_id = declaration.get("asset_id")
    if not isinstance(asset_id, str) or not asset_id:
        raise ValueError("declaration needs a non-empty str asset_id")
    if not isinstance(asset_id_scheme, str) or not asset_id_scheme:
        raise ValueError("asset_id_scheme must be a non-empty str")
    if asset_id_scheme not in registered_schemes:
        # The deployment's own scheme is seeded with the tables (spec 1.1). Without it every
        # declaration would mint an identity no alias can find, so refuse rather than mint.
        raise ValueError("asset_id_scheme is not a registered scheme")

    ref = declaration_ref(declaration)
    b = current_binding(asset_id)
    if b is not None and b.declaration == ref:
        return Replay(ref)

    carried = (
        (asset_id_scheme, asset_id),
        ("serial", declaration.get("serial")),
        ("uii", declaration.get("uii")),
    )
    kept: list[tuple[Alias, Identity | None]] = []
    skipped: list[str] = []
    for scheme, value in carried:
        if value is None or value == "":
            continue
        namespace = registered_schemes.get(scheme)
        if namespace is None:
            skipped.append(scheme)
            continue
        lk = lookup_key(namespace, value)
        kept.append((Alias(scheme, value, lk), resolve(scheme, lk)))
    skipped_t = tuple(sorted(skipped))

    ids: list[Identity] = []
    for _, found in kept:
        if found is not None and found not in ids:
            ids.append(found)

    if len(ids) > 1:
        return Conflict(ref, asset_id, _sorted_ids(ids), "aliases_disagree", skipped_t)
    if len(ids) == 1:
        if b is not None and b.identity != ids[0]:
            return Conflict(
                ref,
                asset_id,
                _sorted_ids((b.identity, ids[0])),
                "asset_bound_elsewhere",
                skipped_t,
            )
        new = tuple(a for a, found in kept if found is None)
        return Bind(ref, ids[0], new, b is not None, skipped_t)
    if b is None:
        return Mint(ref, tuple(a for a, _ in kept), skipped_t)
    # Not covered by spec 2.3: the asset is bound but none of its aliases is
    # open (for example an equivalence closed them). Never mint for a bound
    # asset and never guess; refuse and let a person decide.
    return Conflict(ref, asset_id, (b.identity,), "bound_without_alias", skipped_t)
