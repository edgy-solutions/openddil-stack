"""asset_telemetry_windows.element_rollup must be declared and migrated."""
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
HCL = (ROOT / "schema" / "schema.hcl").read_text(encoding="utf-8")
MIGRATIONS = ROOT / "schema" / "migrations"


def test_element_rollup_in_schema_hcl():
    start = HCL.index('table "asset_telemetry_windows"')
    end = HCL.index("\ntable ", start)
    assert 'column "element_rollup"' in HCL[start:end]


def test_element_rollup_has_migration():
    sql = "\n".join(p.read_text(encoding="utf-8") for p in MIGRATIONS.glob("*.sql"))
    assert (
        'ALTER TABLE "public"."asset_telemetry_windows" '
        'ADD COLUMN "element_rollup" jsonb NULL'
    ) in sql
