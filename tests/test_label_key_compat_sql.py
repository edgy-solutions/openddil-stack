"""
The label key compatibility store: label_policy, label_registry, label_key and
label on 17 tables, and the one trigger that stamps them.

schema/migrations/20261121000000_label_key_compat_columns.sql builds on the lk1
functions of 20261115000000 (DESIGN-2026-11-06 sections 1.1 and 1.3). The
backfill (scripts/backfill_label_key.py, section 1.4) is tested offline in
tests/test_backfill_label_key.py; its SQL text is run against postgres here.

What runs where:
  * The structural tests need nothing: the migration's text, schema.hcl, the
    atlas.sum entry.
  * The postgres tests run only when OPENDDIL_TEST_PG_DSN is set and psql is on
    PATH; otherwise they skip, and the skip reason names which is missing. Each
    runs the whole migration chain inside BEGIN ... ROLLBACK (the files' own
    BEGIN; and COMMIT; lines removed), so the database must hold none of the
    stack's tables to begin with, and nothing is left behind. CI gives each job
    a fresh database, and every test in this directory rolls back.

Fixtures use invented values only: nations ATL, BDR, CRD, policy ids
example:policy-1 and example:policy-2.

Standard library plus pytest only.
"""
from __future__ import annotations

import importlib.util
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
MIGRATION = MIGRATIONS / "20261121000000_label_key_compat_columns.sql"
LK2_MIGRATION = MIGRATIONS / "20261117000000_label_key_lk2_function.sql"
HCL = REPO_ROOT / "schema" / "schema.hcl"
VECTORS = Path(__file__).parent / "vectors" / "lk1.json"
BACKFILL = REPO_ROOT / "scripts" / "backfill_label_key.py"

TABLES = [
    "inventory_items", "asset_cm_state", "asset_logistics_status",
    "telemetry_latest_state", "tactical_events", "asset_telemetry_windows",
    "parts_availability", "asset_capability_state", "asset_element_telemetry",
    "asset_element_state", "intake_records", "effector_launch",
    "effector_resupply", "munition_track", "asset_declaration",
    "asset_correlation", "asset_fielding",
]
TRIGGER = "trg_label_key_stamp"
POLICY_1 = "example:policy-1"
NULL_MARK = "<NULL>"


def _text() -> str:
    return MIGRATION.read_text(encoding="utf-8")


def _hcl_table(name: str) -> str:
    m = re.search(r'^table "%s" \{\n(.*?)^\}\n' % name, HCL.read_text(encoding="utf-8"), re.S | re.M)
    assert m, name
    return m.group(1)


def _strip_txn(path: Path) -> str:
    lines = path.read_text(encoding="utf-8").split("\n")
    return "\n".join(x for x in lines if x not in ("BEGIN;", "COMMIT;"))


def _chain(include_new: bool) -> str:
    files = sorted(MIGRATIONS.glob("*.sql"))
    if not include_new:
        files = [f for f in files if f.name < MIGRATION.name]
    return "\n".join(_strip_txn(f).rstrip("\n") for f in files)


def _backfill_module():
    spec = importlib.util.spec_from_file_location("backfill_label_key_under_test", BACKFILL)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


# ---------------------------------------------------------------------------
# Postgres-free tests
# ---------------------------------------------------------------------------

def test_migration_sorts_after_lk2_and_is_in_atlas_sum():
    assert MIGRATION.name > LK2_MIGRATION.name
    assert max(p.name for p in MIGRATIONS.glob("*.sql")) == MIGRATION.name
    sum_lines = (MIGRATIONS / "atlas.sum").read_text(encoding="utf-8").splitlines()
    assert any(x.startswith(MIGRATION.name + " h1:") for x in sum_lines)


def test_own_transaction_with_local_timeouts_and_version_guard():
    statements = [x for x in _text().split("\n") if x.strip() and not x.startswith("--")]
    assert statements[0] == "BEGIN;"
    assert re.fullmatch(r"SET LOCAL lock_timeout = '\d+m?s';", statements[1])
    assert re.fullmatch(r"SET LOCAL statement_timeout = '\d+m?s';", statements[2])
    assert statements[-1] == "COMMIT;"
    assert "110000" in _text()
    assert "server_version_num" in _text()


