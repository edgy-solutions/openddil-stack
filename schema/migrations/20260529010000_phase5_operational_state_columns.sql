-- Phase 5: operational_state 3-axis breakout on telemetry_latest_state
-- Mirrors EntityTelemetryEvent.operational_state proto fields (ADR-0026).
-- Stored as nullable columns rather than a nested jsonb so the SPA can
-- render them in the Maintainer GROUND DIAGNOSTICS panel without JSON
-- parsing per row, and so future SQL filters on these axes are cheap.
ALTER TABLE "public"."telemetry_latest_state"
  ADD COLUMN "power_state" text NULL,
  ADD COLUMN "functional_mode" text NULL,
  ADD COLUMN "health_state" text NULL,
  ADD COLUMN "actively_receiving" boolean NULL,
  ADD COLUMN "actively_transmitting" boolean NULL;
