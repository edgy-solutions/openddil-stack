"""Postgres writer for asset_registry rows.

asyncpg pool + a single upsert helper. Kept narrow on purpose -- this
service writes one table; pulling in a full ORM would add weight
without buying anything.

Upsert semantics:
  - first observation of an asset -> INSERT with assignment_source +
    last_observed_at + observed_edge_id
  - subsequent observations -> UPDATE last_observed_at + observed_edge_id,
    AND recompute `divergent` against the existing assigned edge_id.
    The assignment_source / edge_id / region_id of an existing row are
    NOT overwritten by a re-observation -- that's the load-bearing
    "static wins" rule from ADR-0028. The asset-registry-service writes
    a new assignment_source ONLY when its computed assignment LOSES the
    priority comparison against the existing row (in which case it
    silently keeps the existing assignment) or matches (no change).
    Static-assigned rows are inserted out-of-band (admin API, future);
    asset-registry-service only ever sets assignment_source in
    ('connection', 'position', 'unspecified') itself.
"""
from __future__ import annotations
import asyncpg
import logging
from dataclasses import dataclass
from typing import Optional

log = logging.getLogger(__name__)


# Lower number wins. Used in UPSERT to decide whether the incoming
# observation should replace the existing assignment. Mirrors the
# priority order from ADR-0028.
_PRIORITY = {
    "static": 0,
    "connection": 1,
    "position": 2,
    "unspecified": 3,
}


@dataclass
class RegistryRow:
    """Shape of one asset_registry row -- what we write + what we
    publish to the changelog topic."""
    asset_id: str
    edge_id: str
    region_id: str
    assignment_source: str
    assigned_by: str
    observed_edge_id: Optional[str] = None
    divergent: bool = False
    platform_variant: str = ""


# Module-level pool; initialized once in init_pool().
_pool: Optional[asyncpg.Pool] = None


async def init_pool(dsn: str, min_size: int = 2, max_size: int = 8) -> None:
    """Create the pg pool. Idempotent -- safe to call multiple times in
    test setup. min/max defaults are conservative; the service does one
    upsert per asset-event so contention is low."""
    global _pool
    if _pool is not None:
        return
    _pool = await asyncpg.create_pool(
        dsn=dsn,
        min_size=min_size,
        max_size=max_size,
    )
    log.info("postgres pool ready (min=%d max=%d)", min_size, max_size)


async def close_pool() -> None:
    """Tear down the pool. Mainly for tests."""
    global _pool
    if _pool is not None:
        await _pool.close()
        _pool = None


def _pool_required() -> asyncpg.Pool:
    if _pool is None:
        raise RuntimeError("postgres pool not initialized; call init_pool() first")
    return _pool


