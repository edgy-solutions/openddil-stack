"""The three ADR-0050 tables must be declared, migrated, and published."""
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
HCL = (ROOT / "schema" / "schema.hcl").read_text(encoding="utf-8")
ELECTRIFY = (ROOT / "electric" / "electrify.sql").read_text(encoding="utf-8")
MIGRATION = (
    ROOT / "schema" / "migrations" / "20261015120000_asset_arrivals.sql"
).read_text(encoding="utf-8")
TABLES = ("asset_declaration", "asset_correlation", "asset_fielding")


def test_tables_declared_migrated_published():
    for t in TABLES:
        assert f'table "{t}"' in HCL
        assert f'CREATE TABLE "public"."{t}"' in MIGRATION
        assert f"ADD TABLE public.{t}" in ELECTRIFY


def test_source_check_matches_in_both():
    check = "source IN ('declare', 'expect-confirm')"
    assert MIGRATION.count(check) == 2
    assert HCL.count(check) == 2
