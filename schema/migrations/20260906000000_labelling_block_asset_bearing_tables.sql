-- Labelling block — the four asset-bearing tables Arc 1 deferred.
--
-- WHAT THIS FINISHES
-- The Arc-1 migration (20260807000000) labelled five tables and named its own
-- deferral in as many words:
--
--     Deliberately NOT labelled in this migration:
--       * region_* rollup tables -- ... an ADR-0029 Slice-2/3 question
--       * inventory_items, edge_buffer_status, tactical_events, audit_log --
--         not part of the Slice-1 read-path filter surface. Additive later.
--
-- This is "additive later". It was brought forward by a measurement rather
-- than a schedule: on 2026-09-06, probing every served table through the
-- gateway with a fully-entitled session returned 200 for exactly the five
-- labelled tables and 502 for the other nine. The releasability predicate
-- names columns those nine do not have, so Electric rejected every query and
-- the browser rendered a transport failure as an ABSENCE OF DATA. The alert
-- feed and the inventory panel had been empty for that reason, not for want
-- of events or items.
--
-- WHY ONLY FOUR OF THE NINE
-- Two of the nine got a better answer than labels, and it is worth recording
-- that labelling them would have been the wrong fix:
--
--   edge_buffer_status  Bridge lag, a severance flag, a probe result. NO
--                       asset data, so there is nothing to partition BY.
--                       Serving it under a nation filter would mean
--                       inventing a nationality for a link. It is
--                       ROLE-SERVED: authenticated operators see topology.
--
--   audit_log           Partitioned by WHO, not by nation — a subject sees
--                       their own actions and an oversight role sees all.
--                       SUBJECT-SCOPED on `actor`.
--
-- The three region_* rollups remain deferred for the reason Arc 1 gave, and
-- it has not weakened: an aggregate over many assets of possibly mixed
-- nationality needs a composition rule, and the floor for that rule is now
-- recorded (intersection of contributing inputs' releasable_to; per-nation
-- rollups additive, never a relaxation) in the regional node's design. They
-- are labelled by that block, under that rule, not by this one.
--
-- WHY THESE FOUR CAN BE STAMPED MECHANICALLY
-- Each carries an asset_id, and each row derives from an asset that is
-- ALREADY labelled. So stamping is PROPAGATION of an existing decision, not
-- a new one — the same relationship the projector already maintains for
-- telemetry_latest_state. No policy question is being answered here, which
-- is precisely why it can be done without one being decided.
--
-- NULLABLE, and for the same reason Arc 1 gave: purely additive, deployable
-- ahead of the stamping code, no backfill required to apply. Deny-unlabeled
-- means existing rows are releasable to nobody until a producer re-stamps
-- them — which is the safe direction, and the completeness gate is what says
-- when it has finished happening.
--
-- asset_registry is included though it is absent from schema.hcl (it was
-- created by 20260604202813 and never added to the declarative file). That
-- drift predates this migration and is NOT repaired here; repairing it in
-- the same change would mix a schema correction into a labelling step and
-- make both harder to revert.

BEGIN;

ALTER TABLE "tactical_events"
    ADD COLUMN IF NOT EXISTS "originator_nation" text   NULL,
    ADD COLUMN IF NOT EXISTS "releasable_to"     text[] NULL;

ALTER TABLE "inventory_items"
    ADD COLUMN IF NOT EXISTS "originator_nation" text   NULL,
    ADD COLUMN IF NOT EXISTS "releasable_to"     text[] NULL;

ALTER TABLE "asset_telemetry_windows"
    ADD COLUMN IF NOT EXISTS "originator_nation" text   NULL,
    ADD COLUMN IF NOT EXISTS "releasable_to"     text[] NULL;

ALTER TABLE "asset_registry"
    ADD COLUMN IF NOT EXISTS "originator_nation" text   NULL,
    ADD COLUMN IF NOT EXISTS "releasable_to"     text[] NULL;

COMMIT;
