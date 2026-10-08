-- Record the Fire PDU's munition expendable entity on effector_launch.
--
-- Stored as an asset_id, the same urn-is-asset_id identity launcher_asset_id
-- uses. NULL when the Fire names no distinct expendable (DIS 0:0:0). Lets
-- interior code find a munition's launcher from this table instead of
-- parsing the munition's id.
ALTER TABLE "public"."effector_launch"
  ADD COLUMN "munition_asset_id" text NULL;

CREATE INDEX "idx_effector_launch_munition_asset_id"
  ON "public"."effector_launch" ("munition_asset_id");
