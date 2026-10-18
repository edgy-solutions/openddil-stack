-- egress_delivered_events -- name the episode, asset, component and fault
-- code a recorded event belongs to.
--
-- The ledger knew a delivered event's case_id but not what it was about, so
-- a reader holding an asset and a fault code could not find the case. The
-- forwarder now writes these four columns, read from the event body through
-- the kind's declared pointers (x-openddil.episode and .episode_id). A kind
-- with no episode block, or a pointer that does not resolve, leaves the
-- column NULL; an upsert never overwrites a stored value with NULL. Rows
-- written before this migration stay NULL; nothing backfills.
ALTER TABLE "public"."egress_delivered_events"
  ADD COLUMN "episode_id" text NULL,
  ADD COLUMN "asset_id" text NULL,
  ADD COLUMN "component" text NULL,
  ADD COLUMN "fault_code" text NULL;

CREATE INDEX "egress_delivered_events_route_asset_idx"
  ON "public"."egress_delivered_events" ("route", "asset_id");
