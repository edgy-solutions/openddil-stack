"""Shared pieces for the asset identity tests (no test collected here).

The real-postgres tests read ASSET_REGISTRY_TEST_PG_DSN. When it is unset they skip, and
the skip reason names that variable, so CI can fail the job on exactly those skips. When it
is set, one fresh database is created from it for the whole session, every migration in
schema/migrations is applied to it in order, and it is dropped at the end.
"""
from __future__ import annotations

import asyncio
import os
import uuid
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

import asyncpg
import pytest

REPO_ROOT = Path(__file__).resolve().parents[3]
MIGRATIONS = REPO_ROOT / "schema" / "migrations"
TEST_DSN = os.environ.get("ASSET_REGISTRY_TEST_PG_DSN", "")
SKIP_REASON = "ASSET_REGISTRY_TEST_PG_DSN is not set; the real-postgres identity tests run in CI"

requires_pg = pytest.mark.skipif(not TEST_DSN, reason=SKIP_REASON)

SIX_TABLES = (
    "alias_scheme", "asset_identity", "asset_alias",
    "asset_binding", "identity_equivalence", "identity_conflict",
)
SEEDED_SCHEMES = ("asset_id", "serial", "identity", "uii")


def _with_database(dsn: str, name: str) -> str:
    parts = urlsplit(dsn)
    return urlunsplit((parts.scheme, parts.netloc, "/" + name, parts.query, parts.fragment))


async def _create_and_migrate(name: str) -> None:
    admin = await asyncpg.connect(TEST_DSN)
    try:
        await admin.execute(f'CREATE DATABASE "{name}"')
    finally:
        await admin.close()
    conn = await asyncpg.connect(_with_database(TEST_DSN, name))
    try:
        for path in sorted(MIGRATIONS.glob("*.sql")):
            await conn.execute(path.read_text(encoding="utf-8"))
    finally:
        await conn.close()


async def _drop(name: str) -> None:
    admin = await asyncpg.connect(TEST_DSN)
    try:
        await admin.execute(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)')
    finally:
        await admin.close()


@pytest.fixture(scope="session")
def pg_dsn():
    """DSN of a fresh database with every migration applied."""
    if not TEST_DSN:
        pytest.skip(SKIP_REASON)
    name = "asset_registry_test_" + uuid.uuid4().hex[:12]
    asyncio.run(_create_and_migrate(name))
    try:
        yield _with_database(TEST_DSN, name)
    finally:
        asyncio.run(_drop(name))


_RESET = (
    "TRUNCATE public.asset_alias, public.asset_binding, public.identity_equivalence, "
    "public.identity_conflict, public.asset_identity, public.asset_registry"
)


def run_pg(pg_dsn: str, fn, *, size: int = 6):
    """Run `async fn(pool)` on an emptied store (the four scheme rows stay), return its result."""

    async def main():
        pool = await asyncpg.create_pool(pg_dsn, min_size=1, max_size=size)
        try:
            async with pool.acquire() as conn:
                await conn.execute(_RESET)
            return await fn(pool)
        finally:
            await pool.close()

    return asyncio.run(main())


async def counts(pool) -> dict:
    async with pool.acquire() as conn:
        return {t: await conn.fetchval(f"SELECT count(*) FROM public.{t}") for t in SIX_TABLES}


async def scheme_namespace(pool, scheme: str) -> uuid.UUID:
    async with pool.acquire() as conn:
        return await conn.fetchval("SELECT namespace FROM public.alias_scheme WHERE scheme = $1", scheme)
