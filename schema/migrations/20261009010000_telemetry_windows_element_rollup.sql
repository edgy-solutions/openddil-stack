-- Asset-level rollup of the per-element snapshot, derived at the owning edge
-- and carried inside the window it already publishes. NULL = the asset has no
-- element profile.
ALTER TABLE "public"."asset_telemetry_windows" ADD COLUMN "element_rollup" jsonb NULL;
