"""Asset identity checks that need a real postgres (DESIGN-2026-11-08 section 5).

They read ASSET_REGISTRY_TEST_PG_DSN, apply every migration in schema/migrations to a fresh
database, and skip locally when it is unset (see identity_support). In CI the skip fails the job.
"""
from __future__ import annotations

import asyncio
import uuid
from datetime import datetime, timedelta, timezone

import asyncpg
import pytest

from asset_registry_service import db, identity_store
from asset_registry_service.vendor.openddil_identity import (
    GENERATOR,
    GENERATOR_VERSION,
    era,
    lookup_key,
    mint_identity,
)

from .identity_support import (
    SEEDED_SCHEMES,
    SIX_TABLES,
    counts,
    pg_dsn,  # noqa: F401  (session fixture)
    requires_pg,
    run_pg,
    scheme_namespace,
)
from .test_asset_identity_static import LOOKUP_VECTORS, served_mentions

pytestmark = requires_pg

TIER = "hq-test"


def decl(i: int, *, serial: bool = True, uii: bool = False, **extra) -> dict:
    rec = {"asset_id": f"urn:test:asset-{i:04d}", "declared_by": "test"}
    if serial:
        rec["serial"] = f"SN-TEST-{i:04d}"
    if uii:
        rec["uii"] = f"UII-TEST-{i:04d}"
    rec.update(extra)
    return rec


async def declare(pool, rec: dict):
    return await identity_store.process_declaration(rec, tier=TIER, pool=pool)


async def plant_identity(pool, aliases: dict) -> uuid.UUID:
    """An identity that already exists, holding the given {scheme: value} aliases. Written
    directly, as an earlier run of the registry would have left it."""
    ident = mint_identity().value
    async with pool.acquire() as conn:
        await conn.execute(
            "INSERT INTO public.asset_identity VALUES ($1, now(), $2, $3, $4, $5)",
            ident, TIER, GENERATOR, GENERATOR_VERSION, "planted",
        )
        for scheme, value in aliases.items():
            ns = await scheme_namespace(pool, scheme)
            await conn.execute(
                "INSERT INTO public.asset_alias VALUES ($1, $2, $3, $4, 'planted', now(), NULL, 'planted')",
                scheme, value, lookup_key(ns, value).value, ident,
            )
    return ident


def test_schema_constraints(pg_dsn):
    async def go(pool):
        async with pool.acquire() as conn:
            schemes = [r["scheme"] for r in await conn.fetch("SELECT scheme FROM public.alias_scheme ORDER BY scheme")]
            assert schemes == sorted(SEEDED_SCHEMES)
            row = await conn.fetchrow("SELECT owner FROM public.alias_scheme WHERE scheme = 'asset_id'")
            assert row["owner"] == "deployment"
            assert await conn.fetchval(
                "SELECT bool_and(registered_by = 'migration 20261122000000') FROM public.alias_scheme"
            )
        a = await plant_identity(pool, {"serial": "SN-TEST-X"})
        b = await plant_identity(pool, {})
        async with pool.acquire() as conn:
            # the open-alias index: a second open row for one (scheme, lookup_key) is refused ...
            with pytest.raises(asyncpg.UniqueViolationError):
                await plant_identity(pool, {"serial": "SN-TEST-X"})
            # ... a closed one is allowed
            ns = await scheme_namespace(pool, "serial")
            await conn.execute(
                "INSERT INTO public.asset_alias VALUES ('serial', 'SN-TEST-X', $1, $2, 'x', now(), now(), 'x')",
                lookup_key(ns, "SN-TEST-X").value, b,
            )
            # one binding per asset_id
            await conn.execute("INSERT INTO public.asset_binding VALUES ('urn:test:one', $1, now(), 'x', 'x')", a)
            with pytest.raises(asyncpg.UniqueViolationError):
                await conn.execute("INSERT INTO public.asset_binding VALUES ('urn:test:one', $1, now(), 'x', 'x')", b)
            # survivor <> merged
            with pytest.raises(asyncpg.CheckViolationError):
                await conn.execute(
                    "INSERT INTO public.identity_equivalence VALUES ($1, $1, 'o', now(), 'r', 'd')", a
                )

    run_pg(pg_dsn, go)


