-- ADR-0044 §4 rollup: a per-tier, per-releasability-partition view over
-- telemetry_latest_state's two lifecycle columns (operational_status,
-- reporting_status — slice 1, this repo's 20260927000000 migration).
--
-- GROUPED BY (region_id, originator_nation, releasable_to), not just
-- region_id. This is the same partitioning discipline 20260908000000 laid
-- down for region_fleet_summary/top_factors/wear_trends, applied for a
-- different reason here: those three tables compose ACROSS assets of
-- possibly mixed nationality and so need an intersection rule computed
-- upstream. This view never composes across a releasability boundary at
-- all -- GROUP BY puts every asset that already carries the same
-- (originator_nation, releasable_to) pair into the same row, so each row's
-- counts describe a single, already-homogeneous releasability partition.
-- There is no cross-partition arithmetic inside this view for a PEP to get
-- wrong.
--
-- WHAT THIS VIEW DOES NOT DO: it does not sum across rows to produce a
-- fleet-wide total. Two rows for the same region_id but different
-- releasable_to values are two different viewers' worlds (ADR-0043 §4); a
-- viewer entitled to one is not entitled to the other, and adding their
-- fleet_total columns together would manufacture a number no single viewer
-- is allowed to see. Any fleet-wide rollup belongs in a consumer that has
-- already applied the PEP and is summing only the rows one particular
-- viewer was released.
--
-- NULL originator_nation / releasable_to is its own group, same as any
-- other GROUP BY -- an unlabelled asset's row is real and reflects
-- deny-unlabeled's own definition of "releasable to no one" (see
-- 20260908000000's note on why a hidden row still matters); it is not
-- merged into or hidden by a labelled row.
--
-- reporting/not_reporting counts operational_status and reporting_status
-- independently (ADR-0044 §1) -- a row can be simultaneously "destroyed"
-- and "reporting" for one more tick before the staleness sweep catches up,
-- and this view does not collapse that distinction either.

BEGIN;

CREATE VIEW "public"."asset_lifecycle_summary" AS
SELECT
    "region_id",
    "originator_nation",
    "releasable_to",
    COUNT(*)                                                       AS "fleet_total",
    COUNT(*) FILTER (WHERE "operational_status" = 'operational')   AS "operational",
    COUNT(*) FILTER (WHERE "operational_status" = 'destroyed')     AS "destroyed",
    COUNT(*) FILTER (WHERE "operational_status" = 'deactivated')   AS "deactivated",
    COUNT(*) FILTER (WHERE "operational_status" = 'removed')       AS "removed",
    COUNT(*) FILTER (WHERE "reporting_status" = 'reporting')       AS "reporting",
    COUNT(*) FILTER (WHERE "reporting_status" = 'not_reporting')   AS "not_reporting"
FROM "public"."telemetry_latest_state"
GROUP BY "region_id", "originator_nation", "releasable_to";

COMMIT;
