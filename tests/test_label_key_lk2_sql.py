"""
The lk2 SQL functions, checked against the golden vectors.

schema/migrations/20261117000000_label_key_lk2_function.sql defines the lk2
compile entry points (ADR-0055 sections 2a and 2b) as pure SQL functions on
jsonb: lk2_policy_ok, lk2_life_ok, lk2_compile and label_compile. The Python
reference (openddil_labels.lk2) lives in the contracts repo; the two agree
because both are tested against one vector table, not because one was ported
from the other.

Vectors: tests/vectors/lk2.json is a byte-identical copy of the contracts
repo's lib/python/openddil_labels/vectors/lk2.json at 3a6a204, pinned here by
sha256. Its 47 vectors, 11 policy_id cases and 4 declarations are the contract.

What runs where:
  * The structural tests need nothing: the vector pin and shape, and the
    migration's text (four public functions and their helpers, IMMUTABLE,
    the version guard, its own transaction, no DISTINCT inside string_agg,
    sorts after the lk1 migration).
  * The postgres tests run only when OPENDDIL_TEST_PG_DSN is set and psql is
    on PATH; otherwise they skip, and the skip reason names which is missing.
    They run the lk1 migration and then the lk2 migration (each without its
    own BEGIN;/COMMIT; lines) and the queries inside BEGIN ... ROLLBACK, so
    nothing is left in the database.

The garbage table (GARBAGE) and the life table (LIFE_CASES) hold literal
expectations. Where the Python reference can express the case (a valid
Declaration with garbage dims or life) the expectation is what
openddil_labels.lk2.compile_label returned when the table was written.

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
MIGRATIONS = REPO_ROOT / "schema" / "migrations"
LK1_MIGRATION = MIGRATIONS / "20261115000000_label_key_lk1_function.sql"
MIGRATION = MIGRATIONS / "20261117000000_label_key_lk2_function.sql"
VECTORS = Path(__file__).parent / "vectors" / "lk2.json"
LK1_VECTORS = Path(__file__).parent / "vectors" / "lk1.json"
VECTORS_SHA256 = "46282ff48e733c1a4f3158539cae977943cb9b32f7f578f4f41d203cc75f5ed7"
PUBLIC_FUNCTIONS = {"lk2_policy_ok", "lk2_life_ok", "lk2_compile", "label_compile"}
VECTOR_KEYS = {
    "name", "declaration", "dims", "life", "form", "canonical", "label_key",
    "label", "no_key", "no_key_dimension",
}
NULL_MARK = "<NULL>"


class _SqlNull:
    def __repr__(self) -> str:
        return "SQL_NULL"


SQL_NULL = _SqlNull()


def _data() -> dict:
    return json.loads(VECTORS.read_text(encoding="utf-8"))


def _vectors() -> list[dict]:
    return _data()["vectors"]


def _migration_text() -> str:
    return MIGRATION.read_text(encoding="utf-8")


def _body(path: Path) -> str:
    """A migration without its own BEGIN; and COMMIT; lines.

    Each file carries its own transaction (Atlas applies with --tx-mode none).
    The postgres tests wrap them in BEGIN ... ROLLBACK so nothing is left
    behind; a file's COMMIT would end that transaction early, so the tests run
    it without those two lines. Its SET LOCAL lines stay and hold for the
    test's transaction.
    """
    lines = path.read_text(encoding="utf-8").split("\n")
    assert lines.count("BEGIN;") == 1 and lines.count("COMMIT;") == 1
    return "\n".join(x for x in lines if x not in ("BEGIN;", "COMMIT;"))


# ---------------------------------------------------------------------------
# Postgres-free tests
# ---------------------------------------------------------------------------

def test_vectors_sha256_pinned():
    assert hashlib.sha256(VECTORS.read_bytes()).hexdigest() == VECTORS_SHA256


def test_vectors_shape():
    data = _data()
    vectors = data["vectors"]
    assert len(vectors) == 47
    assert len({v["name"] for v in vectors}) == 47
    for v in vectors:
        assert set(v.keys()) >= VECTOR_KEYS, v.get("name")
    assert len(data["policy_ids"]) == 11
    assert len(data["declarations"]) == 4
    for v in vectors:
        assert v["declaration"] is None or v["declaration"] in data["declarations"], v["name"]


def test_migration_defines_the_public_functions_and_helpers():
    names = re.findall(r'CREATE OR REPLACE FUNCTION "public"\."([a-z0-9_]+)"', _migration_text())
    assert len(names) == len(set(names))
    public = {n for n in names if n in PUBLIC_FUNCTIONS}
    assert public == PUBLIC_FUNCTIONS
    helpers = set(names) - PUBLIC_FUNCTIONS
    assert helpers
    for h in helpers:
        assert re.fullmatch(r"lk2_[a-z_]+", h), h


def test_every_function_is_immutable_and_parallel_safe():
    text = _migration_text()
    names = re.findall(r'CREATE OR REPLACE FUNCTION "public"\."([a-z0-9_]+)"', text)
    for name in names:
        m = re.search(
            r'CREATE OR REPLACE FUNCTION "public"\."%s"\(.*?\)\s*RETURNS\s+[\w\[\]]+\s+'
            r"LANGUAGE\s+\w+\s+(\w+)\s+(PARALLEL SAFE)" % name,
            text,
            re.S,
        )
        assert m is not None, name
        assert m.group(1) == "IMMUTABLE", name


def test_every_public_function_has_a_comment():
    text = _migration_text()
    for name in PUBLIC_FUNCTIONS:
        m = re.search(r'COMMENT ON FUNCTION "public"\."%s"\([^)]*\)\s+IS \'([^\']*)\'' % name, text)
        assert m is not None, name
        assert "ADR-0055" in m.group(1), name


def test_version_guard_present():
    assert "110000" in _migration_text()


def test_own_transaction_with_local_timeouts():
    # Atlas applies with --tx-mode none, so the file is its own transaction:
    # BEGIN, then both timeouts as SET LOCAL, before any other statement;
    # COMMIT as the last statement. squawk: require-lock-timeout,
    # require-statement-timeout.
    statements = [
        x for x in _migration_text().split("\n")
        if x.strip() and not x.startswith("--")
    ]
    assert statements[0] == "BEGIN;"
    assert re.fullmatch(r"SET LOCAL lock_timeout = '\d+m?s';", statements[1])
    assert re.fullmatch(r"SET LOCAL statement_timeout = '\d+m?s';", statements[2])
    assert statements[-1] == "COMMIT;"
    assert _migration_text().split("\n").count("BEGIN;") == 1
    assert _migration_text().split("\n").count("COMMIT;") == 1


def test_no_distinct_inside_string_agg():
    assert "string_agg(DISTINCT" not in _migration_text()


def test_no_exception_block():
    # A plpgsql EXCEPTION block starts a subtransaction, which makes the
    # function parallel unsafe; the functions guard their inputs instead.
    assert not re.search(r"^\s*EXCEPTION\b", _migration_text(), re.M)


def test_sorts_after_the_lk1_migration():
    assert LK1_MIGRATION.exists()
    assert LK1_MIGRATION.name < MIGRATION.name
    assert [p.name for p in sorted(MIGRATIONS.glob("*.sql"))].index(MIGRATION.name) > \
        [p.name for p in sorted(MIGRATIONS.glob("*.sql"))].index(LK1_MIGRATION.name)


# ---------------------------------------------------------------------------
# Tables of cases
# ---------------------------------------------------------------------------

_COMP = {"nation": {"author": ["BDR", "ATL"], "release": ["CRD"]}}
_VALID_DECL = {
    "policy_id": "urn:oid:1.3.6.1.4.1.32473.1",
    "dimensions": [
        {
            "name": "nation", "type": "permissive", "vocabulary": ["ATL", "BDR", "CRD"],
            "owner": "nation-steward", "has_author": True,
        }
    ],
}
_RIGHTS_DECL = {
    "policy_id": "urn:oid:1.3.6.1.4.1.32473.2",
    "dimensions": [
        _VALID_DECL["dimensions"][0],
        {
            "name": "data_rights_holder", "type": "restrictive", "vocabulary": ["H1", "H2"],
            "owner": "rights-steward", "has_author": False,
        },
    ],
}
_NOT_LK2 = ("declaration_not_lk2", None)
_LIFE_OK_TIMES = {"CreationDateTime": "2026-11-16T09:30:00Z", "ReviewDateTime": None}


def _life(creation: str, review=None) -> dict:
    return {"CreationDateTime": creation, "ReviewDateTime": review}


def _decl(**changes) -> dict:
    d = json.loads(json.dumps(_VALID_DECL))
    d.update(changes)
    return d


# name, declaration (a JSON value, or SQL_NULL), dims, life, (reason, dimension), py.
# py names the declaration in lk2.json's declarations for the cases the Python
# reference can express (a valid Declaration with garbage dims or life): the
# expectation is what compile_label returned for them. The declaration garbage
# cases cannot be expressed in Python (Declaration refuses to build) and are the
# SQL contract: declaration_not_lk2, no dimension.
GARBAGE = [
    ("decl_empty_object", {}, _COMP, None, _NOT_LK2, None),
    ("decl_array", [], _COMP, None, _NOT_LK2, None),
    ("decl_string", "x", _COMP, None, _NOT_LK2, None),
    ("decl_number", 5, _COMP, None, _NOT_LK2, None),
    ("decl_policy_fails_policy_re", _decl(policy_id="Bad Policy"), _COMP, None, _NOT_LK2, None),
    ("decl_policy_not_string", _decl(policy_id=7), _COMP, None, _NOT_LK2, None),
    ("decl_dimensions_empty", _decl(dimensions=[]), _COMP, None, _NOT_LK2, None),
    ("decl_dimensions_scalar", _decl(dimensions=5), _COMP, None, _NOT_LK2, None),
    ("decl_dimension_without_type", _decl(dimensions=[{"name": "nation", "vocabulary": ["ATL"]}]),
     _COMP, None, _NOT_LK2, None),
    ("decl_vocabulary_not_array", _decl(dimensions=[{"name": "nation", "type": "permissive",
                                                      "vocabulary": "ATL", "has_author": True}]),
     _COMP, None, _NOT_LK2, None),
    ("decl_vocabulary_number", _decl(dimensions=[{"name": "nation", "type": "permissive",
                                                   "vocabulary": [1], "has_author": True}]),
     _COMP, None, _NOT_LK2, None),
    ("decl_has_author_absent", _decl(dimensions=[{"name": "nation", "type": "permissive",
                                                   "vocabulary": ["ATL"]}]),
     _COMP, None, _NOT_LK2, None),
    ("decl_has_author_null", _decl(dimensions=[{"name": "nation", "type": "permissive",
                                                 "vocabulary": ["ATL"], "has_author": None}]),
     _COMP, None, _NOT_LK2, None),
    ("decl_has_author_number", _decl(dimensions=[{"name": "nation", "type": "permissive",
                                                   "vocabulary": ["ATL"], "has_author": 1}]),
     _COMP, None, _NOT_LK2, None),
    ("decl_has_author_string", _decl(dimensions=[{"name": "nation", "type": "permissive",
                                                   "vocabulary": ["ATL"], "has_author": "true"}]),
     _COMP, None, _NOT_LK2, None),
    ("decl_has_author_true_on_restrictive",
     _decl(dimensions=_VALID_DECL["dimensions"] + [{"name": "holder", "type": "restrictive",
                                                     "vocabulary": ["H1"], "has_author": True}]),
     _COMP, None, _NOT_LK2, None),
    ("dims_empty_array", _VALID_DECL, [], None, ("value_malformed", None), "nation_only"),
    ("dims_string", _VALID_DECL, "x", None, ("value_malformed", None), "nation_only"),
    ("dims_json_null", _VALID_DECL, None, None, ("value_malformed", None), "nation_only"),
    ("dims_sql_null", _VALID_DECL, SQL_NULL, None, ("value_malformed", None), None),
    ("life_empty_array", _VALID_DECL, _COMP, [], ("life_malformed", None), "nation_only"),
    ("life_number", _VALID_DECL, _COMP, 5, ("life_malformed", None), "nation_only"),
    ("life_empty_object", _VALID_DECL, _COMP, {}, ("life_malformed", None), "nation_only"),
    ("life_leap_second", _VALID_DECL, _COMP, _life("2026-06-30T23:59:60Z"),
     ("life_malformed", None), "nation_only"),
    ("life_year_0000", _VALID_DECL, _COMP, _life("0000-01-01T00:00:00Z"),
     ("life_malformed", None), "nation_only"),
    ("life_review_not_a_date", _VALID_DECL, _COMP,
     _life("2026-11-16T09:30:00Z", "2027-02-29T00:00:00Z"), ("life_malformed", None), "nation_only"),
    ("nation_author_object", _VALID_DECL, {"nation": {"author": {}, "release": ["CRD"]}}, None,
     ("value_malformed", "nation"), "nation_only"),
    ("nation_is_string", _VALID_DECL, {"nation": "ATL"}, None,
     ("value_malformed", "nation"), "nation_only"),
    ("nation_is_null", _VALID_DECL, {"nation": None}, None,
     ("dimension_missing", "nation"), "nation_only"),
    ("nation_release_string", _VALID_DECL,
     {"nation": {"author": ["ATL", "BDR"], "release": "CRD"}}, None,
     ("value_malformed", "nation"), "nation_only"),
    ("nation_author_number", _VALID_DECL, {"nation": {"author": 5, "release": None}}, None,
     ("value_malformed", "nation"), "nation_only"),
    ("nation_unknown_member", _VALID_DECL,
     {"nation": {"rollup": True, "release": ["ATL"], "x": 1}}, None,
     ("value_malformed", "nation"), "nation_only"),
    ("set_value_is_number", _RIGHTS_DECL,
     {"nation": {"author": "ATL", "release": ["BDR"]}, "data_rights_holder": 5}, None,
     ("value_malformed", "data_rights_holder"), "nation_rights"),
    ("undeclared_dimension", _RIGHTS_DECL,
     {"nation": {"author": "ATL", "release": None}, "zzz": 1}, None,
     ("dimension_undeclared", "zzz"), "nation_rights"),
]

# creation (a string, or the whole life value), review, expected lk2_life_ok.
# Each entry whose life is a Python-expressible value was checked against
# openddil_labels.lk2._life_ok when the table was written.
LIFE_CASES = [
    (_life("2026-11-16T09:30:00Z"), True),
    (_life("2026-11-16T10:45:12.250Z", "2027-11-16T10:45:12.250Z"), True),
    (_life("0001-01-01T00:00:00Z"), True),
    (_life("9999-12-31T23:59:59Z"), True),
    (_life("2028-02-29T00:00:00Z"), True),
    (_life("2000-02-29T00:00:00Z"), True),
    (_life("1900-02-29T00:00:00Z"), False),
    (_life("2100-02-29T00:00:00Z"), False),
    (_life("2026-02-29T00:00:00Z"), False),
    (_life("2026-04-31T00:00:00Z"), False),
    (_life("2026-06-30T23:59:60Z"), False),
    (_life("0000-01-01T00:00:00Z"), False),
    (_life("2026-13-01T00:00:00Z"), False),
    (_life("2026-00-10T00:00:00Z"), False),
    (_life("2026-11-00T00:00:00Z"), False),
    (_life("2026-11-16T24:00:00Z"), False),
    (_life("2026-11-16T09:60:00Z"), False),
    (_life("2026-11-16T09:30:00"), False),
    (_life("2026-11-16T09:30:00z"), False),
    (_life("2026-11-16T09:30:00.Z"), False),
    (_life("2026-11-16T09:30:00+00:00"), False),
    (_life("2026-11-16 09:30:00Z"), False),
    (_life("2026-11-16T09:30:00Z\n"), False),
    (_life("2026-11-16T09:30:00Z", "never"), False),
    (_life("2026-11-16T09:30:00Z", 5), False),
    (_life(5), False),
    (_life(None), False),
    ({"CreationDateTime": "2026-11-16T09:30:00Z"}, False),
    ({"ReviewDateTime": None}, False),
    (dict(_life("2026-11-16T09:30:00Z"), Extra="x"), False),
    ({}, False),
    ([], False),
    ("x", False),
    (5, False),
    (None, False),
]


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


def _jlit(value) -> str:
    """A jsonb literal; SQL_NULL is SQL NULL, None is JSON null."""
    if value is SQL_NULL:
        return "NULL::jsonb"
    return _lit(json.dumps(value, sort_keys=True)) + "::jsonb"


def _arr(values: list | None) -> str:
    if values is None:
        return "NULL::text[]"
    if not values:
        return "ARRAY[]::text[]"
    return "ARRAY[" + ", ".join(_lit(x) for x in values) + "]::text[]"


def _decl_of(v: dict):
    return SQL_NULL if v["declaration"] is None else _data()["declarations"][v["declaration"]]


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
            timeout=120,
        )
    finally:
        os.unlink(path)


def _marked(expr: str) -> str:
    return "coalesce((%s)::text, '%s')" % (expr, NULL_MARK)


def _run_queries(queries: list[tuple[str, str]]) -> dict[str, str | None]:
    """Run (tag, expression) pairs after both migrations; tag -> text, None for SQL NULL."""
    lines = [
        "\\set ON_ERROR_STOP 1",
        "BEGIN;",
        _body(LK1_MIGRATION).rstrip("\n"),
        _body(MIGRATION).rstrip("\n"),
        "",
    ]
    for tag, expr in queries:
        lines.append("SELECT %s, %s;" % (_lit(tag), _marked(expr)))
    lines.append("ROLLBACK;")
    proc = _run_psql("\n".join(lines) + "\n")
    assert proc.returncode == 0, proc.stderr
    rows: dict[str, str | None] = {}
    for line in proc.stdout.splitlines():
        if not line:
            continue
        tag, _, value = line.partition("\t")
        rows[tag] = None if value == NULL_MARK else value
    assert len(rows) == len(queries)
    return rows


def _compile(fn: str, decl, dims, life) -> str:
    return '"public"."%s"(%s, %s, %s)' % (fn, _jlit(decl), _jlit(dims), _jlit(life))


def _expected(v: dict) -> dict:
    if v["no_key"] is not None:
        return {"no_key": v["no_key"], "no_key_dimension": v["no_key_dimension"]}
    return {
        "form": v["form"], "canonical": v["canonical"],
        "label_key": v["label_key"], "label": v["label"],
    }


def _lk2_expected(v: dict) -> dict:
    """What lk2_compile gives for a vector: the lk1 form is refused there."""
    if v["form"] != "lk1":
        return _expected(v)
    urn = re.fullmatch(
        r"urn:oid:[0-2](\.(0|[1-9][0-9]*))+", _data()["declarations"][v["declaration"]]["policy_id"]
    )
    if urn:
        return {"no_key": "form_is_lk1", "no_key_dimension": "nation"}
    return {"no_key": "policy_id_not_urn_oid", "no_key_dimension": None}


@requires_pg
def test_sql_label_compile_matches_every_vector():
    vectors = _vectors()
    rows = _run_queries(
        [(v["name"], _compile("label_compile", _decl_of(v), v["dims"], v["life"])) for v in vectors]
    )
    for v in vectors:
        assert rows[v["name"]] is not None, v["name"]
        assert json.loads(rows[v["name"]]) == _expected(v), v["name"]


@requires_pg
def test_sql_lk2_compile_matches_every_vector():
    vectors = _vectors()
    rows = _run_queries(
        [(v["name"], _compile("lk2_compile", _decl_of(v), v["dims"], v["life"])) for v in vectors]
    )
    for v in vectors:
        assert rows[v["name"]] is not None, v["name"]
        assert json.loads(rows[v["name"]]) == _lk2_expected(v), v["name"]


@requires_pg
def test_sql_policy_ok_matches_every_policy_id_case():
    cases = _data()["policy_ids"]
    queries = [
        ("p%d" % i, '"public"."lk2_policy_ok"(%s)' % _jlit(c["policy_id"])) for i, c in enumerate(cases)
    ]
    queries.append(("p_sql_null", '"public"."lk2_policy_ok"(NULL::jsonb)'))
    rows = _run_queries(queries)
    for i, c in enumerate(cases):
        # _marked casts with ::text, and boolean::text is 'true'/'false' (not psql's t/f).
        assert rows["p%d" % i] == ("true" if c["accepted"] else "false"), c["policy_id"]
    assert rows["p_sql_null"] == "false"


@requires_pg
def test_sql_life_ok_table():
    queries = [("l%d" % i, '"public"."lk2_life_ok"(%s)' % _jlit(life)) for i, (life, _) in enumerate(LIFE_CASES)]
    queries.append(("l_sql_null", '"public"."lk2_life_ok"(NULL::jsonb)'))
    rows = _run_queries(queries)
    for i, (life, want) in enumerate(LIFE_CASES):
        assert rows["l%d" % i] == ("true" if want else "false"), life
    assert rows["l_sql_null"] == "false"


@requires_pg
def test_sql_properties():
    vectors = {v["name"]: v for v in _vectors()}
    queries = []
    for v in vectors.values():
        queries.append(("lc:" + v["name"], _compile("label_compile", _decl_of(v), v["dims"], v["life"])))
    lk1_forms = [v for v in vectors.values() if v["form"] == "lk1"]
    for v in lk1_forms:
        nation = v["label"]["dims"]["nation"]
        queries.append((
            "lk1:" + v["name"],
            '"public"."lk1_key"(%s, %s, %s)' % (_lit(v["label"]["policy"]), _lit(nation["author"]), _arr(nation["release"])),
        ))
    rows = _run_queries(queries)
    got = {n: json.loads(rows["lc:" + n]) for n in vectors}

    # life never enters a key: two lives, one key.
    assert got["life_a"]["label_key"] == got["life_b"]["label_key"] == got["composite_release"]["label_key"]
    assert got["life_a"]["label"]["life"] != got["life_b"]["label"]["life"]

    # An lk1 label keeps the key lk1_key gives, life or not.
    assert lk1_forms
    for v in lk1_forms:
        assert got[v["name"]]["form"] == "lk1", v["name"]
        assert got[v["name"]]["label_key"] == rows["lk1:" + v["name"]], v["name"]
        assert got[v["name"]]["label_key"].startswith("lk1:"), v["name"]

    # The lk1 legacy-policy key with life is the key lk1.json fixes for atl_bdr.
    lk1_atl_bdr = next(x for x in json.loads(LK1_VECTORS.read_text(encoding="utf-8")) if x["name"] == "atl_bdr")
    assert got["life_on_lk1_legacy_policy"]["label_key"] == lk1_atl_bdr["label_key"]
    assert got["life_on_lk1_legacy_policy"]["label"]["life"] == {
        "CreationDateTime": vectors["life_on_lk1_legacy_policy"]["life"]["CreationDateTime"],
        "ReviewDateTime": vectors["life_on_lk1_legacy_policy"]["life"]["ReviewDateTime"],
    }

    # No canonical string carries a life time.
    with_life = [v for v in vectors.values() if v["life"] is not None]
    assert with_life
    for v in with_life:
        out = got[v["name"]]
        if "canonical" not in out:
            continue
        for member in ("CreationDateTime", "ReviewDateTime"):
            t = v["life"].get(member)
            if isinstance(t, str):
                assert t not in out["canonical"], v["name"]


@requires_pg
def test_sql_garbage_inputs_never_raise():
    assert len(GARBAGE) >= 12
    queries = []
    for name, decl, dims, life, _, _py in GARBAGE:
        for fn in ("label_compile", "lk2_compile"):
            queries.append((fn + ":" + name, _compile(fn, decl, dims, life)))
    rows = _run_queries(queries)
    for name, _decl_, _dims, _life_, (reason, dim), _py in GARBAGE:
        for fn in ("label_compile", "lk2_compile"):
            tag = fn + ":" + name
            assert rows[tag] is not None, tag
            assert json.loads(rows[tag]) == {"no_key": reason, "no_key_dimension": dim}, tag


@requires_pg
def test_sql_null_and_json_null_mean_absent():
    comp = {"nation": {"author": ["BDR", "ATL"], "release": ["CRD"]}}
    queries = []
    for fn in ("label_compile", "lk2_compile"):
        queries.append((fn + ":decl_sql_null", _compile(fn, SQL_NULL, comp, None)))
        queries.append((fn + ":decl_json_null", _compile(fn, None, comp, None)))
        queries.append((fn + ":life_sql_null", _compile(fn, _VALID_DECL, comp, SQL_NULL)))
        queries.append((fn + ":life_json_null", _compile(fn, _VALID_DECL, comp, None)))
        queries.append((fn + ":life_absent_vs_a", _compile(fn, _VALID_DECL, comp, _LIFE_OK_TIMES)))
    rows = _run_queries(queries)
    for fn in ("label_compile", "lk2_compile"):
        for tag in ("decl_sql_null", "decl_json_null"):
            assert json.loads(rows[fn + ":" + tag]) == {"no_key": "no_policy", "no_key_dimension": None}, (fn, tag)
        a = json.loads(rows[fn + ":life_sql_null"])
        b = json.loads(rows[fn + ":life_json_null"])
        c = json.loads(rows[fn + ":life_absent_vs_a"])
        assert a == b and a["form"] == "lk2" and "life" not in a["label"], fn
        assert c["label_key"] == a["label_key"] and c["label"]["life"] == _LIFE_OK_TIMES, fn
