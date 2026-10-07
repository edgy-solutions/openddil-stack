-- ADR-0044 amendment ("posture, a third column"). Independent of the two
-- lifecycle columns 20260927000000 added: a moving launcher is still fully
-- operational and fully reporting. Decided once, at the edge tier that owns
-- the sensor, by a per-asset state machine; every tier (this table included)
-- stores the decided value and none re-derives it. Does not feed readiness.
ALTER TABLE "public"."telemetry_latest_state"
  ADD COLUMN "posture_status" text NOT NULL DEFAULT 'unspecified',
  ADD COLUMN "posture_since" timestamptz NULL,
  ADD CONSTRAINT "telemetry_latest_state_posture_status_check"
    CHECK (posture_status IN ('unspecified', 'emplaced', 'march_ordered', 'moving', 'emplacing'));
