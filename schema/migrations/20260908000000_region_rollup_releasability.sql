-- Releasability columns for the three regional rollups.
--
-- These were the LAST tables on the pending list. Every other asset-bearing
-- table got its columns in 20260906000000; the rollups were held back because
-- propagation does not answer the question they ask. An asset's labels come
-- from the adapter that knew the source's national origin. A rollup is one
-- number standing for many assets of possibly mixed nationality, so it needs a
-- COMPOSITION rule, and composing wrongly is the failure mode that matters:
-- union would show the aggregate to anyone entitled to any single contributor.
--
-- THE RULE IS INTERSECTION, and it is applied where the number is made -- in
-- the regional aggregator, over the same snapshot the counts come from -- not
-- here and not in the projector. This migration only makes room for the
-- answer.
--
-- WHY A HIDDEN ROW STILL MATTERS. A viewer who sees "14 assets, 5 critical"
-- has learned something about every asset that moved those counters, whether
-- or not they could see it individually. Intersection is what makes the
-- aggregate no more visible than its least visible input. The consequence is
-- deliberate: one ATL-only asset makes every rollup in that region ATL-only,
-- and one UNLABELLED asset makes it releasable to nobody -- because under
-- deny-unlabeled an unlabelled row is releasable to no one, and intersecting
-- with no-one gives no-one.
--
-- originator_nation IS ADDED BUT WILL STAY NULL FOR ROLLUPS. The column
-- exists so the three tables carry the same shape as every other labelled
-- table, and because the PEP's table-class test looks for both columns. It is
-- deliberately never populated: the PEP predicate is a DISJUNCTION, so a
-- non-null originator_nation ALONE grants access and would bypass the
-- intersection entirely while looking like a reasonable label. An aggregate
-- has no originator nation, and claiming one would be a claim.

BEGIN;

ALTER TABLE "public"."region_fleet_summary"
    ADD COLUMN IF NOT EXISTS "originator_nation" text   NULL,
    ADD COLUMN IF NOT EXISTS "releasable_to"     text[] NULL;

ALTER TABLE "public"."region_top_factors"
    ADD COLUMN IF NOT EXISTS "originator_nation" text   NULL,
    ADD COLUMN IF NOT EXISTS "releasable_to"     text[] NULL;

ALTER TABLE "public"."region_wear_trends"
    ADD COLUMN IF NOT EXISTS "originator_nation" text   NULL,
    ADD COLUMN IF NOT EXISTS "releasable_to"     text[] NULL;

-- NO BACKFILL, deliberately, and this is the one place the rollups differ
-- from the asset-bearing tables.
--
-- 20260906010000 backfilled tactical_events and asset_registry from
-- telemetry_latest_state, which was PROPAGATION: an event about an asset is
-- exactly as releasable as the asset, so copying the decision made none.
--
-- There is no equivalent here. A rollup's releasability is a function of the
-- SET of assets that produced it at the moment it was produced, and that set
-- is not recoverable from a row that only carries the counts. Backfilling
-- would mean inventing a composition over contributors we can no longer
-- enumerate.
--
-- So existing rows stay unlabelled and deny-unlabeled hides them until the
-- aggregator emits again -- which it does on its heartbeat, within seconds.
-- A brief empty rollup is the honest cost; a guessed label would be
-- permanent.

COMMIT;
