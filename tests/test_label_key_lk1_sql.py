"""
The lk1 SQL functions, checked against the golden vectors.

schema/migrations/20261115000000_label_key_lk1_function.sql defines the lk1
canonical form and key (DESIGN-2026-11-06 section 1.2) as pure SQL functions.
The Python reference (openddil_labels.lk1) lives in the contracts repo; the
two agree because both are tested against one vector table, not because one
was ported from the other.

Vectors: tests/vectors/lk1.json is a byte-identical copy of the contracts
repo's lib/openddil-labels vectors (lib/python/openddil_labels/vectors/lk1.json
at 0552b81), pinned here by sha256.

What runs where:
  * The structural tests need nothing: the vector pin and shape, and the
    migration's text (four functions, IMMUTABLE, the version guard, no
    DISTINCT inside string_agg).
  * The postgres tests run only when OPENDDIL_TEST_PG_DSN is set and psql is
    on PATH; otherwise they skip, and the skip reason names which is missing.
    They run the migration and the vector queries inside BEGIN ... ROLLBACK,
    so nothing is left in the database.

Standard library plus pytest only.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import subprocess
import tempfile
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).parent.parent
MIGRATION = REPO_ROOT / "schema" / "migrations" / "20261115000000_label_key_lk1_function.sql"
VECTORS = Path(__file__).parent / "vectors" / "lk1.json"
VECTORS_SHA256 = "f7d8075fb8fd674bad470b737d990c4566dd8d170ffbd3fd86434d300cd5db48"
VECTOR_KEYS = {
    "name", "policy_id", "originator_nation", "releasable_to",
    "canonical", "label_key", "label",
}
NULL_MARK = "<NULL>"


def _vectors() -> list[dict]:
    return json.loads(VECTORS.read_text(encoding="utf-8"))


def _migration_text() -> str:
    return MIGRATION.read_text(encoding="utf-8")


# ---------------------------------------------------------------------------
# Postgres-free tests
# ---------------------------------------------------------------------------

def test_vectors_sha256_pinned():
    assert hashlib.sha256(VECTORS.read_bytes()).hexdigest() == VECTORS_SHA256


def test_vectors_shape():
    vectors = _vectors()
    assert len(vectors) == 18
    assert len({v["name"] for v in vectors}) == 18
    for v in vectors:
        assert set(v.keys()) == VECTOR_KEYS, v.get("name")


def test_migration_defines_four_functions():
    names = set(re.findall(r'CREATE OR REPLACE FUNCTION "public"\."(lk1_[a-z_]+)"', _migration_text()))
    assert names == {"lk1_canonical", "lk1_key", "lk1_label", "lk1_sorted_release"}


def test_public_functions_are_immutable():
    text = _migration_text()
    for name in ("lk1_canonical", "lk1_key", "lk1_label"):
        m = re.search(
            r'CREATE OR REPLACE FUNCTION "public"\."%s"\(.*?\)\s*RETURNS\s+\w+\s+LANGUAGE\s+\w+\s+(\w+)' % name,
            text,
            re.S,
        )
        assert m is not None, name
        assert m.group(1) == "IMMUTABLE", name


def test_version_guard_present():
    assert "110000" in _migration_text()


def test_no_distinct_inside_string_agg():
    assert "string_agg(DISTINCT" not in _migration_text()


# ---------------------------------------------------------------------------
# Postgres tests
# ---------------------------------------------------------------------------

def _pg_skip_reason() -> str | None:
    if not os.environ.get("OPENDDIL_TEST_PG_DSN"):
        return "OPENDDIL_TEST_PG_DSN is not set"
    if shutil.which("psql") is None:
        return "psql is not on PATH"
    return None


requires_pg = pytest.mark.skipif(
    _pg_skip_reason() is not None,
    reason=_pg_skip_reason() or "",
)


def _lit(value: str | None) -> str:
    if value is None:
        return "NULL"
    return "'" + value.replace("'", "''") + "'"


def _arr(values: list | None) -> str:
    if values is None:
        return "NULL::text[]"
    if not values:
        return "ARRAY[]::text[]"
    return "ARRAY[" + ", ".join(_lit(x) for x in values) + "]::text[]"


def _args(v: dict) -> str:
    return "%s, %s, %s" % (_lit(v["policy_id"]), _lit(v["originator_nation"]), _arr(v["releasable_to"]))


def _run_psql(script: str) -> subprocess.CompletedProcess:
    dsn = os.environ["OPENDDIL_TEST_PG_DSN"]
    fd, path = tempfile.mkstemp(suffix=".sql", text=False)
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(script.encode("utf-8"))
        return subprocess.run(
            ["psql", "-X", "-q", "-A", "-t", "-F", "\t", "-d", dsn, "-f", path],
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=60,
        )
    finally:
        os.unlink(path)


def _marked(expr: str) -> str:
    return "coalesce((%s)::text, '%s')" % (expr, NULL_MARK)


@requires_pg
def test_sql_matches_vectors_and_properties():
    lines = ["\\set ON_ERROR_STOP 1", "BEGIN;", _migration_text().rstrip("\n"), ""]
    vectors = _vectors()
    for v in vectors:
        a = _args(v)
        lines.append(
            "SELECT %s, %s, %s, %s;"
            % (
                _lit(v["name"]),
                _marked('"public"."lk1_canonical"(%s)' % a),
                _marked('"public"."lk1_key"(%s)' % a),
                _marked('"public"."lk1_label"(%s)' % a),
            )
        )
    p = "'example:policy-1'"
    lines.append(
        "SELECT 'prop_order_dupes', \"public\".\"lk1_key\"(%s, 'ATL', ARRAY['BDR', 'ATL']) "
        "= \"public\".\"lk1_key\"(%s, 'ATL', ARRAY['ATL', 'BDR', 'ATL']);" % (p, p)
    )
    lines.append(
        "SELECT 'prop_empty_vs_absent', \"public\".\"lk1_key\"(%s, 'ATL', ARRAY[]::text[]) "
        "IS DISTINCT FROM \"public\".\"lk1_key\"(%s, 'ATL', NULL);" % (p, p)
    )
    lines.append("ROLLBACK;")
    proc = _run_psql("\n".join(lines) + "\n")
    assert proc.returncode == 0, proc.stderr

    rows = {}
    for line in proc.stdout.splitlines():
        if not line:
            continue
        parts = line.split("\t")
        rows[parts[0]] = parts[1:]

    for v in vectors:
        got = rows[v["name"]]
        assert len(got) == 3, v["name"]
        canonical, key, label = (None if x == NULL_MARK else x for x in got)
        assert canonical == v["canonical"], v["name"]
        assert key == v["label_key"], v["name"]
        parsed = None if label is None else json.loads(label)
        assert parsed == v["label"], v["name"]

    assert rows["prop_order_dupes"] == ["t"]
    assert rows["prop_empty_vs_absent"] == ["t"]


@requires_pg
def test_sql_invalid_policy_id_raises():
    script = "\n".join(
        [
            "\\set ON_ERROR_STOP 1",
            "BEGIN;",
            _migration_text().rstrip("\n"),
            "SELECT \"public\".\"lk1_canonical\"('Bad Policy', 'ATL', NULL);",
            "ROLLBACK;",
            "",
        ]
    )
    proc = _run_psql(script)
    assert proc.returncode != 0
    assert "lk1: invalid policy_id" in proc.stderr
