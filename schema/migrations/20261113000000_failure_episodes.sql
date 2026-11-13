-- failure_episodes: closed manual-discrepancy episodes, projected at the tier
-- (ADR-0051 first slice, item 2). One row per closed episode; episode_id is the
-- episode's discrepancy_id. originator_nation / releasable_to are the ADR-0029
-- labels (empty releasable_to = released to no other nation, section 7);
-- edge_id names the producing tier. Usage-meter columns (rounds / moves at
-- open and close) are ADR-0051's and are out of this increment. No task id or
-- task status is carried here.
CREATE TABLE "public"."failure_episodes" (
  "episode_id" text NOT NULL,
  "detected_at" timestamptz NOT NULL,
  "asset_id" text NOT NULL,
  "component" text NOT NULL DEFAULT '',
  "fault_code" text NOT NULL DEFAULT '',
  "cleared_at" timestamptz NOT NULL,
  "cleared_by" text NOT NULL DEFAULT '',
  "resolution" text NOT NULL DEFAULT '',
  "sources" jsonb NOT NULL DEFAULT '[]',
  "baseline_id" text NOT NULL DEFAULT '',
  "mod_compliance" jsonb NOT NULL DEFAULT '[]',
  "originator_nation" text NOT NULL,
  "releasable_to" text[] NOT NULL DEFAULT '{}',
  "edge_id" text NOT NULL DEFAULT '',
  "region_id" text NULL,
  "updated_at" timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY ("episode_id", "detected_at")
);
-- Create index "idx_failure_episodes_asset_id" to table: "failure_episodes"
CREATE INDEX "idx_failure_episodes_asset_id" ON "public"."failure_episodes" ("asset_id");
