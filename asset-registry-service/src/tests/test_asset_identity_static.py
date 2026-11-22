"""Asset identity checks that need no database (DESIGN-2026-11-08 section 5).

The tests that need postgres are in test_asset_identity_store.py. Here: the minting
shape, the lookup-key vectors, the single writer of identity_equivalence, the tier
refusal, bad records, and the text of the migration.
"""
from __future__ import annotations

import ast
import asyncio
import logging
import re
import uuid
from contextlib import asynccontextmanager

import pytest

from asset_registry_service import config, identity_store, main as main_module
from asset_registry_service.vendor.openddil_identity import Identity, lookup_key, mint_identity

from .identity_support import MIGRATIONS, REPO_ROOT, SEEDED_SCHEMES, SIX_TABLES

MIGRATION = MIGRATIONS / "20261122000000_asset_identity.sql"

# The migration's four scheme namespaces, and the lookup keys that follow from them. The keys
# are written out below so a change to either side is a visible diff, then checked against
# uuid.uuid5 (and, in the store tests, against uuid_generate_v5 in postgres).
NS_ASSET_ID = "b9e2aa09-1434-4a82-b59e-f149e9a3f5d2"
NS_SERIAL = "3e77136b-26ce-4947-85b8-6ab6fc2012d5"
NS_IDENTITY = "e0bc965c-1ab8-4c0f-9c7b-8f4f546c0c5e"
NS_UII = "45c19c89-c3b1-40ef-81c8-85f16302c4dd"
VECTOR_INPUTS = [
    (NS_ASSET_ID, "urn:test:asset-0001"),
    (NS_SERIAL, "SN-TEST-0001"),
    (NS_UII, "UII-TEST-0001"),
    (NS_IDENTITY, "urn:uuid:01a1e284-45a5-7101-a8c8-9882e4b3e92d"),
    (NS_SERIAL, "SN TEST 0001 (with spaces)"),
    (NS_UII, "UII-TÉST-0001"),
    (NS_SERIAL, "sn-test-0001"),  # case is not folded
]
LOOKUP_VECTORS = [(ns, v, str(uuid.uuid5(uuid.UUID(ns), v))) for ns, v in VECTOR_INPUTS]


def test_uuid7_shape():
    ids = [mint_identity().value for _ in range(10_000)]
    for u in ids:
        assert u.version == 7
        assert u.variant == uuid.RFC_4122
        assert (u.int >> 76) & 0xF == 7
        assert (u.int >> 62) & 0x3 == 0b10
    assert all(a < b for a, b in zip(ids, ids[1:])), "mint order is sort order within one process"
    assert len(set(ids)) == len(ids)


def test_lookup_key_vectors_python_half():
    for ns, value, key in LOOKUP_VECTORS:
        assert str(uuid.uuid5(uuid.UUID(ns), value)) == key
        assert str(lookup_key(uuid.UUID(ns), value).value) == key
    # a lookup key is a version 5 uuid, never an identity (version 7)
    assert {uuid.UUID(k).version for _, _, k in LOOKUP_VECTORS} == {5}
    with pytest.raises(ValueError):
        Identity(uuid.UUID(LOOKUP_VECTORS[0][2]))


def test_migration_namespaces_are_fixed_literals_not_derived():
    text = MIGRATION.read_text(encoding="utf-8")
    rows = re.findall(
        r"\('(\w+)', '[^']*', '[^']*', '([0-9a-f-]{36})', now\(\), 'migration 20261122000000'\)", text
    )
    assert tuple(r[0] for r in rows) == SEEDED_SCHEMES
    spaces = {r[0]: uuid.UUID(r[1]) for r in rows}
    assert spaces == {
        "asset_id": uuid.UUID(NS_ASSET_ID), "serial": uuid.UUID(NS_SERIAL),
        "identity": uuid.UUID(NS_IDENTITY), "uii": uuid.UUID(NS_UII),
    }
    assert len(set(spaces.values())) == 4
    std = (uuid.NAMESPACE_DNS, uuid.NAMESPACE_URL, uuid.NAMESPACE_OID, uuid.NAMESPACE_X500)
    for name, ns in spaces.items():
        assert ns.version == 4, "generated once with uuid4"
        assert all(ns != uuid.uuid5(s, name) for s in std), "never derived from the name"


def test_migration_text_fence():
    text = MIGRATION.read_text(encoding="utf-8")
    lines = text.split("\n")
    statements = [x for x in lines if x.strip() and not x.startswith("--")]
    assert statements[0] == "BEGIN;"
    assert "SET LOCAL lock_timeout = '5s';" in statements[1:3]
    assert statements[-1] == "COMMIT;"
    for t in SIX_TABLES:
        assert f'CREATE TABLE "public"."{t}"' in text
    code = "\n".join(statements)
    assert "originator_nation" not in code and "releasable_to" not in code
    assert not re.search(r"create\s+extension|publication", code, re.I)
    assert "asset_id_era" in text


# ---- the single writer of identity_equivalence (ADR-0056 check 5) -----------------------

_INSERT = re.compile(r"INSERT\s+INTO\s+(?:\"?public\"?\.)?\"?identity_equivalence\b", re.I)


def writers_of(source: str) -> list[str]:
    """Names of the functions whose string constants hold an INSERT INTO identity_equivalence."""
    tree = ast.parse(source)
    for node in ast.walk(tree):
        for child in ast.iter_child_nodes(node):
            child.parent = node
    out = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and isinstance(node.value, str) and _INSERT.search(node.value):
            fn = node
            while fn is not None and not isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)):
                fn = getattr(fn, "parent", None)
            out.append(fn.name if fn else "<module>")
    return out


