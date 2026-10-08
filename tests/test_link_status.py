"""link_status must be declared, migrated, and published to Electric."""
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
HCL = (ROOT / "schema" / "schema.hcl").read_text(encoding="utf-8")
ELECTRIFY = (ROOT / "electric" / "electrify.sql").read_text(encoding="utf-8")
MIGRATIONS = ROOT / "schema" / "migrations"


def test_link_status_in_schema_hcl():
    assert 'table "link_status"' in HCL


def test_link_status_has_migration():
    sql = "\n".join(p.read_text(encoding="utf-8") for p in MIGRATIONS.glob("*.sql"))
    assert 'CREATE TABLE "public"."link_status"' in sql


def test_link_status_in_electric_publication():
    assert "public.link_status" in ELECTRIFY
    assert "tablename = 'link_status'" in ELECTRIFY
    assert "ADD TABLE public.link_status" in ELECTRIFY
