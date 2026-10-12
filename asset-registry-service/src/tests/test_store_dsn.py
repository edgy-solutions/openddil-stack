"""Store connection source: PG* environment, DSN only as compose fallback."""
from __future__ import annotations

import asyncio

import asyncpg
import pytest

from asset_registry_service import config, db

_VARS = ("ASSET_REGISTRY_POSTGRES_DSN", "PGHOST", "PGDATABASE", "PGUSER",
         "PGPASSWORD")


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    for k in _VARS:
        monkeypatch.delenv(k, raising=False)


def test_dsn_set_is_used(monkeypatch):
    monkeypatch.setenv("ASSET_REGISTRY_POSTGRES_DSN",
                       "postgres://u:pw-test-only@h:5432/d")
    monkeypatch.setenv("PGHOST", "ignored")
    assert config.store_dsn_from_env() == "postgres://u:pw-test-only@h:5432/d"


def test_pghost_only_gives_none_and_pool_gets_none(monkeypatch):
    monkeypatch.setenv("PGHOST", "db.internal")
    monkeypatch.setenv("PGPASSWORD", "pw-test-only")
    dsn = config.store_dsn_from_env()
    assert dsn is None

    seen = {}

    async def fake_create_pool(*args, **kwargs):
        seen.update(kwargs)
        return object()

    monkeypatch.setattr(asyncpg, "create_pool", fake_create_pool)
    monkeypatch.setattr(db, "_pool", None)
    asyncio.run(db.init_pool(dsn))
    assert seen["dsn"] is None
    monkeypatch.setattr(db, "_pool", None)


def test_neither_exits_nonzero():
    with pytest.raises(SystemExit) as exc:
        config.store_dsn_from_env()
    assert exc.value.code == 2


def test_log_names_source_never_password(monkeypatch, caplog):
    monkeypatch.setenv("PGHOST", "db.internal")
    monkeypatch.setenv("PGDATABASE", "openddil")
    monkeypatch.setenv("PGUSER", "svc")
    monkeypatch.setenv("PGPASSWORD", "pw-test-only")
    with caplog.at_level("INFO"):
        config.store_dsn_from_env()
    assert "host=db.internal db=openddil user=svc" in caplog.text
    assert "pw-test-only" not in caplog.text
