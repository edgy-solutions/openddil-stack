-- munition_track: one row per in-flight munition, keyed by the munition's own
-- DIS id (never an asset). Updated in place as samples arrive, so it is
-- excluded from pruning and TTL like effector_launch. Labelled from the
-- launcher once the Fire is joined (ADR-0049). `silent` is a track state on
-- this tier's own arrival clock (last_heard_at), never a launch outcome.
CREATE TABLE "public"."munition_track" (
  "munition_asset_id"  text NOT NULL,
  "launcher_asset_id"  text NULL,
  "event_urn"          text NULL,
  -- "k.d.c.cat.sub.spec.extra", same form as effector_launch.
  "munition_type"      text NOT NULL,
  "ecef_x"             double precision NOT NULL,
  "ecef_y"             double precision NOT NULL,
  "ecef_z"             double precision NOT NULL,
  "first_seen_at"      timestamptz NOT NULL,
  "last_sample_at"     timestamptz NOT NULL,
  "last_heard_at"      timestamptz NOT NULL,
  "terminal_state"     text NULL,
  "detonation_result"  int NULL,
  "terminated_at"      timestamptz NULL,
  "late_terminal"      boolean NOT NULL DEFAULT false,
  "edge_id"            text NULL,
  "region_id"          text NULL,
  "originator_nation"  text NULL,
  "releasable_to"      text[] NULL,
  "updated_at"         timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY ("munition_asset_id"),
  CONSTRAINT "munition_track_terminal_state_check" CHECK (terminal_state IN ('entity_impact', 'ground_impact', 'detonated', 'dud', 'other', 'silent'))
);
-- Create index "idx_munition_track_launcher_asset_id" to table: "munition_track"
CREATE INDEX "idx_munition_track_launcher_asset_id" ON "public"."munition_track" ("launcher_asset_id");
-- Create index "idx_munition_track_in_flight" to table: "munition_track"
CREATE INDEX "idx_munition_track_in_flight" ON "public"."munition_track" ("last_heard_at") WHERE terminal_state IS NULL;
