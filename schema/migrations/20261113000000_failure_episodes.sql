-- failure_episodes: closed manual-discrepancy episodes, projected at the tier
-- (ADR-0051 first slice, item 2). One row per closed episode; episode_id is the
-- episode's discrepancy_id. originator_nation / releasable_to are the ADR-0029
-- labels (empty releasable_to = released to no other nation, section 7);
-- edge_id names the producing tier. Usage-meter columns (rounds / moves at
-- open and close) are ADR-0051's and are out of this increment. No task id or
-- task status is carried here.
--
-- Atlas applies migrations with --tx-mode none, so this file carries its own
-- transaction. The lock and statement timeouts are SET LOCAL and end at COMMIT.

BEGIN;
SET LOCAL lock_timeout = '5s';
SET LOCAL statement_timeout = '60s';

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
  -- Required, no default; {} is written only when the record says released to
  -- no other nation (ADR-0029 section 7, ADR-0055 section 1a).
  "releasable_to" text[] NOT NULL,
  -- lk1 label key and body per DESIGN-2026-11-06 section 1.2, compiled at write
  -- by the projector with openddil_labels. Both NULL when there is no key, which
  -- means no label_policy row; an unlabelled close is refused before it gets here.
  "label_key" text NULL,
  "label" jsonb NULL,
  "edge_id" text NOT NULL DEFAULT '',
  "region_id" text NULL,
  -- sha256 hex of the canonical JSON of the record as first written; a replay
  -- is compared against it and never overwrites.
  "body_sha256" text NOT NULL,
  "updated_at" timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY ("episode_id", "detected_at"),
  CONSTRAINT "failure_episodes_label_key_form" CHECK ("label_key" IS NULL OR "label_key" ~ '^lk1:[0-9a-f]{64}$'),
  CONSTRAINT "failure_episodes_label_pair" CHECK (("label_key" IS NULL) = ("label" IS NULL))
);
-- Create index "idx_failure_episodes_asset_id" to table: "failure_episodes"
CREATE INDEX "idx_failure_episodes_asset_id" ON "public"."failure_episodes" ("asset_id");
-- Create index "idx_failure_episodes_label_key" to table: "failure_episodes"
CREATE INDEX "idx_failure_episodes_label_key" ON "public"."failure_episodes" ("label_key");
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

COMMIT;