def test_each_of_17_tables_gets_columns_index_and_trigger():
    text = _text()
    for t in TABLES:
        assert re.search(
            r'ALTER TABLE "public"\."%s"\s+ADD COLUMN IF NOT EXISTS "label_key" text NULL,\s+'
            r'ADD COLUMN IF NOT EXISTS "label" jsonb NULL;' % t, text), t
        assert 'CREATE INDEX IF NOT EXISTS "idx_%s_label_key" ON "public"."%s" ("label_key");' % (t, t) in text, t
        assert re.search(
            r'CREATE TRIGGER "%s"\s+BEFORE INSERT OR UPDATE ON "public"\."%s"\s+'
            r'FOR EACH ROW EXECUTE FUNCTION "public"\."label_key_stamp"\(\);' % (TRIGGER, t), text), t
    code = "\n".join(x for x in text.split("\n") if not x.startswith("--"))
    assert code.count("CREATE TRIGGER") == 17
    assert code.count("CREATE INDEX") == 17


def test_failure_episodes_is_not_touched():
    code = "\n".join(x for x in _text().split("\n") if not x.startswith("--"))
    assert "failure_episodes" not in code


def test_no_generated_column_no_concurrently_no_policy_row_written():
    code = "\n".join(x for x in _text().split("\n") if not x.startswith("--"))
    assert "GENERATED" not in code.upper()
    assert "CONCURRENTLY" not in code.upper()
    assert not re.search(r'INSERT\s+INTO\s+"?public"?\."?label_policy', code)
    assert "session_replication_role" not in code


def test_plain_indexes_name_the_lock_and_carry_the_squawk_ignore():
    text = _text()
    assert "SHARE lock" in text
    assert text.count("-- squawk-ignore require-concurrent-index-creation") == 17


def test_wrappers_are_stable_and_the_trigger_function_has_one_registry_insert():
    text = _text()
    for fn in ("label_key_of", "label_body_of"):
        assert re.search(r'FUNCTION "public"\."%s"\(.*?\)\s+RETURNS \w+\s+LANGUAGE sql STABLE' % fn, text, re.S), fn
    assert text.count('INSERT INTO "public"."label_registry"') == 1
    assert "ON CONFLICT (\"label_key\") DO NOTHING" in text


def test_schema_hcl_matches_for_the_17_tables_and_the_two_new_ones():
    for t in TABLES:
        body = _hcl_table(t)
        assert re.search(r'column "label_key" \{\s+type = text\s+null = true\s+\}', body), t
        assert re.search(r'column "label" \{\s+type = jsonb\s+null = true\s+\}', body), t
        assert re.search(r'index "idx_%s_label_key" \{\s+columns = \[column\.label_key\]\s+\}' % t, body), t
    policy = _hcl_table("label_policy")
    for c in ("singleton", "policy_id", "declared_by", "declared_at"):
        assert 'column "%s"' % c in policy
    assert "singleton" in policy and "^[a-z0-9][a-z0-9:._-]{0,62}$" in policy
    registry = _hcl_table("label_registry")
    for c in ("label_key", "label", "first_seen"):
        assert 'column "%s"' % c in registry


def test_hcl_tables_with_the_pair_are_the_17_plus_failure_episodes():
    src = HCL.read_text(encoding="utf-8")
    carriers = set()
    for m in re.finditer(r'^table "(\w+)" \{\n(.*?)^\}\n', src, re.S | re.M):
        if 'column "originator_nation"' in m.group(2) and 'column "releasable_to"' in m.group(2):
            carriers.add(m.group(1))
    assert carriers == set(TABLES) | {"failure_episodes"}


def test_failure_episodes_hcl_keeps_its_own_label_columns_and_checks():
    body = _hcl_table("failure_episodes")
    assert 'check "failure_episodes_label_key_form"' in body
    assert 'check "failure_episodes_label_pair"' in body
    assert 'index "idx_failure_episodes_label_key"' in body