def test_identity_not_lookup_key(pg_dsn):
    async def go(pool):
        outcomes = []
        for start in range(0, 1000, 10):
            outcomes += await asyncio.gather(
                *(declare(pool, decl(i, uii=(i % 2 == 0))) for i in range(start, start + 10))
            )
        assert {o.kind for o in outcomes} == {"mint"}
        async with pool.acquire() as conn:
            idents = [r["identity"] for r in await conn.fetch("SELECT identity FROM public.asset_identity")]
            assert len(idents) == 1000
            assert all(u.version == 7 and u.variant == uuid.RFC_4122 for u in idents)
            keys = {r["lookup_key"] for r in await conn.fetch("SELECT lookup_key FROM public.asset_alias")}
            assert len(keys) == 1000 + 1000 + 500
            assert not (set(idents) & keys), "an identity is never a lookup key"
            assert await conn.fetchval(
                "SELECT count(*) FROM public.asset_identity i JOIN public.asset_alias a ON a.lookup_key = i.identity"
            ) == 0
            assert await conn.fetchval(
                "SELECT count(*) FROM public.asset_identity WHERE generator = $1 AND generator_version = $2 "
                "AND minting_tier = $3", GENERATOR, GENERATOR_VERSION, TIER,
            ) == 1000

    run_pg(pg_dsn, go, size=10)


def test_era_from_binding_row(pg_dsn):
    async def go(pool):
        rec = decl(1)
        t0 = datetime.now(timezone.utc) - timedelta(days=1)
        # before any binding: era 1 (SQL and Python)
        assert await identity_store.asset_id_era(rec["asset_id"], t0, pool=pool) == 1
        assert (await declare(pool, rec)).kind == "mint"
        async with pool.acquire() as conn:
            bound_at = await conn.fetchval("SELECT bound_at FROM public.asset_binding WHERE asset_id = $1", rec["asset_id"])
        assert bound_at.tzinfo is not None
        for first_observed, expected in (
            (bound_at - timedelta(seconds=1), 1),
            (bound_at - timedelta(microseconds=1), 1),
            (bound_at, 2),
            (bound_at + timedelta(microseconds=1), 2),
            (bound_at + timedelta(days=30), 2),
        ):
            assert await identity_store.asset_id_era(rec["asset_id"], first_observed, pool=pool) == expected
            assert era(bound_at, first_observed) == expected, "the library and the SQL agree"
        # another asset that was never bound
        assert await identity_store.asset_id_era("urn:test:never", bound_at + timedelta(days=1), pool=pool) == 1
        # bound_at is the write time, not the declaration's time
        old = decl(2, declared_at="2001-01-01T00:00:00Z")
        await declare(pool, old)
        async with pool.acquire() as conn:
            b2 = await conn.fetchval("SELECT bound_at FROM public.asset_binding WHERE asset_id = $1", old["asset_id"])
        assert b2 > datetime(2026, 1, 1, tzinfo=timezone.utc)

    run_pg(pg_dsn, go)


def test_lookup_before_mint(pg_dsn):
    async def go(pool):
        x = await plant_identity(pool, {"uii": "UII-TEST-0042"})
        before = await counts(pool)
        assert before["asset_identity"] == 1
        rec = {"asset_id": "urn:test:new-asset", "uii": "UII-TEST-0042"}
        out = await declare(pool, rec)
        assert out.kind == "bind" and out.identity == x
        after = await counts(pool)
        assert after["asset_identity"] == 1, "nothing was minted"
        assert after["asset_binding"] == 1 and after["asset_alias"] == before["asset_alias"] + 1
        async with pool.acquire() as conn:
            row = await conn.fetchrow("SELECT identity FROM public.asset_binding WHERE asset_id = $1", rec["asset_id"])
            assert row["identity"] == x
            assert await conn.fetchval(
                "SELECT count(*) FROM public.asset_alias WHERE identity = $1 AND scheme = 'asset_id' AND valid_to IS NULL", x
            ) == 1

    run_pg(pg_dsn, go)