def _shipped_sources():
    for p in REPO_ROOT.rglob("*.py"):
        rel = p.relative_to(REPO_ROOT).parts
        if {".git", "tests", "vendor", "__pycache__", "node_modules"} & set(rel):
            continue
        yield p


class _StubConn:
    """Records writes; answers the one read declare_equivalence makes before it checks."""

    def __init__(self):
        self.written = []

    async def fetchval(self, sql, *args):
        return False

    async def execute(self, sql, *args):
        self.written.append(sql)

    @asynccontextmanager
    async def transaction(self):
        yield


class _StubPool:
    def __init__(self):
        self.conn = _StubConn()

    @asynccontextmanager
    async def acquire(self):
        yield self.conn


def test_equivalence_single_writer():
    found = []
    for p in _shipped_sources():
        for name in writers_of(p.read_text(encoding="utf-8", errors="replace")):
            found.append((p.name, name))
    assert found == [("identity_store.py", "declare_equivalence")], found
    # no SQL file writes it either
    for p in list((REPO_ROOT / "schema").rglob("*.sql")) + list((REPO_ROOT / "electric").rglob("*.sql")):
        assert not _INSERT.search(p.read_text(encoding="utf-8")), p
    # the scanner sees a planted second writer (the plant lives here, never in the package)
    assert writers_of('def sneaky():\n    return "insert into identity_equivalence (a) values (1)"\n') == ["sneaky"]

    # an empty owner is refused before anything is written
    a, b = mint_identity().value, mint_identity().value
    for owner, reason in (("", "same unit"), ("   ", "same unit"), ("registry owner", "")):
        pool = _StubPool()
        with pytest.raises(identity_store.EquivalenceRefused) as exc:
            asyncio.run(identity_store.declare_equivalence(a, b, owner, reason, "d" * 64, pool=pool))
        assert exc.value.reason in ("owner_missing", "reason_missing")
        assert pool.conn.written == []
    with pytest.raises(identity_store.EquivalenceRefused) as exc:
        asyncio.run(identity_store.declare_equivalence(a, a, "owner", "reason", "d" * 64, pool=_StubPool()))
    assert exc.value.reason == "survivor_is_merged"


# ---- the tier is required ---------------------------------------------------------------

def test_refusal_without_asset_registry_tier(monkeypatch, caplog):
    monkeypatch.delenv("ASSET_REGISTRY_TIER", raising=False)
    monkeypatch.setenv("PGHOST", "fixture")
    for value in (None, "", "   "):
        if value is not None:
            monkeypatch.setenv("ASSET_REGISTRY_TIER", value)
        caplog.clear()
        with caplog.at_level(logging.ERROR), pytest.raises(SystemExit) as exc:
            config.tier_from_env()
        assert exc.value.code not in (0, None)
        assert "ASSET_REGISTRY_TIER" in caplog.text
    # the service entrypoint refuses before it reads anything else
    monkeypatch.delenv("ASSET_REGISTRY_TIER", raising=False)
    caplog.clear()
    with caplog.at_level(logging.ERROR), pytest.raises(SystemExit):
        main_module.main()
    assert "ASSET_REGISTRY_TIER" in caplog.text
    monkeypatch.setenv("ASSET_REGISTRY_TIER", " hq ")
    assert config.tier_from_env() == "hq"


def test_declaration_topic_default(monkeypatch):
    monkeypatch.delenv("ASSET_REGISTRY_DECLARATION_TOPIC", raising=False)
    monkeypatch.setenv("PGHOST", "fixture")
    assert config.Config.from_env().declaration_topic == "asset-declarations"
    monkeypatch.setenv("ASSET_REGISTRY_DECLARATION_TOPIC", "other-topic")
    assert config.Config.from_env().declaration_topic == "other-topic"


# ---- a bad record never raises ------------------------------------------------------------

class _NoStore:
    def acquire(self):
        raise AssertionError("a bad record must not reach the store")


@pytest.mark.parametrize("raw", [
    b"not json", b"", b"[]", b"42", b"null", b"{}", b'{"asset_id": ""}', b'{"asset_id": 7}',
    b'{"serial": "SN-TEST-0001"}', b"\xff\xfe",
])
def test_bad_record_is_skipped_not_raised(raw):
    assert asyncio.run(identity_store.handle_declaration(raw, tier="t", pool=_NoStore())) is None


def test_non_string_alias_is_skipped_not_raised():
    raw = b'{"asset_id": "urn:test:a", "serial": 12345}'

    class Conn:
        async def fetch(self, *a):
            return []

        async def fetchrow(self, *a):
            return None

        @asynccontextmanager
        async def transaction(self):
            yield

    class Pool:
        @asynccontextmanager
        async def acquire(self):
            yield Conn()

    assert asyncio.run(identity_store.handle_declaration(raw, tier="t", pool=Pool())) is None


# ---- no served identity table (grep half; the catalog half is in the store tests) ---------

def served_mentions() -> list:
    """Files in schema/ and electric/ that mention a publication and one of the six tables."""
    hits = []
    for base in ("schema", "electric"):
        for p in (REPO_ROOT / base).rglob("*"):
            if not p.is_file() or p.suffix not in (".sql", ".hcl", ".sh", ".yaml", ".yml"):
                continue
            if p.name == MIGRATION.name:
                continue
            raw = p.read_text(encoding="utf-8", errors="replace")
            text = "\n".join(re.sub(r"(--|#).*$", "", line) for line in raw.split("\n"))  # comments name things freely
            if not re.search(r"publication", text, re.I):
                continue
            for t in SIX_TABLES:
                if re.search(rf"\b{t}\b", text):
                    hits.append(f"{p.relative_to(REPO_ROOT)}: {t}")
    return hits


def test_no_publication_statement_names_an_identity_table():
    assert served_mentions() == []