def test_backfill_table_list_matches_schema_hcl_primary_keys():
    mod = _backfill_module()
    assert [t for t, _ in mod.TABLES] == TABLES
    for t, pk in mod.TABLES:
        m = re.search(r'primary_key \{\s*columns = \[(.*?)\]', _hcl_table(t), re.S)
        assert m, t
        assert tuple(re.findall(r"column\.(\w+)", m.group(1))) == pk, t


# ---------------------------------------------------------------------------
# Postgres tests
# ---------------------------------------------------------------------------

def _pg_skip_reason() -> str | None:
    if not os.environ.get("OPENDDIL_TEST_PG_DSN"):
        return "OPENDDIL_TEST_PG_DSN is not set"
    if shutil.which("psql") is None:
        return "psql is not on PATH"
    return None


requires_pg = pytest.mark.skipif(_pg_skip_reason() is not None, reason=_pg_skip_reason() or "")


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


def _run_psql(script: str) -> subprocess.CompletedProcess:
    dsn = os.environ["OPENDDIL_TEST_PG_DSN"]
    fd, path = tempfile.mkstemp(suffix=".sql", text=False)
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(script.encode("utf-8"))
        return subprocess.run(
            ["psql", "-X", "-q", "-A", "-t", "-F", "\t", "-d", dsn, "-f", path],
            capture_output=True, text=True, encoding="utf-8", timeout=180,
        )
    finally:
        os.unlink(path)


FRESH_GUARD = """
DO $guard$
BEGIN
  IF to_regclass('public.inventory_items') IS NOT NULL OR to_regclass('public.label_policy') IS NOT NULL THEN
    RAISE EXCEPTION 'the test database is not empty: this test needs a fresh database';
  END IF;
END
$guard$;
"""


def _run(body: str, include_new: bool = True) -> dict[str, list[list[str]]]:
    """Run FRESH_GUARD, the chain, then `body`, all in one transaction that is
    rolled back. Returns {tag: [row fields after the tag, ...]}."""
    script = "\n".join([
        "\\set ON_ERROR_STOP 1", "BEGIN;", FRESH_GUARD, _chain(include_new), "", body, "ROLLBACK;", "",
    ])
    proc = _run_psql(script)
    assert proc.returncode == 0, proc.stderr
    rows: dict[str, list[list[str]]] = {}
    for line in proc.stdout.splitlines():
        if line:
            parts = line.split("\t")
            rows.setdefault(parts[0], []).append(parts[1:])
    return rows


def _tag(tag: str, select: str) -> str:
    """`select` is a SELECT list; every column is cast to text with NULL marked."""
    cols = [c.strip() for c in select.split("|||")]
    inner = ", ".join("coalesce((%s)::text, '%s')" % (c, NULL_MARK) for c in cols)
    return "SELECT %s, %s;" % (_lit(tag), inner)


def _tag_query(tag: str, query: str) -> str:
    """Wrap a query returning one text column per row."""
    return "SELECT %s, coalesce(q::text, '%s') FROM (%s) AS t(q);" % (_lit(tag), NULL_MARK, query)


def _policy(policy_id: str = POLICY_1) -> str:
    return ("INSERT INTO label_policy (policy_id, declared_by, declared_at) VALUES (%s, 'example', now());"
            % _lit(policy_id))


def _ins(table_id: str, nation: str | None, release: list | None, table: str = "inventory_items") -> str:
    if table == "inventory_items":
        return ("INSERT INTO inventory_items (id, name, originator_nation, releasable_to) VALUES (%s, 'n', %s, %s);"
                % (_lit(table_id), _lit(nation), _arr(release)))
    assert table == "asset_cm_state"
    return ("INSERT INTO asset_cm_state (asset_id, originator_nation, releasable_to) VALUES (%s, %s, %s);"
            % (_lit(table_id), _lit(nation), _arr(release)))


def _row(tag: str, table: str, pk: str, id_: str) -> str:
    return ("SELECT %s, %s, coalesce(label_key, '%s'), coalesce(label::text, '%s') FROM %s WHERE %s = %s;"
            % (_lit(tag), _lit(id_), NULL_MARK, NULL_MARK, table, pk, _lit(id_)))


