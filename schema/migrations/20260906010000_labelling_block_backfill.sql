-- Labelling block, part 2 — backfill the rows that already exist.
--
-- SEPARATE FROM THE SCHEMA CHANGE ON PURPOSE. 20260906000000 adds columns
-- and is trivially revertible; this changes data and is not. Mixing them
-- would make the pair revertible only as a unit, and the schema half is the
-- one you want back quickly if something is wrong.
--
-- WHY A BACKFILL IS NEEDED AT ALL, when the producers now stamp:
-- `tactical_events` is an APPEND log, not a projection. Its rows are never
-- rewritten, so newly-taught producers only label rows written from now on.
-- And the events themselves are sparse by design — fusion emits only on an
-- UPWARD transition into an alerting severity, so on a fleet already sitting
-- at CRITICAL there may be no new events for a long time.
--
-- Enabling deny-unlabeled over the existing rows without this would trade a
-- refusal banner for a permanently empty alert feed. Both are honest; the
-- second is worse, because "refused" is visible and "empty" is not.
--
-- WHY THIS IS PROPAGATION AND NOT A NEW DECISION
-- Every tactical event carries `subject` = the asset it is about, and that
-- asset's labels were decided at ingress and already live on
-- telemetry_latest_state. An event about an asset is exactly as releasable
-- as the asset. This copies that decision; it does not make one.
--
-- Rows whose subject has no labelled asset are LEFT UNLABELLED, deliberately.
-- Deny-unlabeled then hides them, which is the safe direction, and the
-- completeness gate counts them so the remainder is visible rather than
-- assumed. Guessing a nation for an event whose asset we cannot identify
-- would be the one thing this whole path forbids.

BEGIN;

UPDATE "tactical_events" AS te
   SET "originator_nation" = tls."originator_nation",
       "releasable_to"     = COALESCE(tls."releasable_to", ARRAY[]::text[])
  FROM "telemetry_latest_state" AS tls
 WHERE te."subject" = tls."asset_id"
   AND tls."originator_nation" IS NOT NULL
   AND te."originator_nation" IS NULL;

-- asset_registry is asset-keyed too, and its writer does not stamp yet.
-- Backfilled from the same source for the same reason; the service is
-- taught separately, and until it is, new rows arrive unlabelled and the
-- gate counts them.
UPDATE "asset_registry" AS ar
   SET "originator_nation" = tls."originator_nation",
       "releasable_to"     = COALESCE(tls."releasable_to", ARRAY[]::text[])
  FROM "telemetry_latest_state" AS tls
 WHERE ar."asset_id" = tls."asset_id"
   AND tls."originator_nation" IS NOT NULL
   AND ar."originator_nation" IS NULL;

COMMIT;
