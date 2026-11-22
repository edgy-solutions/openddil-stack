#!/usr/bin/env python3
"""Backfill label_key and label on rows written before the trigger existed.

DESIGN-2026-11-06 section 1.4 (ADR-0055 compatibility slice). The migration
20261121000000_label_key_compat_columns.sql adds label_key, label and a
BEFORE INSERT OR UPDATE trigger to 17 tables. Rows written before it keep
label_key NULL. This script rewrites them so the trigger stamps them:

    UPDATE <t> SET originator_nation = originator_nation
     WHERE label_key IS NULL AND originator_nation IS NOT NULL

in batches of 5,000 rows by primary key, one commit per batch, the batch count
logged to stderr.

TWO INVOCATIONS, NEVER ONE
    python scripts/backfill_label_key.py --count --helm-dir ../openddil-helm -n NS --pod POD
        reads only. Prints, per table, the rows the backfill would rewrite:
        the input to the prediction (rows per table and the shape bytes
        expected). Writes nothing.
    python scripts/backfill_label_key.py         --helm-dir ../openddil-helm -n NS --pod POD
        writes. Run it only after the prediction is confirmed.

WHAT IT COSTS. It rewrites every labelled row, so every Electric shape over
those tables sees an update. The repo's record shows a bulk commit reaching
shapes 10 to 100 s late, and old shapes growing until evicted. That is why this
is a named step with its own prediction and is never folded into the migration.
It never runs in a helm hook.

REFUSALS. The write mode refuses (exit 3, nothing written) when a table lacks
label_key (the migration is not applied) or when label_policy has no row (every
key would come out NULL, and the rewrite would be shape churn for nothing).
Neither refusal is retried.

EXIT CODES: 0 done, 3 refused, 78 the cluster guard refused.

THE CLUSTER GUARD runs first: the chart's scripts/lib/require-cluster.sh, as in
openddil-contracts scripts/migrate_site_declarations.py. Its exit 78 is a halt.
Every kubectl call is then pinned to the context it asserted. --context, when
given, must equal that asserted context or the run is refused.

Standard library only.
"""
from __future__ import annotations

import argparse
import json
import pathlib
import re
import subprocess
import sys

EXIT_OK = 0
EXIT_REFUSED = 3
EXIT_GUARD = 78

BATCH_SIZE = 5000

# The 17 tables that carry originator_nation and releasable_to (spec Finding 1)
# with their primary keys, read from schema/schema.hcl. tests/ checks this list
# against schema.hcl so it cannot drift.
TABLES: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("inventory_items", ("id",)),
    ("asset_cm_state", ("asset_id",)),
    ("asset_logistics_status", ("asset_id",)),
    ("telemetry_latest_state", ("asset_id",)),
    ("tactical_events", ("id",)),
    ("asset_telemetry_windows", ("asset_id",)),
    ("parts_availability", ("site", "part_ref")),
    ("asset_capability_state", ("asset_id",)),
    ("asset_element_telemetry", ("asset_id",)),
    ("asset_element_state", ("asset_id", "element_path")),
    ("intake_records", ("kind", "key")),
    ("effector_launch", ("event_urn",)),
    ("effector_resupply", ("event_urn", "munition_type")),
    ("munition_track", ("munition_asset_id",)),
    ("asset_declaration", ("asset_id",)),
    ("asset_correlation", ("serial",)),
    ("asset_fielding", ("serial",)),
)


class Refusal(Exception):
    """A refusal. Halts the run; `state` names what was and was not done."""

    def __init__(self, reason: str, state: dict | None = None):
        super().__init__(reason)
        self.reason = reason
        self.state = state or {}


# --------------------------------------------------------------------------
# Pure: SQL text and batching
# --------------------------------------------------------------------------

def ident(name: str) -> str:
    if not re.fullmatch(r"[a-z_][a-z0-9_]*", name):
        raise ValueError(f"not a plain identifier: {name!r}")
    return '"' + name + '"'


def lit(value) -> str:
    """A SQL string literal. The server casts the unknown literal to the key's
    type. Assumes standard_conforming_strings = on (the default)."""
    s = str(value)
    if "\x00" in s:
        raise ValueError("NUL in a key value")
    return "'" + s.replace("'", "''") + "'"


