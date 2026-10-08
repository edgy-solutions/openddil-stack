-- Record which part of its platform a telemetry record describes.
--
-- Declared by the boundary mapper (AssetIdentity.subsystem), stored as the
-- enum name in text, the same way force_id / power_state are. NULL = not
-- declared = the record describes the platform itself. Interior code reads
-- this column instead of parsing the asset_id suffix (ADR-0047).
ALTER TABLE "public"."telemetry_latest_state"
  ADD COLUMN "subsystem" text NULL;
