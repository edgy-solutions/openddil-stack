"""Priority + divergence rules for db.upsert_observation.

These are the load-bearing semantics from ADR-0028:

  * static > connection > position > unspecified  (lower wins)
  * existing higher-or-equal priority is NEVER overwritten by a re-
    observation. Only divergent + last_observed_at + observed_edge_id
    refresh.
  * existing strictly lower priority IS replaced (e.g. when a new
    static assignment from the warfighter UI beats a previous
    position-derived assignment)
  * divergent = (assigned edge_id != observed edge_id), computed at
    write time on EVERY observation -- it's a flag, not a frozen
    derived value.

Wrong behavior here = silent override of warfighter intent OR silent
loss of divergence signal. Worth testing carefully.
"""
from __future__ import annotations
import pytest

from asset_registry_service import db


# ---------------------------------------------------------------------------
# Case 1: no existing row -> INSERT
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_first_insert_position_source(fake_pool_empty):
    """First observation creates the row with the proposed assignment."""
    _, conn = fake_pool_empty
    conn.seeded_row = None  # no existing

    row = await db.upsert_observation(
        asset_id="ASSET-A",
        observed_edge_id="edge-01",
        proposed_edge_id="edge-01",
        proposed_region_id="region-east",
        proposed_source="position",
        proposed_by="edge_assignment.yaml/nearest_fob",
    )

    assert row.asset_id == "ASSET-A"
    assert row.edge_id == "edge-01"
    assert row.region_id == "region-east"
    assert row.assignment_source == "position"
    assert row.divergent is False
    # SQL: one INSERT happened
    assert len(conn.executed) == 1
    sql, args = conn.executed[0]
    assert "INSERT INTO asset_registry" in sql
    assert args[0] == "ASSET-A"
    assert args[3] == "position"  # assignment_source positional


@pytest.mark.asyncio
async def test_first_insert_divergent_when_proposed_vs_observed_differ(
    fake_pool_empty,
):
    """First-insert path also computes divergent. If position-based
    assignment says edge-01 but we observed via edge-02 (rare but
    possible -- e.g. asset moved + edge subscribed to both), the row
    is created with divergent=true so the maintainer sees it."""
    _, conn = fake_pool_empty
    row = await db.upsert_observation(
        asset_id="ASSET-A",
        observed_edge_id="edge-02",   # observed somewhere
        proposed_edge_id="edge-01",   # assigned somewhere else
        proposed_region_id="region-east",
        proposed_source="position",
        proposed_by="edge_assignment.yaml/nearest_fob",
    )
    assert row.divergent is True
    assert row.edge_id == "edge-01"          # assignment held
    assert row.observed_edge_id == "edge-02" # observation captured


# ---------------------------------------------------------------------------
# Case 2: existing higher priority -> KEEP existing, refresh observation
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_existing_static_beats_proposed_position(fake_pool_existing):
    """ADR-0028 load-bearing rule: warfighter's static assignment wins
    even when re-observation suggests a different edge. The registry
    NEVER overrides the static -- it just flags divergent so the
    warfighter sees it."""
    _, conn = fake_pool_existing
    conn.seeded_row = {
        "edge_id": "edge-01",
        "region_id": "region-east",
        "assignment_source": "static",   # warfighter-set, highest priority
    }

    row = await db.upsert_observation(
        asset_id="ASSET-A",
        observed_edge_id="edge-02",      # observed actually flowing through edge-02
        proposed_edge_id="edge-02",      # position-based assignment agrees with observation
        proposed_region_id="region-west",
        proposed_source="position",      # LOWER priority than static
        proposed_by="edge_assignment.yaml/nearest_fob",
    )

    # Existing static assignment held
    assert row.edge_id == "edge-01"
    assert row.region_id == "region-east"
    assert row.assignment_source == "static"
    # Divergence detected vs the static assignment
    assert row.divergent is True
    assert row.observed_edge_id == "edge-02"

    # SQL: a "keep existing, refresh observation" UPDATE, NOT a
    # full-replacement UPDATE. The keep-path SETs observation columns
    # only; the replace-path also SETs edge_id/region_id/source. Use
    # the absence of "region_id = " in the SET clause as the signal
    # (region_id is unique to the replace-path SET, and doesn't appear
    # as a substring of any keep-path column name).
    assert len(conn.executed) == 1
    sql, _ = conn.executed[0]
    assert "UPDATE asset_registry" in sql
    assert "region_id = " not in sql          # NOT replacing the assignment
    assert "assignment_source = " not in sql  #   "         "      "
    assert "last_observed_at = now()" in sql  # observation columns refreshed
    assert "divergent = $3" in sql            #   "          "        "


