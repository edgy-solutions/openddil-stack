-- Create "asset_capability_state" table
-- Recipe v3 Sub-phase E: capability-snapshot projector target. One row per
-- asset holding the latest StrikeCapabilityMessage snapshot; capabilities is
-- the per-store array stored verbatim as JSONB.
CREATE TABLE "public"."asset_capability_state" (
  "asset_id" text NOT NULL,
  "capabilities" jsonb NOT NULL DEFAULT '[]',
  "schema_version" text NULL,
  "mode" text NULL,
  "edge_id" text NOT NULL DEFAULT 'edge-01',
  "region_id" text NOT NULL DEFAULT 'region-01',
  "observed_at" timestamptz NULL,
  "updated_at" timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY ("asset_id")
);