def test_replay_is_idempotent(pg_dsn):
    async def go(pool):
        topic = [decl(i, serial=(i % 3 != 0), uii=(i % 2 == 0)) for i in range(40)]
        first = [await declare(pool, r) for r in topic]
        assert {o.kind for o in first} == {"mint"}
        after_one = await counts(pool)
        second = [await declare(pool, r) for r in topic]  # a new consumer group re-reads the topic
        assert {o.kind for o in second} == {"replay"}
        assert await counts(pool) == after_one
        assert after_one["asset_identity"] == 40 and after_one["asset_binding"] == 40
        # the same topic read twice more, in one burst, changes nothing either
        await asyncio.gather(*(declare(pool, r) for r in topic + topic))
        assert await counts(pool) == after_one
        # a changed declaration for a bound asset adds its new alias and mints nothing
        changed = dict(topic[3], serial="SN-TEST-CHANGED")
        out = await declare(pool, changed)
        assert out.kind == "bind"
        now = await counts(pool)
        assert now["asset_identity"] == 40 and now["asset_binding"] == 40
        assert now["asset_alias"] == after_one["asset_alias"] + 1

    run_pg(pg_dsn, go)


def test_race_one_identity(pg_dsn):
    async def go(pool):
        for i in range(25):
            serial = f"SN-TEST-RACE-{i:03d}"
            a = {"asset_id": f"urn:test:race-{i:03d}-a", "serial": serial}
            b = {"asset_id": f"urn:test:race-{i:03d}-b", "serial": serial}
            oa, ob = await asyncio.gather(declare(pool, a), declare(pool, b))
            assert "retries_exhausted" not in (oa.kind, ob.kind)
            assert {oa.kind, ob.kind} <= {"mint", "bind"}
            async with pool.acquire() as conn:
                rows = await conn.fetch(
                    "SELECT identity FROM public.asset_binding WHERE asset_id IN ($1, $2)", a["asset_id"], b["asset_id"]
                )
            assert len(rows) == 2 and rows[0]["identity"] == rows[1]["identity"]
        c = await counts(pool)
        assert c["asset_identity"] == 25 and c["asset_binding"] == 50 and c["identity_conflict"] == 0

    run_pg(pg_dsn, go)


def test_race_on_one_asset_id_binds_once(pg_dsn):
    async def go(pool):
        # two different declarations for one asset_id race: one binding survives
        a = {"asset_id": "urn:test:same", "serial": "SN-TEST-A"}
        b = {"asset_id": "urn:test:same", "serial": "SN-TEST-B"}
        outs = await asyncio.gather(declare(pool, a), declare(pool, b))
        assert "retries_exhausted" not in {o.kind for o in outs}
        c = await counts(pool)
        assert c["asset_binding"] == 1

    run_pg(pg_dsn, go)


def test_conflict_binds_nothing(pg_dsn):
    async def go(pool):
        x = await plant_identity(pool, {"serial": "SN-TEST-0007"})
        y = await plant_identity(pool, {"uii": "UII-TEST-0007"})
        before = await counts(pool)
        rec = {"asset_id": "urn:test:contested", "serial": "SN-TEST-0007", "uii": "UII-TEST-0007"}
        out = await declare(pool, rec)
        assert out.kind == "conflict" and out.reason == "aliases_disagree"
        after = await counts(pool)
        assert after["identity_conflict"] == 1
        after["identity_conflict"] = before["identity_conflict"]
        assert after == before, "no identity, alias or binding row was written"
        async with pool.acquire() as conn:
            row = await conn.fetchrow("SELECT * FROM public.identity_conflict")
        assert row["asset_id"] == rec["asset_id"] and set(row["identities"]) == {x, y}
        assert row["raised_at"] is not None
        # a replay of the conflicted record does not raise a second row
        await declare(pool, rec)
        assert (await counts(pool))["identity_conflict"] == 1

    run_pg(pg_dsn, go)


def test_asset_bound_elsewhere_is_a_conflict(pg_dsn):
    async def go(pool):
        first = await declare(pool, {"asset_id": "urn:test:a", "serial": "SN-TEST-A"})
        other = await plant_identity(pool, {"uii": "UII-TEST-OTHER"})
        before = await counts(pool)
        out = await declare(pool, {"asset_id": "urn:test:a", "serial": "SN-TEST-A", "uii": "UII-TEST-OTHER"})
        assert out.kind == "conflict" and first.identity != other
        after = await counts(pool)
        assert after["identity_conflict"] == before["identity_conflict"] + 1
        assert after["asset_binding"] == before["asset_binding"]

    run_pg(pg_dsn, go)


