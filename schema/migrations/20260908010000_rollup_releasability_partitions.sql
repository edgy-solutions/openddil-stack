-- Partition the regional rollups by releasability class.
--
-- WHY ONE ROW PER REGION CANNOT WORK, measured rather than argued.
--
-- region-east's fourteen contributors fall into three effective-audience
-- classes: {ATL} x7, {BDR} x6, {ATL,BDR} x1. The intersection floor over
-- those is EMPTY, so the single composed row was releasable to nobody --
-- including the liaison, who is entitled to every one of the fourteen.
--
-- The floor is not wrong. The floor is the correct MINIMUM for one row, and
-- what it revealed is that one row cannot express the right access rule. The
-- right rule is "the subject may see every input": for each contributor, the
-- subject's nations overlap that contributor's audience. The liaison passes
-- it for all fourteen; an ATL subject fails it on the six BDR-only assets.
-- That is a PER-CONTRIBUTOR test, and no single releasable_to set encodes it
-- under overlap semantics -- the empty set denies everyone, the union leaks
-- to everyone, and there is nothing honest in between.
--
-- So: one partial per equivalence class of effective audience. Each partial
-- is an ORDINARY LABELLED ROW -- originator_nation NULL (a partial claims no
-- authorship either; its class is its audience, not its authorship), and
-- releasable_to set to its class. The §4 overlap predicate then works
-- unchanged, with no aggregate-specific access logic anywhere:
--
--   liaison (ATL,BDR) -> sees all three partials, presentation sums to 14
--   ATL subject       -> sees {ATL} and {ATL,BDR}, an honest ATL-scoped 8
--   BDR subject       -> sees {BDR} and {ATL,BDR}, an honest 7
--
-- and nobody is shown a number containing data they cannot see. More rows,
-- every one of them ordinary. That is the trade: aggregate-shaped access
-- rules replaced by more instances of the rule that already exists.
--
-- COMPOSITION ACROSS PARTIALS IS NOT UNIFORM, and this is GD-05 restated per
-- class rather than avoided. Counts SUM exactly: fleet-summary buckets over
-- disjoint contributor sets add. Top-N factors and wear trends DO NOT -- a
-- per-class top-10 merged with another per-class top-10 is not the top-10 of
-- the union, and a per-class mean is not the mean of the union without its
-- weight. Those merge LOSSILY at presentation and must be marked as such
-- where they are rendered; asset_count is carried per partial so a weighted
-- merge is at least possible for the means.

BEGIN;

ALTER TABLE "public"."region_fleet_summary"
    ADD COLUMN IF NOT EXISTS "releasability_class" text NOT NULL DEFAULT '';
ALTER TABLE "public"."region_top_factors"
    ADD COLUMN IF NOT EXISTS "releasability_class" text NOT NULL DEFAULT '';
ALTER TABLE "public"."region_wear_trends"
    ADD COLUMN IF NOT EXISTS "releasability_class" text NOT NULL DEFAULT '';

-- The class is a canonical rendering of the audience set: sorted, comma
-- joined, so {BDR,ATL} and {ATL,BDR} are one class and not two. The empty
-- string is the class of contributors releasable to nobody -- it can still
-- arise, from an unlabelled asset, and it must remain expressible rather
-- than collapse into "no class".

-- The old single-row-per-region rows cannot be reclassified: which
-- contributors produced them is not recoverable from the counts. They are
-- deleted rather than migrated, and the aggregator repopulates on its next
-- heartbeat within seconds. Same reasoning as the previous migration's
-- refusal to backfill -- leave the unrecoverable unrecovered -- but here the
-- rows would actively mislead, because a legacy row carries the whole
-- region's counts under a single class it never had.
DELETE FROM "public"."region_fleet_summary";
DELETE FROM "public"."region_top_factors";
DELETE FROM "public"."region_wear_trends";

ALTER TABLE "public"."region_fleet_summary" DROP CONSTRAINT IF EXISTS "region_fleet_summary_pkey";
ALTER TABLE "public"."region_fleet_summary" ADD PRIMARY KEY ("region_id", "releasability_class");

ALTER TABLE "public"."region_top_factors" DROP CONSTRAINT IF EXISTS "region_top_factors_pkey";
ALTER TABLE "public"."region_top_factors" ADD PRIMARY KEY ("region_id", "releasability_class");

ALTER TABLE "public"."region_wear_trends" DROP CONSTRAINT IF EXISTS "region_wear_trends_pkey";
ALTER TABLE "public"."region_wear_trends" ADD PRIMARY KEY ("region_id", "releasability_class");

COMMIT;
