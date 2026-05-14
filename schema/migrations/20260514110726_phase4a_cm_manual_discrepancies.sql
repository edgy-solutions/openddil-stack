-- Modify "asset_cm_state" table
ALTER TABLE "public"."asset_cm_state" ADD COLUMN "manual_discrepancies" jsonb NOT NULL DEFAULT '[]';