def test_observation_writes_no_identity(pg_dsn, monkeypatch):
    async def go(pool):
        monkeypatch.setattr(db, "_pool", pool)
        before = await counts(pool)
        assert before["alias_scheme"] == 4 and sum(v for k, v in before.items() if k != "alias_scheme") == 0
        row = await db.upsert_observation(
            asset_id="urn:test:observed-only",
            observed_edge_id="edge-test",
            proposed_edge_id="edge-test",
            proposed_region_id="region-test",
            proposed_source="connection",
            proposed_by="test",
        )
        assert row.asset_id == "urn:test:observed-only"
        async with pool.acquire() as conn:
            assert await conn.fetchval("SELECT count(*) FROM public.asset_registry WHERE asset_id = 'urn:test:observed-only'") == 1
        assert await counts(pool) == before, "an observation writes no row to any identity table"

    run_pg(pg_dsn, go)


def test_no_served_identity_table(pg_dsn):
    assert served_mentions() == []

    async def go(pool):
        async with pool.acquire() as conn:
            rows = await conn.fetch(
                "SELECT tablename FROM pg_publication_tables "
                "WHERE pubname = 'electric_publication' AND tablename = ANY($1::text[])",
                list(SIX_TABLES),
            )
            assert rows == []
            # whatever publications exist in this database, none holds a table of the six
            rows = await conn.fetch(
                "SELECT pubname, tablename FROM pg_publication_tables WHERE tablename = ANY($1::text[])",
                list(SIX_TABLES),
            )
            assert rows == []
            assert await conn.fetchval(
                "SELECT count(*) FROM pg_publication WHERE puballtables"
            ) == 0

    run_pg(pg_dsn, go)


def test_equivalence_flow(pg_dsn):
    async def go(pool):
        a = await declare(pool, decl(1))
        b = await declare(pool, decl(2))
        survivor, merged = a.identity, b.identity
        await identity_store.declare_equivalence(
            survivor, merged, "unit owner", "same airframe", "ref-1", pool=pool
        )
        async with pool.acquire() as conn:
            eq = await conn.fetchrow("SELECT * FROM public.identity_equivalence")
            assert (eq["survivor"], eq["merged"], eq["owner"], eq["reason"]) == (survivor, merged, "unit owner", "same airframe")
            assert await conn.fetchval(
                "SELECT count(*) FROM public.asset_alias WHERE identity = $1 AND valid_to IS NULL", merged
            ) == 0
            moved = await conn.fetchrow(
                "SELECT * FROM public.asset_alias WHERE scheme = 'identity' AND identity = $1", survivor
            )
            assert moved["value"] == f"urn:uuid:{merged}" and moved["valid_to"] is not None
            assert await conn.fetchval("SELECT count(*) FROM public.asset_identity") == 2, "nothing is deleted"
        for args, reason in (
            ((survivor, merged, "o", "r", "d"), "already_merged"),
            ((survivor, survivor, "o", "r", "d"), "survivor_is_merged"),
            ((merged, survivor, "", "r", "d"), "owner_missing"),
            ((merged, survivor, "o", " ", "d"), "reason_missing"),
        ):
            with pytest.raises(identity_store.EquivalenceRefused) as exc:
                await identity_store.declare_equivalence(*args, pool=pool)
            assert exc.value.reason == reason
        assert (await counts(pool))["identity_equivalence"] == 1

    run_pg(pg_dsn, go)


def test_lookup_key_vectors_in_postgres(pg_dsn):
    async def go(pool):
        async with pool.acquire() as conn:
            try:
                await conn.execute('CREATE EXTENSION IF NOT EXISTS "uuid-ossp"')
            except asyncpg.PostgresError as exc:
                pytest.skip(f"uuid-ossp cannot be created in the test database: {exc.__class__.__name__}")
            for ns, value, key in LOOKUP_VECTORS:
                got = await conn.fetchval("SELECT uuid_generate_v5($1::uuid, $2)", ns, value)
                assert str(got) == key, (ns, value)
            # the namespaces in the store are the ones the vectors use
            stored = {r["scheme"]: str(r["namespace"]) for r in await conn.fetch("SELECT scheme, namespace FROM public.alias_scheme")}
            assert set(stored.values()) >= {ns for ns, _, _ in LOOKUP_VECTORS}
            # and a lookup key the registry wrote equals uuid_generate_v5 over the same pair
        await declare(pool, decl(5))
        async with pool.acquire() as conn:
            bad = await conn.fetchval(
                "SELECT count(*) FROM public.asset_alias a JOIN public.alias_scheme s USING (scheme) "
                "WHERE a.lookup_key <> uuid_generate_v5(s.namespace, a.value)"
            )
            assert bad == 0

    run_pg(pg_dsn, go)