def _vectors() -> list[dict]:
    return json.loads(VECTORS.read_text(encoding="utf-8"))


@requires_pg
def test_chain_applies_on_empty_database_and_creates_the_catalog_objects():
    parts = []
    parts.append(_tag("policy_rows", "(SELECT count(*) FROM label_policy)"))
    parts.append(_tag("registry_rows", "(SELECT count(*) FROM label_registry)"))
    for t in TABLES:
        parts.append(_tag(
            "table:" + t,
            "(SELECT count(*) FROM information_schema.columns WHERE table_schema='public' AND table_name=%s "
            "AND column_name='label_key' AND data_type='text' AND is_nullable='YES')"
            " ||| (SELECT count(*) FROM information_schema.columns WHERE table_schema='public' AND table_name=%s "
            "AND column_name='label' AND data_type='jsonb' AND is_nullable='YES')"
            " ||| (SELECT count(*) FROM pg_indexes WHERE schemaname='public' AND tablename=%s "
            "AND indexname='idx_%s_label_key' AND indexdef LIKE '%%(label_key)')"
            " ||| (SELECT count(*) FROM pg_trigger WHERE tgrelid=('public.'||%s)::regclass AND tgname=%s "
            "AND NOT tgisinternal AND tgtype = 23 AND tgfoid = 'public.label_key_stamp()'::regprocedure)"
            % (_lit(t), _lit(t), _lit(t), t, _lit(t), _lit(TRIGGER))))
    parts.append(_tag("triggers_total", "(SELECT count(*) FROM pg_trigger WHERE tgname=%s AND NOT tgisinternal)" % _lit(TRIGGER)))
    parts.append(_tag("registry_cols", "(SELECT string_agg(column_name||':'||data_type||':'||is_nullable, ',' ORDER BY ordinal_position) "
                      "FROM information_schema.columns WHERE table_schema='public' AND table_name='label_registry')"))
    parts.append(_tag("policy_cols", "(SELECT string_agg(column_name||':'||data_type||':'||is_nullable, ',' ORDER BY ordinal_position) "
                      "FROM information_schema.columns WHERE table_schema='public' AND table_name='label_policy')"))
    rows = _run("\n".join(parts))
    assert rows["policy_rows"] == [["0"]]
    assert rows["registry_rows"] == [["0"]]
    for t in TABLES:
        assert rows["table:" + t] == [["1", "1", "1", "1"]], t
    assert rows["triggers_total"] == [["17"]]
    assert rows["registry_cols"] == [["label_key:text:NO,label:jsonb:NO,first_seen:timestamp with time zone:NO"]]
    assert rows["policy_cols"] == [[
        "singleton:boolean:NO,policy_id:text:NO,declared_by:text:NO,declared_at:timestamp with time zone:NO"]]


@requires_pg
def test_policy_table_refuses_a_second_row_and_a_malformed_id():
    for bad in (
        "INSERT INTO label_policy (singleton, policy_id, declared_by, declared_at) VALUES (false, 'a', 'x', now());",
        "INSERT INTO label_policy (policy_id, declared_by, declared_at) VALUES ('Bad Policy', 'x', now());",
    ):
        script = "\n".join(["\\set ON_ERROR_STOP 1", "BEGIN;", FRESH_GUARD, _chain(True), bad, "ROLLBACK;", ""])
        assert _run_psql(script).returncode != 0, bad
    script = "\n".join(["\\set ON_ERROR_STOP 1", "BEGIN;", FRESH_GUARD, _chain(True), _policy(), _policy("example:policy-2"), "ROLLBACK;", ""])
    proc = _run_psql(script)
    assert proc.returncode != 0
    assert "label_policy_pkey" in proc.stderr or "duplicate key" in proc.stderr


