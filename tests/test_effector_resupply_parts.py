"""effector_resupply and parts_availability must be declared, migrated, and published to Electric."""
import pytest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
HCL = (ROOT / "schema" / "schema.hcl").read_text(encoding="utf-8")
ELECTRIFY = (ROOT / "electric" / "electrify.sql").read_text(encoding="utf-8")
MIGRATIONS = ROOT / "schema" / "migrations"
TABLES = ["effector_resupply", "parts_availability"]


@pytest.mark.parametrize("name", TABLES)
def test_table_in_schema_hcl(name):
    assert f'table "{name}"' in HCL


@pytest.mark.parametrize("name", TABLES)
def test_table_has_migration(name):
    sql = "\n".join(p.read_text(encoding="utf-8") for p in MIGRATIONS.glob("*.sql"))
    assert f'CREATE TABLE "public"."{name}"' in sql


@pytest.mark.parametrize("name", TABLES)
def test_table_in_electric_publication(name):
    assert f"public.{name}" in ELECTRIFY
    assert f"tablename = '{name}'" in ELECTRIFY
    assert f"ADD TABLE public.{name}" in ELECTRIFY