def count_sql(table: str) -> str:
    return (f"SELECT count(*) FROM {ident(table)} "
            "WHERE label_key IS NULL AND originator_nation IS NOT NULL")


def columns_sql() -> str:
    names = ", ".join(lit(t) for t, _ in TABLES)
    return ("SELECT table_name FROM information_schema.columns "
            "WHERE table_schema = 'public' AND column_name = 'label_key' "
            f"AND table_name IN ({names}) ORDER BY 1")


POLICY_SQL = "SELECT count(*) FROM label_policy"


def batch_sql(table: str, pk: tuple[str, ...], after: list | None,
              size: int = BATCH_SIZE) -> str:
    """One batch: the next `size` unstamped rows after the key `after`, in
    primary-key order, rewritten with the no-op assignment that fires the
    trigger. Returns one row: rows rewritten, rows in the window, and the
    window's last key as a JSON array (NULL when the window is empty)."""
    if after is not None and len(after) != len(pk):
        raise ValueError("resume key does not match the primary key")
    t = ident(table)
    cols = ", ".join(ident(c) for c in pk)
    keyset = ""
    if after is not None:
        keyset = f" AND ({cols}) > ({', '.join(lit(v) for v in after)})"
    join = " AND ".join(f"x.{ident(c)} = b.{ident(c)}" for c in pk)
    ret = ", ".join(f"x.{ident(c)}" for c in pk)
    last = ", ".join(f"b.{ident(c)}" for c in pk)
    desc = ", ".join(f"b.{ident(c)} DESC" for c in pk)
    return (
        f"WITH b AS (SELECT {cols} FROM {t} "
        f"WHERE label_key IS NULL AND originator_nation IS NOT NULL{keyset} "
        f"ORDER BY {cols} LIMIT {int(size)}), "
        f"u AS (UPDATE {t} AS x SET originator_nation = x.originator_nation "
        f"FROM b WHERE {join} AND x.label_key IS NULL "
        f"AND x.originator_nation IS NOT NULL RETURNING {ret}) "
        "SELECT (SELECT count(*) FROM u), (SELECT count(*) FROM b), "
        f"(SELECT json_build_array({last}) FROM b ORDER BY {desc} LIMIT 1)"
    )


def parse_batch(out: str, width: int) -> tuple[int, int, list | None]:
    """(rows rewritten, rows in the window, last key or None) from a batch row."""
    line = out.strip("\r\n")
    parts = line.split("\t")
    if len(parts) != 3:
        raise Refusal(f"unexpected batch result: {line[:200]!r}")
    try:
        done, window = int(parts[0]), int(parts[1])
    except ValueError:
        raise Refusal(f"unexpected batch counts: {line[:200]!r}")
    last = json.loads(parts[2]) if parts[2] else None
    if window and (not isinstance(last, list) or len(last) != width):
        raise Refusal(f"unexpected last key: {parts[2][:200]!r}")
    return done, window, last


def plan_batches(runner_rows, table: str, pk: tuple[str, ...], size: int = BATCH_SIZE,
                 log=lambda s: None) -> tuple[int, int]:
    """Run batches until a window comes back short. `runner_rows(sql)` returns
    psql's stdout. Returns (batches run, rows rewritten). A keyset, not a
    repeat-until-zero, so rows that stay NULL (a malformed nation) cannot loop."""
    after = None
    batches = rows = 0
    while True:
        out = runner_rows(batch_sql(table, pk, after, size))
        done, window, last = parse_batch(out, len(pk))
        if window == 0:
            break
        batches += 1
        rows += done
        log(f"{table}: batch {batches} rewrote {done} of {window} rows (total {rows})")
        if window < size:
            break
        after = last
    return batches, rows


# --------------------------------------------------------------------------
# Cluster (the guard, then kubectl exec psql)
# --------------------------------------------------------------------------

def run_guard(helm_dir: pathlib.Path, run=subprocess.run) -> str:
    guard = helm_dir / "scripts" / "lib" / "require-cluster.sh"
    if not guard.is_file():
        raise Refusal(f"no cluster guard at {guard}")
    p = run(["bash", "-c", '. "$1"', "_", str(guard)], capture_output=True, text=True)
    sys.stderr.write(p.stderr)
    if p.returncode != 0:
        sys.exit(EXIT_GUARD if p.returncode == EXIT_GUARD else EXIT_REFUSED)
    m = re.search(r"^cluster: (\S+) \(asserted\)$", p.stdout, re.M)
    if not m:
        raise Refusal("the guard passed but printed no asserted context")
    return m.group(1)