@requires_pg
def test_failure_episodes_is_unchanged_by_the_migration():
    snap = """
CREATE TEMP TABLE fe_{n} AS
SELECT 'col' AS k, column_name || ':' || data_type || ':' || is_nullable || ':' || coalesce(column_default, '') AS v
  FROM information_schema.columns WHERE table_schema='public' AND table_name='failure_episodes'
UNION ALL SELECT 'con', conname || ':' || pg_get_constraintdef(oid) FROM pg_constraint WHERE conrelid='public.failure_episodes'::regclass
UNION ALL SELECT 'idx', indexdef FROM pg_indexes WHERE schemaname='public' AND tablename='failure_episodes'
UNION ALL SELECT 'trg', tgname FROM pg_trigger WHERE tgrelid='public.failure_episodes'::regclass AND NOT tgisinternal;
"""
    script = "\n".join([
        "\\set ON_ERROR_STOP 1", "BEGIN;", FRESH_GUARD, _chain(False), snap.format(n="before"),
        _strip_txn(MIGRATION), snap.format(n="after"),
        _tag("only_before", "(SELECT count(*) FROM (SELECT * FROM fe_before EXCEPT SELECT * FROM fe_after) x)"),
        _tag("only_after", "(SELECT count(*) FROM (SELECT * FROM fe_after EXCEPT SELECT * FROM fe_before) x)"),
        _tag("seen", "(SELECT count(*) FROM fe_before)"),
        _tag("trg", "(SELECT count(*) FROM pg_trigger WHERE tgrelid='public.failure_episodes'::regclass AND NOT tgisinternal)"),
        "ROLLBACK;", "",
    ])
    proc = _run_psql(script)
    assert proc.returncode == 0, proc.stderr
    rows = {}
    for line in proc.stdout.splitlines():
        if line:
            p = line.split("\t")
            rows[p[0]] = p[1:]
    assert rows["only_before"] == ["0"] and rows["only_after"] == ["0"]
    assert int(rows["seen"][0]) > 10
    assert rows["trg"] == ["0"]


@requires_pg
def test_rows_written_before_the_migration_are_backfilled_and_only_labelled_rows_get_a_key():
    mod = _backfill_module()
    rows_before = [
        # id, nation, releasable_to
        ("a_absent", "ATL", None),
        ("b_empty", "ATL", []),
        ("c_bdr", "ATL", ["BDR"]),
        ("d_swapped", "ATL", ["CRD", "BDR", "CRD"]),
        ("e_none", None, None),
        ("f_finding5", None, ["BDR"]),
        ("g_blank", "", ["BDR"]),
        ("h_lower", "atl", None),
    ]
    labelled = {"a_absent", "b_empty", "c_bdr", "d_swapped"}
    prior = [_ins(i, n, r) for i, n, r in rows_before]
    prior += [_ins(i, n, r, table="asset_cm_state") for i, n, r in rows_before]

    # The script's own batch SQL against inventory_items, windows of 2, each
    # resuming after the last key of the one before. Six rows carry a nation
    # (e_none and f_finding5 do not), so: three full windows, then an empty one.
    ordered = sorted(i for i, n, _ in rows_before if n is not None)
    assert len(ordered) == 6
    batches = []
    after = None
    for w in range(4):
        batches.append("\\echo BATCH:%d" % w)
        batches.append(mod.batch_sql("inventory_items", ("id",), after, 2) + ";")
        after = [ordered[min(2 * w + 1, len(ordered) - 1)]]
    plain = ("UPDATE asset_cm_state SET originator_nation = originator_nation "
             "WHERE label_key IS NULL AND originator_nation IS NOT NULL;")
    script = "\n".join([
        "\\set ON_ERROR_STOP 1", "BEGIN;", FRESH_GUARD, _chain(False), "", "\n".join(prior), "",
        _strip_txn(MIGRATION), "",
        _tag("after_migration", "(SELECT count(*) FROM inventory_items WHERE label_key IS NOT NULL)"
                                " ||| (SELECT count(*) FROM asset_cm_state WHERE label_key IS NOT NULL)"
                                " ||| (SELECT count(*) FROM label_registry)"),
        _policy(),
        "\n".join(batches),
        plain,
        "SELECT 'inv', id, coalesce(label_key, '%s') FROM inventory_items ORDER BY id;" % NULL_MARK,
        "SELECT 'cm', asset_id, coalesce(label_key, '%s') FROM asset_cm_state ORDER BY asset_id;" % NULL_MARK,
        _tag("registry", "(SELECT count(*) FROM label_registry)"),
        _tag("expect", "\"public\".\"lk1_key\"(%s, 'ATL', ARRAY['BDR','CRD']::text[])" % _lit(POLICY_1)),
        "ROLLBACK;", "",
    ])
    proc = _run_psql(script)
    assert proc.returncode == 0, proc.stderr
    got: dict[str, list[list[str]]] = {}
    batch_out: dict[int, str] = {}
    lines = proc.stdout.splitlines()
    i = 0
    while i < len(lines):
        if lines[i].startswith("BATCH:"):
            batch_out[int(lines[i][6:])] = lines[i + 1]
            i += 2
            continue
        if lines[i]:
            p = lines[i].split("\t")
            got.setdefault(p[0], []).append(p[1:])
        i += 1

    # The migration does not stamp existing rows, and the registry starts empty.
    assert got["after_migration"] == [["0", "0", "0"]]

    # (rewritten, window) per batch; the script's own parser reads the rows.
    parsed = [mod.parse_batch(batch_out[w], 1) for w in range(4)]
    assert [(d, win) for d, win, _ in parsed] == [(2, 2), (2, 2), (2, 2), (0, 0)]
    assert [last for _, _, last in parsed] == [["b_empty"], ["d_swapped"], ["h_lower"], None]

    for table_tag in ("inv", "cm"):
        keys = {r[0]: r[1] for r in got[table_tag]}
        assert set(keys) == {i for i, _, _ in rows_before}
        for i in labelled:
            assert re.fullmatch(r"lk1:[0-9a-f]{64}", keys[i]), (table_tag, i)
        # No row without a valid nation gets a key: no originator, a blank one,
        # a malformed one, and Finding 5's row.
        for i in set(keys) - labelled:
            assert keys[i] == NULL_MARK, (table_tag, i)
    inv = {r[0]: r[1] for r in got["inv"]}
    cm = {r[0]: r[1] for r in got["cm"]}
    assert len({inv[i] for i in labelled}) == 4
    assert inv == cm
    assert inv["d_swapped"] == got["expect"][0][0]
    # One registry row per distinct key, across both tables.
    assert got["registry"] == [["4"]]


