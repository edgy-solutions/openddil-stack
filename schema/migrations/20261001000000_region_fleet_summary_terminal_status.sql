-- ADR-0044 §3: terminal operational-status partitions on region_fleet_summary.
--
-- nominal/degraded/critical/non_operational now count only assets WITHOUT a
-- terminal operational-status claim; these three new columns count the
-- assets that DO have one. asset_count is the whole partition (buckets +
-- terminal), so a destroyed asset stays in asset_count -- it is counted, in
-- its own column, never folded into a severity bucket and never dropped.
--
-- The claim is made once, at the edge that owns the sensor
-- (sim-dis-mapping.yaml:306 sets operational_state.operational_status from
-- the appearance damage bits); every other tier -- including this table --
-- ADOPTS it from faust-regional's aggregator and never re-derives its own,
-- same discipline the existing severity buckets already follow.
--
-- Reporting status is deliberately NOT added here -- it is per-reader
-- (ADR-0044 §4), not a region-wide count; follow-up.
ALTER TABLE "public"."region_fleet_summary"
  ADD COLUMN "destroyed" integer NOT NULL DEFAULT 0,
  ADD COLUMN "deactivated" integer NOT NULL DEFAULT 0,
  ADD COLUMN "removed" integer NOT NULL DEFAULT 0;
