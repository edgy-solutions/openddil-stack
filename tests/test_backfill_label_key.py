"""
scripts/backfill_label_key.py, offline: its SQL text, its batching and its
primary-key handling, with the kubectl runner faked. No cluster, no postgres.
The SQL itself is run against postgres in tests/test_label_key_compat_sql.py.

Fixtures use invented values only.
"""
from __future__ import annotations

import importlib.util
import io
import json
import re
import subprocess
from pathlib import Path

import pytest

SCRIPT = Path(__file__).parent.parent / "scripts" / "backfill_label_key.py"


def _load():
    spec = importlib.util.spec_from_file_location("backfill_label_key_offline", SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


bf = _load()


def _cp(stdout="", returncode=0, stderr=""):
    return subprocess.CompletedProcess([], returncode, stdout, stderr)


# --- SQL text ---------------------------------------------------------------

def test_there_are_17_tables_and_failure_episodes_is_not_one():
    assert len(bf.TABLES) == 17
    assert len({t for t, _ in bf.TABLES}) == 17
    assert "failure_episodes" not in {t for t, _ in bf.TABLES}


def test_default_batch_is_5000():
    assert bf.BATCH_SIZE == 5000
    assert "LIMIT 5000" in bf.batch_sql("inventory_items", ("id",), None)


def test_count_sql_is_the_backfill_predicate_and_read_only():
    sql = bf.count_sql("inventory_items")
    assert sql == ('SELECT count(*) FROM "inventory_items" '
                   "WHERE label_key IS NULL AND originator_nation IS NOT NULL")
    assert not re.search(r"\b(UPDATE|INSERT|DELETE|ALTER|DROP)\b", sql)


def test_batch_sql_single_key_first_window():
    sql = bf.batch_sql("asset_cm_state", ("asset_id",), None, 5000)
    assert 'UPDATE "asset_cm_state" AS x SET originator_nation = x.originator_nation' in sql
    assert "label_key IS NULL AND originator_nation IS NOT NULL" in sql
    assert 'ORDER BY "asset_id" LIMIT 5000' in sql
    assert ">" not in sql.replace("->", "")  # no resume predicate on the first window
    assert sql.count("UPDATE") == 1


def test_batch_sql_resumes_after_the_last_key():
    sql = bf.batch_sql("asset_cm_state", ("asset_id",), ["asset-1"], 5000)
    assert """AND ("asset_id") > ('asset-1')""" in sql


def test_batch_sql_composite_key_uses_a_row_comparison_in_key_order():
    sql = bf.batch_sql("parts_availability", ("site", "part_ref"), ["site-1", "part-9"], 10)
    assert """AND ("site", "part_ref") > ('site-1', 'part-9')""" in sql
    assert 'ORDER BY "site", "part_ref" LIMIT 10' in sql
    assert 'x."site" = b."site" AND x."part_ref" = b."part_ref"' in sql
    assert 'b."site" DESC, b."part_ref" DESC' in sql


def test_key_named_key_is_quoted():
    sql = bf.batch_sql("intake_records", ("kind", "key"), None, 5)
    assert 'ORDER BY "kind", "key" LIMIT 5' in sql


def test_resume_key_values_are_quoted_as_literals():
    sql = bf.batch_sql("asset_cm_state", ("asset_id",), ["o'brien; DROP TABLE x"], 5)
    assert "('o''brien; DROP TABLE x')" in sql


def test_resume_key_width_must_match_the_primary_key():
    with pytest.raises(ValueError):
        bf.batch_sql("parts_availability", ("site", "part_ref"), ["only-one"], 5)


def test_identifiers_are_checked():
    with pytest.raises(ValueError):
        bf.ident('x"; DROP TABLE y; --')


def test_every_table_builds_a_batch_statement_from_its_pk():
    for table, pk in bf.TABLES:
        sql = bf.batch_sql(table, pk, ["v"] * len(pk), 7)
        assert '"%s"' % table in sql
        for c in pk:
            assert '"%s"' % c in sql


# --- batch results ----------------------------------------------------------

def test_parse_batch():
    assert bf.parse_batch('5000\t5000\t["asset-9"]\n', 1) == (5000, 5000, ["asset-9"])
    assert bf.parse_batch('3\t3\t["s","p"]', 2) == (3, 3, ["s", "p"])
    assert bf.parse_batch("0\t0\t\n", 1) == (0, 0, None)


def test_parse_batch_refuses_malformed_output():
    for bad in ("", "1\t2", "x\ty\tz", '1\t1\t["a","b"]'):
        with pytest.raises(bf.Refusal):
            bf.parse_batch(bad, 1)


class FakeDb:
    """Answers batch statements for a table of sorted single keys, as postgres
    would: the window is the next `size` keys after the resume key, every one
    of which is rewritten. Records each statement."""

    def __init__(self, keys, size):
        self.keys = sorted(keys)
        self.size = size
        self.sql = []

    def __call__(self, sql):
        self.sql.append(sql)
        m = re.search(r"AND \(\"id\"\) > \('([^']*)'\)", sql)
        rest = [k for k in self.keys if m is None or k > m.group(1)]
        window = rest[: self.size]
        last = json.dumps([window[-1]]) if window else ""
        return f"{len(window)}\t{len(window)}\t{last}\n"


def test_batching_walks_the_keys_in_windows_and_counts_batches():
    db = FakeDb([f"k{i:03d}" for i in range(11)], 5)
    log = []
    batches, rows = bf.plan_batches(db, "inventory_items", ("id",), 5, log=log.append)
    assert (batches, rows) == (3, 11)
    assert len(db.sql) == 3  # the short third window ends the run
    assert "> (" not in db.sql[0]
    assert "> ('k004')" in db.sql[1] and "> ('k009')" in db.sql[2]
    assert [x.split(":")[1].strip().split(" rewrote")[0] for x in log] == ["batch 1", "batch 2", "batch 3"]


def test_batching_exact_multiple_ends_on_an_empty_window():
    db = FakeDb([f"k{i}" for i in range(4)], 2)
    batches, rows = bf.plan_batches(db, "inventory_items", ("id",), 2)
    assert (batches, rows) == (2, 4)
    assert len(db.sql) == 3


def test_batching_an_empty_table_runs_one_statement_and_no_batch():
    db = FakeDb([], 5)
    assert bf.plan_batches(db, "inventory_items", ("id",), 5) == (0, 0)
    assert len(db.sql) == 1


def test_batching_terminates_when_rows_stay_unstamped():
    # The keyset moves past rows that a rewrite leaves NULL (a malformed
    # nation), so a repeat-until-zero loop is not what runs here.
    db = FakeDb(["a", "b", "c"], 2)
    assert bf.plan_batches(db, "inventory_items", ("id",), 2) == (2, 3)


# --- cluster paths, runner faked -------------------------------------------

def _kube(responses, calls):
    def run(argv, **kw):
        calls.append(argv)
        return responses(argv)
    return bf.Kube("ctx-a", "ns-a", "pod-0", "openddil", run=run)


def test_psql_goes_through_kubectl_exec_pinned_to_context_and_namespace():
    calls = []
    kube = _kube(lambda a: _cp("1\n"), calls)
    assert kube.psql("SELECT 1") == "1\n"
    argv = calls[0]
    assert argv[:5] == ["kubectl", "--context", "ctx-a", "-n", "ns-a"]
    assert argv[5:8] == ["exec", "pod-0", "--"]
    assert argv[8] == "psql" and "-d" in argv and argv[argv.index("-d") + 1] == "openddil"
    assert "ON_ERROR_STOP=1" in argv
    assert argv[-2:] == ["-c", "SELECT 1"]


def test_write_statements_carry_lock_and_statement_timeouts():
    argv = bf.Kube("c", "n", "p", "d").argv("SELECT 1", timeouts=True)
    cs = [argv[i + 1] for i, a in enumerate(argv) if a == "-c"]
    assert cs == ["SET lock_timeout = '5s'", "SET statement_timeout = '120s'", "SELECT 1"]


def test_psql_failure_is_a_refusal():
    kube = _kube(lambda a: _cp("", 1, "boom"), [])
    with pytest.raises(bf.Refusal):
        kube.psql("SELECT 1")


def _store(have_cols=None, policy_rows=1, counts=None):
    have = [t for t, _ in bf.TABLES] if have_cols is None else have_cols

    def responses(argv):
        sql = argv[-1]
        if "information_schema.columns" in sql:
            return _cp("\n".join(have) + "\n")
        if sql == bf.POLICY_SQL:
            return _cp(f"{policy_rows}\n")
        if sql.startswith("SELECT count(*) FROM \""):
            t = re.search(r'FROM "(\w+)"', sql).group(1)
            return _cp(f"{(counts or {}).get(t, 0)}\n")
        if sql.startswith("WITH b AS"):
            return _cp("0\t0\t\n")
        raise AssertionError(sql)
    return responses


def test_count_mode_prints_per_table_rows_and_writes_nothing():
    calls = []
    kube = _kube(_store(counts={"inventory_items": 12, "asset_fielding": 3}), calls)
    out = io.StringIO()
    assert bf.step_count(kube, out) == bf.EXIT_OK
    lines = out.getvalue().splitlines()
    assert lines[0] == "inventory_items\t12"
    assert "asset_fielding\t3" in lines
    assert lines[-1] == "total\t15"
    assert len(lines) == 18
    sent = " ".join(a[-1] for a in calls)
    assert not re.search(r"\b(UPDATE|INSERT|DELETE|ALTER|DROP)\b", sent)


def test_count_mode_refuses_when_the_migration_is_not_applied():
    kube = _kube(_store(have_cols=["inventory_items"]), [])
    with pytest.raises(bf.Refusal) as e:
        bf.step_count(kube, io.StringIO())
    assert "asset_cm_state" in e.value.reason and "not applied" in e.value.reason


def test_write_mode_refuses_with_no_policy_row_before_any_update():
    calls = []
    kube = _kube(_store(policy_rows=0), calls)
    with pytest.raises(bf.Refusal) as e:
        bf.step_write(kube, 5000, io.StringIO(), io.StringIO())
    assert "no row" in e.value.reason
    assert not any(a[-1].startswith("WITH b AS") for a in calls)


def test_write_mode_runs_every_table_and_logs_per_table():
    calls = []
    kube = _kube(_store(), calls)
    out, err = io.StringIO(), io.StringIO()
    assert bf.step_write(kube, 5000, out, err) == bf.EXIT_OK
    lines = out.getvalue().splitlines()
    assert lines[0] == "inventory_items\tbatches=0\trows=0"
    assert lines[-1] == "total\trows=0"
    updates = [a for a in calls if a[-1].startswith("WITH b AS")]
    assert len(updates) == 17
    assert all("SET lock_timeout = '5s'" in a for a in updates)


def test_write_mode_names_state_when_a_batch_fails():
    def responses(argv):
        if argv[-1].startswith("WITH b AS") and '"asset_cm_state"' in argv[-1]:
            return _cp("", 1, "deadlock detected")
        return _store()(argv)
    kube = _kube(responses, [])
    with pytest.raises(bf.Refusal) as e:
        bf.step_write(kube, 5000, io.StringIO(), io.StringIO())
    assert e.value.state["done_tables"] == ["inventory_items"]
    assert e.value.state["failed_table"] == "asset_cm_state"
    assert "asset_fielding" in e.value.state["not_attempted"]


# --- guard and exit codes ----------------------------------------------------

def _helm(tmp_path):
    g = tmp_path / "scripts" / "lib"
    g.mkdir(parents=True, exist_ok=True)
    (g / "require-cluster.sh").write_text("echo 'cluster: ctx-a (asserted)'\n", encoding="utf-8")
    return tmp_path


def _runner(guard_rc=0, guard_out="cluster: ctx-a (asserted)\n"):
    calls = []

    def run(argv, **kw):
        calls.append(argv)
        if argv[0] == "bash":
            return _cp(guard_out, guard_rc, "")
        return _store(counts={"inventory_items": 2})(argv)
    return run, calls


ARGS = ["-n", "ns-a", "--pod", "pod-0"]


def test_main_count_exit_0(tmp_path, capsys):
    run, calls = _runner()
    assert bf.main(["--count", "--helm-dir", str(_helm(tmp_path))] + ARGS, run=run) == 0
    assert "inventory_items\t2" in capsys.readouterr().out
    assert calls[0][0] == "bash"  # the guard ran first
    assert all(a[:5] == ["kubectl", "--context", "ctx-a", "-n", "ns-a"] for a in calls[1:])


def test_main_guard_exit_78_is_a_halt(tmp_path):
    run, calls = _runner(guard_rc=78)
    with pytest.raises(SystemExit) as e:
        bf.main(["--count", "--helm-dir", str(_helm(tmp_path))] + ARGS, run=run)
    assert e.value.code == 78
    assert len(calls) == 1  # nothing ran after the guard


def test_main_other_guard_failure_is_exit_3(tmp_path):
    run, _ = _runner(guard_rc=2)
    with pytest.raises(SystemExit) as e:
        bf.main(["--count", "--helm-dir", str(_helm(tmp_path))] + ARGS, run=run)
    assert e.value.code == 3


def test_main_missing_guard_file_is_refused_exit_3(tmp_path):
    run, _ = _runner()
    assert bf.main(["--count", "--helm-dir", str(tmp_path)] + ARGS, run=run) == 3


def test_main_context_flag_must_match_the_asserted_context(tmp_path):
    run, calls = _runner()
    rc = bf.main(["--count", "--helm-dir", str(_helm(tmp_path)), "--context", "ctx-b"] + ARGS, run=run)
    assert rc == 3
    assert len(calls) == 1
    run, _ = _runner()
    assert bf.main(["--count", "--helm-dir", str(_helm(tmp_path)), "--context", "ctx-a"] + ARGS, run=run) == 0


def test_main_write_without_policy_is_exit_3(tmp_path):
    def run(argv, **kw):
        if argv[0] == "bash":
            return _cp("cluster: ctx-a (asserted)\n")
        return _store(policy_rows=0)(argv)
    assert bf.main(["--helm-dir", str(_helm(tmp_path))] + ARGS, run=run) == 3


def test_count_is_the_default_off_write_is_a_separate_invocation():
    src = SCRIPT.read_text(encoding="utf-8")
    assert 'add_argument("--count"' in src
    assert "It never runs in a helm hook." in src