@requires_pg
def test_lk1_vectors_through_the_trigger():
    lines = []
    for v in _vectors():
        if v["policy_id"]:
            lines.append("DELETE FROM label_policy;")
            lines.append(_policy(v["policy_id"]))
        else:
            lines.append("DELETE FROM label_policy;")
        lines.append(_ins(v["name"], v["originator_nation"], v["releasable_to"]))
        lines.append(_row("v", "inventory_items", "id", v["name"]))
    rows = _run("\n".join(lines))
    got = {r[0]: r[1:] for r in rows["v"]}
    vectors = _vectors()
    assert len(got) == len(vectors)
    for v in vectors:
        key, label = got[v["name"]]
        assert (None if key == NULL_MARK else key) == v["label_key"], v["name"]
        assert (None if label == NULL_MARK else json.loads(label)) == v["label"], v["name"]
    assert any(v["label_key"] for v in vectors) and any(v["label_key"] is None for v in vectors)


@requires_pg
def test_no_policy_row_gives_null_keys_and_label():
    body = "\n".join([
        _ins("r1", "ATL", ["BDR"]), _ins("r2", "ATL", None), _ins("r3", None, None),
        _row("row", "inventory_items", "id", "r1"), _row("row", "inventory_items", "id", "r2"),
        _row("row", "inventory_items", "id", "r3"),
        _tag("registry", "(SELECT count(*) FROM label_registry)"),
    ])
    rows = _run(body)
    assert [r[1:] for r in rows["row"]] == [[NULL_MARK, NULL_MARK]] * 3
    assert rows["registry"] == [["0"]]


@requires_pg
def test_finding_5_no_originator_with_release_set_gets_no_key():
    body = "\n".join([
        _policy(), _ins("f5", None, ["BDR"]), _ins("f5b", "", ["BDR"]),
        _row("row", "inventory_items", "id", "f5"), _row("row", "inventory_items", "id", "f5b"),
        _tag("registry", "(SELECT count(*) FROM label_registry)"),
    ])
    rows = _run(body)
    assert [r[1:] for r in rows["row"]] == [[NULL_MARK, NULL_MARK]] * 2
    assert rows["registry"] == [["0"]]


