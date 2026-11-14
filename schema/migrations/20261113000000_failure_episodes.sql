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
  -- sha256 hex of the canonical JSON of the record as first written; a replay
  -- is compared against it and never overwrites.
  "body_sha256" text NOT NULL,
  "updated_at" timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY ("episode_id", "detected_at")
);
-- Create index "idx_failure_episodes_asset_id" to table: "failure_episodes"
CREATE INDEX "idx_failure_episodes_asset_id" ON "public"."failure_episodes" ("asset_id");
-- failure_episodes_withheld: failure-episode records refused at emit, backfill
-- or projection, kept with their reason (ADR-0055: withheld, not dropped).
-- Never pruned. Operator-only: not published to Electric and not in any PEP
-- read set. edge_id / region_id name the producing tier.
CREATE TABLE "public"."failure_episodes_withheld" (
  "body_sha256" text NOT NULL,
  "stage" text NOT NULL,
  "reason" text NOT NULL,
  "asset_id" text NOT NULL DEFAULT '',
  "episode_id" text NOT NULL DEFAULT '',
  "detected_at" timestamptz NULL,
  "body" jsonb NOT NULL,
  "edge_id" text NOT NULL DEFAULT '',
  "region_id" text NULL,
  "first_seen_at" timestamptz NOT NULL DEFAULT now(),
  "last_seen_at" timestamptz NOT NULL DEFAULT now(),
  "times_seen" bigint NOT NULL DEFAULT 1,
  PRIMARY KEY ("body_sha256", "stage", "reason")
);
-- Create index "idx_failure_episodes_withheld_asset_id" to table: "failure_episodes_withheld"
CREATE INDEX "idx_failure_episodes_withheld_asset_id" ON "public"."failure_episodes_withheld" ("asset_id");
