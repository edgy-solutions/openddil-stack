"""Shared fixtures.

asyncpg-free fake pool that records every SQL call. Lets the priority
logic in db.upsert_observation be tested at the function level without
spinning up a real postgres.

The fake is intentionally minimal -- it implements only the asyncpg
pool / connection methods upsert_observation calls
(acquire/fetchrow/execute), and the test asserts on captured SQL
strings + params. If db.py grows more pg surface area, extend this
fake to match.
"""
from __future__ import annotations
import asyncio
import pytest
from typing import Any
from contextlib import asynccontextmanager

from asset_registry_service import db


class FakeConn:
    """One asyncpg-like connection. Returns a pre-seeded row from
    fetchrow() and records every execute() for assertion."""
    def __init__(self, seeded_row: dict | None = None):
        self.seeded_row = seeded_row
        self.executed: list[tuple[str, tuple]] = []

    async def fetchrow(self, sql: str, *args):
        return self.seeded_row

    async def execute(self, sql: str, *args):
        # Normalize whitespace for easier substring matching in tests.
        self.executed.append((" ".join(sql.split()), args))
        return "UPDATE 1"  # asyncpg-style command tag


class FakePool:
    """Pool wrapper that hands out a single FakeConn via an async
    context manager. One pool per test == one FakeConn whose state
    can be inspected after the call."""
    def __init__(self, conn: FakeConn):
        self.conn = conn
        self.closed = False

    @asynccontextmanager
    async def acquire(self):
        yield self.conn

    async def close(self):
        self.closed = True


@pytest.fixture
def fake_pool_existing(monkeypatch):
    """Yield a (pool, conn) pair where the row WILL be found by
    fetchrow. Tests configure the existing row's source/edge by mutating
    `conn.seeded_row` before calling upsert_observation."""
    conn = FakeConn(seeded_row=None)
    pool = FakePool(conn)
    monkeypatch.setattr(db, "_pool", pool)
    yield pool, conn


@pytest.fixture
def fake_pool_empty(monkeypatch):
    """Pool whose fetchrow always returns None -- "no existing row" case."""
    conn = FakeConn(seeded_row=None)
    pool = FakePool(conn)
    monkeypatch.setattr(db, "_pool", pool)
    yield pool, conn