@requires_pg
def test_declared_empty_and_absent_give_two_keys_and_one_registry_row_per_key():
    body = "\n".join([
        _policy(),
        _ins("e1", "ATL", []), _ins("n1", "ATL", None),
        # Repeats: same key from another row, another table, and an order/duplicate variant.
        _ins("e2", "ATL", []), _ins("x1", "ATL", ["BDR", "ATL"]), _ins("x2", "ATL", ["ATL", "BDR", "ATL"]),
        _ins("cm1", "ATL", [], table="asset_cm_state"),
        _row("row", "inventory_items", "id", "e1"), _row("row", "inventory_items", "id", "n1"),
        _row("row", "inventory_items", "id", "e2"),
        _row("row", "inventory_items", "id", "x1"), _row("row", "inventory_items", "id", "x2"),
        _row("row", "asset_cm_state", "asset_id", "cm1"),
        _tag("registry", "(SELECT count(*) FROM label_registry)"),
        _tag("registry_matches", "(SELECT count(*) FROM label_registry r WHERE r.label_key IN "
                                 "(SELECT label_key FROM inventory_items) AND r.label = "
                                 "(SELECT i.label FROM inventory_items i WHERE i.label_key = r.label_key LIMIT 1))"),
    ])
    rows = _run(body)
    keys = {r[0]: r[1] for r in rows["row"]}
    assert keys["e1"] != keys["n1"]
    assert keys["e1"] == keys["e2"]
    assert keys["x1"] == keys["x2"]
    assert rows["registry"] == [["3"]]
    assert rows["registry_matches"] == [["3"]]


@requires_pg
def test_update_that_changes_releasable_to_changes_the_key_and_registers_the_new_one():
    body = "\n".join([
        _policy(), _ins("u1", "ATL", ["BDR"]),
        _row("before", "inventory_items", "id", "u1"),
        "UPDATE inventory_items SET releasable_to = ARRAY['BDR','CRD']::text[] WHERE id = 'u1';",
        _row("after", "inventory_items", "id", "u1"),
        # A write that changes neither column keeps the key.
        "UPDATE inventory_items SET name = 'renamed' WHERE id = 'u1';",
        _row("same", "inventory_items", "id", "u1"),
        # Moving the row to no originator clears both columns.
        "UPDATE inventory_items SET originator_nation = NULL WHERE id = 'u1';",
        _row("cleared", "inventory_items", "id", "u1"),
        _tag("registry", "(SELECT count(*) FROM label_registry)"),
    ])
    rows = _run(body)
    before, after, same, cleared = (rows[k][0] for k in ("before", "after", "same", "cleared"))
    assert before[1] != NULL_MARK and after[1] != NULL_MARK and before[1] != after[1]
    assert same[1:] == after[1:]
    assert cleared[1:] == [NULL_MARK, NULL_MARK]
    assert rows["registry"] == [["2"]]


@requires_pg
def test_copy_fires_the_trigger():
    copy = "COPY inventory_items (id, name, originator_nation, releasable_to) FROM STDIN;\n" + "\n".join([
        "c1\tn\tATL\t{BDR}",
        "c2\tn\tATL\t\\N",
        "c3\tn\t\\N\t{BDR}",
        "\\.",
    ])
    body = "\n".join([
        _policy(), copy,
        _row("row", "inventory_items", "id", "c1"), _row("row", "inventory_items", "id", "c2"),
        _row("row", "inventory_items", "id", "c3"),
        _tag("registry", "(SELECT count(*) FROM label_registry)"),
    ])
    rows = _run(body)
    got = {r[0]: r[1] for r in rows["row"]}
    assert re.fullmatch(r"lk1:[0-9a-f]{64}", got["c1"]) and re.fullmatch(r"lk1:[0-9a-f]{64}", got["c2"])
    assert got["c1"] != got["c2"]
    assert got["c3"] == NULL_MARK
    assert rows["registry"] == [["2"]]