async def upsert_observation(
    asset_id: str,
    observed_edge_id: str,
    proposed_edge_id: str,
    proposed_region_id: str,
    proposed_source: str,
    proposed_by: str,
    originator_nation: str = "",
    releasable_to: list | None = None,
    platform_variant: str = "",
) -> RegistryRow:
    """Apply one observation. Returns the resulting row (what's now in
    the table, which may NOT match what we proposed if a higher-priority
    assignment already exists).

    Args:
      asset_id:        the asset being observed
      observed_edge_id: the edge whose pipeline this observation arrived
                       through -- used for divergence detection AND as
                       the connection-based assignment input
      proposed_edge_id, proposed_region_id, proposed_source:
                       what THIS observation would assign to, if it
                       wins the priority comparison
      proposed_by:     audit field (e.g. "edge_assignment.yaml")
      originator_nation, releasable_to:
                       ADR-0029 labels from the OBSERVATION's provenance.
                       Stamped on INSERT and never re-derived. Absent labels
                       are written as NULL rather than as an empty claim --
                       "we were not told" must stay distinguishable from
                       "released to nobody", and the completeness gate counts
                       them differently.
      platform_variant: the asset's variant, as stated by this observation.
                       Unlike the labels above, this is RECORDED, not
                       stamped-once: a later observation that states a
                       non-empty variant overwrites what's stored, because
                       the registry is reporting the latest thing it was
                       told, not fixing a decision at first sight. An
                       observation that carries no variant ("") never
                       clears a variant the registry already has -- absence
                       of new information is not new information.

    Behavior:
      - No row exists -> INSERT with the proposed assignment.
      - Existing row has SAME or HIGHER priority assignment_source
        (lower _PRIORITY number) -> keep existing assignment. Only
        update last_observed_at, observed_edge_id, and divergent.
      - Existing row has LOWER priority -> replace (rare; happens when
        we acquire static input that beats a previous position-derived
        assignment).

    Divergent is computed at write time: divergent = (assigned edge_id
    != observed edge_id). This is the load-bearing piece per ADR-0028
    -- never override the assignment, just flag the divergence.
    """
    pool = _pool_required()
    proposed_prio = _PRIORITY[proposed_source]
    async with pool.acquire() as conn:
        # All-in-one upsert: try insert; on conflict, decide whether to
        # replace the assignment or just update the observation columns.
        # Implementation is intentionally explicit (two SQL paths,
        # selected by Python) rather than a clever ON CONFLICT clause --
        # easier to reason about + audit.
        existing = await conn.fetchrow(
            'SELECT edge_id, region_id, assignment_source, platform_variant '
            'FROM asset_registry WHERE asset_id = $1',
            asset_id,
        )
        if existing is None:
            # First observation: full insert.
            divergent = proposed_edge_id != observed_edge_id
            # LABELS ARE STAMPED AT INSERT. Until 2026-09-08 this service
            # wrote unlabelled rows and a one-off backfill covered what
            # existed, so the gate read zero unlabelled while every NEW asset
            # arrived unlabelled -- a clean gate over a leaking writer.
            #
            # The cost changed when regional rollups became composed by
            # releasability class: an unlabelled asset has an empty effective
            # audience, so it forms the '' class and is visible to nobody.
            # One such row no longer hides an asset, it removes it from every
            # audience's rollup.
            #
            # NULL, not '' / '{}', when the observation carries nothing: an
            # absent claim and a claim of nothing are different absences.
            await conn.execute(
                'INSERT INTO asset_registry '
                '(asset_id, edge_id, region_id, assignment_source, '
                ' assigned_by, last_observed_at, observed_edge_id, divergent, '
                ' originator_nation, releasable_to, platform_variant) '
                'VALUES ($1, $2, $3, $4, $5, now(), $6, $7, $8, $9, $10)',
                asset_id, proposed_edge_id, proposed_region_id,
                proposed_source, proposed_by, observed_edge_id, divergent,
                originator_nation or None,
                list(releasable_to) if releasable_to is not None else None,
                platform_variant or "",
            )
            return RegistryRow(
                asset_id=asset_id,
                edge_id=proposed_edge_id,
                region_id=proposed_region_id,
                assignment_source=proposed_source,
                assigned_by=proposed_by,
                observed_edge_id=observed_edge_id,
                divergent=divergent,
                platform_variant=platform_variant or "",
            )

        existing_source = existing["assignment_source"]
        existing_prio = _PRIORITY.get(existing_source, _PRIORITY["unspecified"])

        if proposed_prio < existing_prio:
            # Strictly higher priority wins (e.g. new static beats old
            # position). Replace the assignment + reset assigned_at.
            divergent = proposed_edge_id != observed_edge_id
            kept_variant = platform_variant or existing.get("platform_variant") or ""
            await conn.execute(
                'UPDATE asset_registry SET '
                '  edge_id = $2, region_id = $3, assignment_source = $4, '
                '  assigned_by = $5, assigned_at = now(), '
                '  last_observed_at = now(), observed_edge_id = $6, '
                '  divergent = $7, platform_variant = $8 '
                'WHERE asset_id = $1',
                asset_id, proposed_edge_id, proposed_region_id,
                proposed_source, proposed_by, observed_edge_id, divergent,
                kept_variant,
            )
            return RegistryRow(
                asset_id=asset_id,
                edge_id=proposed_edge_id,
                region_id=proposed_region_id,
                assignment_source=proposed_source,
                assigned_by=proposed_by,
                observed_edge_id=observed_edge_id,
                divergent=divergent,
                platform_variant=kept_variant,
            )

        # Existing assignment >= proposed priority -> keep it. Just
        # refresh observation timestamps + recompute divergence against
        # the existing edge_id.
        kept_edge_id = existing["edge_id"]
        kept_region_id = existing["region_id"]
        kept_variant = platform_variant or existing.get("platform_variant") or ""
        divergent = kept_edge_id != observed_edge_id
        await conn.execute(
            'UPDATE asset_registry SET '
            '  last_observed_at = now(), observed_edge_id = $2, divergent = $3, '
            '  platform_variant = $4 '
            'WHERE asset_id = $1',
            asset_id, observed_edge_id, divergent, kept_variant,
        )
        return RegistryRow(
            asset_id=asset_id,
            edge_id=kept_edge_id,
            region_id=kept_region_id,
            assignment_source=existing_source,
            assigned_by="",  # not refreshed in the keep path
            observed_edge_id=observed_edge_id,
            divergent=divergent,
            platform_variant=kept_variant,
        )