class Kube:
    def __init__(self, context: str, namespace: str, pod: str, db: str,
                 user: str = "openddil", run=subprocess.run):
        self.base = ["kubectl", "--context", context, "-n", namespace]
        self.pod, self.db, self.user, self.run = pod, db, user, run

    def argv(self, sql: str, timeouts: bool) -> list[str]:
        a = self.base + ["exec", self.pod, "--", "psql", "-q", "-U", self.user,
                         "-d", self.db, "-v", "ON_ERROR_STOP=1", "-tA", "-F", "\t"]
        if timeouts:
            a += ["-c", "SET lock_timeout = '5s'", "-c", "SET statement_timeout = '120s'"]
        return a + ["-c", sql]

    def psql(self, sql: str, timeouts: bool = False) -> str:
        p = self.run(self.argv(sql, timeouts), capture_output=True, text=True)
        if p.returncode != 0:
            raise Refusal(f"psql in {self.pod}: {p.stderr.strip()[:500]}")
        return p.stdout


def check_ready(kube: Kube, need_policy: bool) -> None:
    have = set(kube.psql(columns_sql()).split())
    missing = [t for t, _ in TABLES if t not in have]
    if missing:
        raise Refusal("label_key is missing on: " + ", ".join(missing)
                      + " (the migration 20261121000000 is not applied)")
    if need_policy:
        try:
            n = int(kube.psql(POLICY_SQL).strip())
        except ValueError:
            raise Refusal("could not read label_policy")
        if n == 0:
            raise Refusal("label_policy has no row: every key would be NULL")


def step_count(kube: Kube, out=None) -> int:
    out = out or sys.stdout
    check_ready(kube, need_policy=False)
    total = 0
    for table, _ in TABLES:
        n = int(kube.psql(count_sql(table)).strip())
        total += n
        out.write(f"{table}\t{n}\n")
    out.write(f"total\t{total}\n")
    return EXIT_OK


def step_write(kube: Kube, size: int = BATCH_SIZE, out=None, err=None) -> int:
    out, err = out or sys.stdout, err or sys.stderr
    check_ready(kube, need_policy=True)
    done_tables = []
    total = 0
    for table, pk in TABLES:
        try:
            batches, rows = plan_batches(lambda s: kube.psql(s, timeouts=True),
                                         table, pk, size,
                                         log=lambda s: err.write(s + "\n"))
        except Refusal as r:
            r.state = {"done_tables": done_tables, "failed_table": table,
                       "rows_rewritten_before_failure": total,
                       "not_attempted": [t for t, _ in TABLES
                                         if t != table and t not in done_tables]}
            raise
        done_tables.append(table)
        total += rows
        out.write(f"{table}\tbatches={batches}\trows={rows}\n")
    out.write(f"total\trows={total}\n")
    return EXIT_OK


def main(argv: list[str] | None = None, run=subprocess.run) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--count", action="store_true",
                    help="read-only: rows per table the backfill would rewrite")
    ap.add_argument("--helm-dir", required=True)
    ap.add_argument("--context", help="must equal the context the guard asserts")
    ap.add_argument("-n", "--namespace", required=True)
    ap.add_argument("--pod", required=True, help="the store pod to exec psql in")
    ap.add_argument("--db", default="openddil")
    ap.add_argument("--user", default="openddil")
    ap.add_argument("--batch-size", type=int, default=BATCH_SIZE)
    args = ap.parse_args(argv)
    if args.batch_size < 1:
        ap.error("--batch-size must be at least 1")
    try:
        ctx = run_guard(pathlib.Path(args.helm_dir), run=run)
        if args.context and args.context != ctx:
            raise Refusal(f"--context {args.context} is not the asserted context {ctx}")
        kube = Kube(ctx, args.namespace, args.pod, args.db, args.user, run=run)
        if args.count:
            return step_count(kube)
        return step_write(kube, args.batch_size)
    except Refusal as r:
        print(f"REFUSED: {r.reason}", file=sys.stderr)
        if r.state:
            print(json.dumps(r.state, indent=2, sort_keys=True), file=sys.stderr)
        return EXIT_REFUSED


if __name__ == "__main__":
    sys.exit(main())
