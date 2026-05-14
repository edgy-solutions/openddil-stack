-- Modify "asset_cm_state" table
ALTER TABLE "public"."asset_cm_state" ADD COLUMN "edge_id" text NOT NULL DEFAULT 'edge-01', ADD COLUMN "region_id" text NOT NULL DEFAULT 'region-01';
-- Modify "asset_logistics_status" table
ALTER TABLE "public"."asset_logistics_status" ADD COLUMN "edge_id" text NOT NULL DEFAULT 'edge-01', ADD COLUMN "region_id" text NOT NULL DEFAULT 'region-01';
-- Modify "asset_telemetry_windows" table
ALTER TABLE "public"."asset_telemetry_windows" ADD COLUMN "edge_id" text NOT NULL DEFAULT 'edge-01', ADD COLUMN "region_id" text NOT NULL DEFAULT 'region-01';
-- Modify "tactical_events" table
ALTER TABLE "public"."tactical_events" ADD COLUMN "edge_id" text NOT NULL DEFAULT 'edge-01', ADD COLUMN "region_id" text NOT NULL DEFAULT 'region-01';
-- Modify "telemetry_latest_state" table
ALTER TABLE "public"."telemetry_latest_state" ADD COLUMN "edge_id" text NOT NULL DEFAULT 'edge-01', ADD COLUMN "region_id" text NOT NULL DEFAULT 'region-01';
