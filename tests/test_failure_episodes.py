"""failure_episodes (ADR-0051 first slice, item 2): declared, migrated, and
the slice stays inside its fence (no other table, no ledger change, no task
columns)."""
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
MIG_DIR = ROOT / "schema" / "migrations"
HCL = (ROOT / "schema" / "schema.hcl").read_text(encoding="utf-8")
MIGRATION = (MIG_DIR / "20261113000000_failure_episodes.sql").read_text(encoding="utf-8")
COLUMNS = (
    "episode_id", "detected_at", "asset_id", "component", "fault_code",
    "cleared_at", "cleared_by", "resolution", "sources", "baseline_id",
    "mod_compliance", "originator_nation", "releasable_to", "edge_id",
    "region_id", "body_sha256", "updated_at",
)
WITHHELD = (
    "body_sha256", "stage", "reason", "asset_id", "episode_id", "detected_at",
    "body", "edge_id", "region_id", "first_seen_at", "last_seen_at", "times_seen",
)
FLOOR = "20261031000000"


def _hcl_block():
    start = HCL.index('table "failure_episodes" ')
    nxt = HCL.find('\ntable "', start + 1)
    return HCL[start:] if nxt == -1 else HCL[start:nxt]


def _withheld_block():
    start = HCL.index('table "failure_episodes_withheld"')
    nxt = HCL.find('\ntable "', start + 1)
    return HCL[start:] if nxt == -1 else HCL[start:nxt]


def test_table_declared_and_migrated():
    assert 'CREATE TABLE "public"."failure_episodes" (' in MIGRATION
    block = _hcl_block()
    for c in COLUMNS:
        assert f'"{c}" ' in MIGRATION, c
        assert f'column "{c}"' in block, c
    assert block.count("  column ") == len(COLUMNS)


def test_withheld_table_declared_and_migrated():
    assert 'CREATE TABLE "public"."failure_episodes_withheld" (' in MIGRATION
    block = _withheld_block()
    for c in WITHHELD:
        assert f'"{c}" ' in MIGRATION, c
        assert f'column "{c}"' in block, c
    assert block.count("  column ") == len(WITHHELD)
    assert 'PRIMARY KEY ("body_sha256", "stage", "reason")' in MIGRATION
    assert "columns = [column.body_sha256, column.stage, column.reason]" in block
    assert '"idx_failure_episodes_withheld_asset_id"' in MIGRATION


def test_primary_key():
    assert 'PRIMARY KEY ("episode_id", "detected_at")' in MIGRATION
    assert "columns = [column.episode_id, column.detected_at]" in _hcl_block()


def test_no_usage_meter_or_task_columns():
    for c in COLUMNS:
        assert not re.search(r"rounds|moves|task", c)
    assert not re.search(r'"[a-z_]*(rounds|moves|task)[a-z_]*" (text|int|jsonb|timestamptz)', MIGRATION)


def slice_violations(sql_texts):
    """Violations of the slice fence over migration texts newer than FLOOR."""
    out = []
    for text in sql_texts:
        for m in re.finditer(r'CREATE\s+TABLE\s+"public"\."(\w+)"', text, re.I):
            if m.group(1) not in ("failure_episodes", "failure_episodes_withheld"):
                out.append(f"CREATE TABLE {m.group(1)}")
        if re.search(r'ALTER\s+TABLE\s+"public"\."egress_delivered_events"', text, re.I):
            out.append("ALTER TABLE egress_delivered_events")
        for m in re.finditer(r'"(task_id|task_status|task_state)"', text):
            out.append(f"column {m.group(1)}")
    return out


def _real_texts():
    return [
        p.read_text(encoding="utf-8")
        for p in sorted(MIG_DIR.glob("*.sql"))
        if p.name.split("_")[0] > FLOOR
    ]


def test_real_tree_has_no_violations():
    texts = _real_texts()
    assert texts
    assert slice_violations(texts) == []


def test_red_ledger_alter_and_new_table_reported():
    alter = 'ALTER TABLE "public"."egress_delivered_events" ADD COLUMN "task_status" text NULL;'
    v = slice_violations(_real_texts() + [alter])
    assert "ALTER TABLE egress_delivered_events" in v
    assert "column task_status" in v
    create = 'CREATE TABLE "public"."episode_tasks" ("id" text NOT NULL);'
    assert "CREATE TABLE episode_tasks" in slice_violations(_real_texts() + [create])


def test_red_task_id_on_withheld_reported():
    planted = 'ALTER TABLE "public"."failure_episodes_withheld" ADD COLUMN "task_id" text NULL;'
    assert "column task_id" in slice_violations(_real_texts() + [planted])