@pytest.mark.asyncio
async def test_existing_same_priority_position_kept(fake_pool_existing):
    """Two position-based observations for the same asset: existing
    wins (same priority, ties go to incumbent -- this avoids edge-
    flapping when an asset hovers between two FOB Voronoi cells)."""
    _, conn = fake_pool_existing
    conn.seeded_row = {
        "edge_id": "edge-01",
        "region_id": "region-east",
        "assignment_source": "position",
    }
    row = await db.upsert_observation(
        asset_id="ASSET-A",
        observed_edge_id="edge-01",
        proposed_edge_id="edge-02",      # nearest-FOB now says edge-02
        proposed_region_id="region-west",
        proposed_source="position",
        proposed_by="edge_assignment.yaml/nearest_fob",
    )
    # Existing position assignment held
    assert row.edge_id == "edge-01"
    assert row.assignment_source == "position"
    # divergence vs observation? observed=edge-01, kept=edge-01, NOT divergent.
    assert row.divergent is False


# ---------------------------------------------------------------------------
# Case 3: existing lower priority -> REPLACE
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_new_static_replaces_existing_position(fake_pool_existing):
    """Warfighter pushes a new static assignment via admin API ->
    table already has a position-derived row -> static wins, the
    assignment_source / edge_id / region_id all update."""
    _, conn = fake_pool_existing
    conn.seeded_row = {
        "edge_id": "edge-02",
        "region_id": "region-west",
        "assignment_source": "position",  # LOWER priority
    }
    row = await db.upsert_observation(
        asset_id="ASSET-A",
        observed_edge_id="edge-02",
        proposed_edge_id="edge-01",       # static says edge-01
        proposed_region_id="region-east",
        proposed_source="static",         # HIGHER priority -> replaces
        proposed_by="warfighter-ui",
    )
    assert row.edge_id == "edge-01"
    assert row.region_id == "region-east"
    assert row.assignment_source == "static"
    # observed=edge-02, assigned=edge-01 -> divergent
    assert row.divergent is True

    # SQL: full-replacement UPDATE; SET clause includes edge_id +
    # region_id + assignment_source.
    sql, args = conn.executed[0]
    assert "UPDATE asset_registry" in sql
    assert "edge_id = $2" in sql
    assert "region_id = $3" in sql
    assert "assignment_source = $4" in sql
    assert "assigned_at = now()" in sql


# ---------------------------------------------------------------------------
# Case 4: unknown assignment_source -> falls back to lowest priority
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_unspecified_source_loses_to_anything(fake_pool_existing):
    """Asset whose only assignment is 'unspecified' must yield to a
    later position-based observation -- otherwise the unspecified
    fallback becomes a permanent sink no real assignment can dislodge."""
    _, conn = fake_pool_existing
    conn.seeded_row = {
        "edge_id": "edge-unspecified",
        "region_id": "region-unspecified",
        "assignment_source": "unspecified",
    }
    row = await db.upsert_observation(
        asset_id="ASSET-A",
        observed_edge_id="edge-01",
        proposed_edge_id="edge-01",
        proposed_region_id="region-east",
        proposed_source="position",       # beats unspecified
        proposed_by="edge_assignment.yaml/nearest_fob",
    )
    assert row.edge_id == "edge-01"
    assert row.assignment_source == "position"
    assert row.divergent is False


# ---------------------------------------------------------------------------
# Sanity: priority order matches ADR-0028 exactly
# ---------------------------------------------------------------------------

def test_priority_table_matches_adr():
    """Catches accidental reordering of _PRIORITY (a silent way to
    break ADR-0028 semantics if someone bumps the dict)."""
    p = db._PRIORITY
    assert p["static"] < p["connection"]
    assert p["connection"] < p["position"]
    assert p["position"] < p["unspecified"]
    # Exactly four sources -- adding one means the CHECK constraint
    # in the migration also needs updating in lockstep.
    assert set(p.keys()) == {"static", "connection", "position", "unspecified"}
