-- ADR-0044 lifecycle slice 1: two independent, timestamped status columns
-- on the asset row. `operational_status` moves only on a signal about the
-- asset (e.g. DIS damage=DESTROYED); `reporting_status` moves only on the
-- arrival/non-arrival of records, per reading tier. Kept as two columns
-- (not folded into `health_state`) because Finding 1 is that a single
-- overloaded field cannot represent "destroyed and still reporting" --
-- the exact second an asset tells you it was hit.
ALTER TABLE "public"."telemetry_latest_state"
  ADD COLUMN "operational_status" text NOT NULL DEFAULT 'operational',
  ADD COLUMN "operational_status_at" timestamptz NULL,
  ADD COLUMN "reporting_status" text NOT NULL DEFAULT 'reporting',
  ADD COLUMN "reporting_status_at" timestamptz NULL;
