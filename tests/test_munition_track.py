"""munition_track must be declared, migrated, published to Electric, and its
terminal vocabulary must match the projector's (ADR-0049)."""
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
HCL = (ROOT / "schema" / "schema.hcl").read_text(encoding="utf-8")
ELECTRIFY = (ROOT / "electric" / "electrify.sql").read_text(encoding="utf-8")
MIGRATION = (
    ROOT / "schema" / "migrations" / "20261015000000_munition_track.sql"
).read_text(encoding="utf-8")
# Detonation outcomes (the effector_launch map) plus the sweep's own `silent`.
PROJECTOR_STATES = {"entity_impact", "ground_impact", "detonated", "dud", "other", "silent"}


def _states(text):
    m = re.search(r"terminal_state IN \(([^)]*)\)", text)
    assert m, "no terminal_state check found"
    return set(re.findall(r"'([a-z_]+)'", m.group(1)))


def test_table_in_schema_hcl():
    assert 'table "munition_track"' in HCL


def test_table_has_migration():
    assert 'CREATE TABLE "public"."munition_track"' in MIGRATION
    assert "WHERE terminal_state IS NULL" in MIGRATION


def test_table_in_electric_publication():
    assert "public.munition_track" in ELECTRIFY
    assert "tablename = 'munition_track'" in ELECTRIFY
    assert "ADD TABLE public.munition_track" in ELECTRIFY


def test_check_vocabulary_matches_projector():
    assert _states(MIGRATION) == PROJECTOR_STATES
    block = HCL[HCL.index('table "munition_track"'):]
    assert _states(block) == PROJECTOR_STATES
