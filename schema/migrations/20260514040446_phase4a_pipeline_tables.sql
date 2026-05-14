-- Create "asset_cm_state" table
CREATE TABLE "public"."asset_cm_state" (
  "asset_id" text NOT NULL,
  "baseline_id" text NULL,
  "lifecycle" text NOT NULL DEFAULT 'LIFECYCLE_UNSPECIFIED',
  "overall_status" text NOT NULL DEFAULT 'CONFIG_STATUS_UNSPECIFIED',
  "last_alerted_status" text NULL,
  "as_of" timestamptz NULL,
  "last_observed_at" timestamptz NULL,
  "installed" jsonb NOT NULL DEFAULT '[]',
  "mod_status" jsonb NOT NULL DEFAULT '[]',
  "discrepancies" jsonb NOT NULL DEFAULT '[]',
  "updated_at" timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY ("asset_id")
);
-- Create "asset_logistics_status" table
CREATE TABLE "public"."asset_logistics_status" (
  "asset_id" text NOT NULL,
  "platform_variant" text NULL,
  "overall_severity" text NOT NULL DEFAULT 'LOGISTICS_SEVERITY_UNSPECIFIED',
  "previous_severity" text NULL,
  "constraining_factors" jsonb NOT NULL DEFAULT '[]',
  "projected_mission_capable_remaining_seconds" integer NULL,
  "projected_time_to_next_constraint_seconds" integer NULL,
  "cm_baseline_id" text NULL,
  "status_revision" bigint NOT NULL DEFAULT 0,
  "computed_at" timestamptz NULL,
  "latest_telemetry_sample_time" timestamptz NULL,
  "is_transition" boolean NOT NULL DEFAULT false,
  "is_initial" boolean NOT NULL DEFAULT false,
  "updated_at" timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY ("asset_id")
);
-- Create "asset_telemetry_windows" table
CREATE TABLE "public"."asset_telemetry_windows" (
  "asset_id" text NOT NULL,
  "platform_variant" text NULL,
  "fluid_trends" jsonb NOT NULL DEFAULT '[]',
  "consumable_trends" jsonb NOT NULL DEFAULT '[]',
  "component_wear_trends" jsonb NOT NULL DEFAULT '[]',
  "window_duration_seconds" integer NULL,
  "sample_count" integer NULL,
  "computed_at" timestamptz NULL,
  "updated_at" timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY ("asset_id")
);
-- Create "audit_log" table
CREATE TABLE "public"."audit_log" (
  "id" uuid NOT NULL DEFAULT gen_random_uuid(),
  "entity_type" character varying(100) NOT NULL,
  "entity_id" uuid NOT NULL,
  "action" character varying(50) NOT NULL,
  "payload" jsonb NULL,
  "actor" character varying(255) NULL,
  "occurred_at" timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY ("id")
);
-- Create index "idx_audit_log_entity" to table: "audit_log"
CREATE INDEX "idx_audit_log_entity" ON "public"."audit_log" ("entity_type", "entity_id");
-- Create "inventory_items" table
CREATE TABLE "public"."inventory_items" (
  "id" uuid NOT NULL DEFAULT gen_random_uuid(),
  "name" character varying(255) NOT NULL,
  "available_count" integer NOT NULL DEFAULT 0,
  "allocated_count" integer NOT NULL DEFAULT 0,
  "created_at" timestamptz NOT NULL DEFAULT now(),
  "updated_at" timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY ("id")
);
-- Create "tactical_events" table
CREATE TABLE "public"."tactical_events" (
  "id" text NOT NULL,
  "source" text NOT NULL,
  "type" text NOT NULL,
  "subject" text NOT NULL,
  "severity" text NULL,
  "time" timestamptz NOT NULL,
  "data" jsonb NOT NULL DEFAULT '{}',
  "ingested_at" timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY ("id")
);
-- Create index "idx_tactical_events_subject_time" to table: "tactical_events"
CREATE INDEX "idx_tactical_events_subject_time" ON "public"."tactical_events" ("subject", "time");
-- Create index "idx_tactical_events_time" to table: "tactical_events"
CREATE INDEX "idx_tactical_events_time" ON "public"."tactical_events" ("time");
-- Create "telemetry_latest_state" table
CREATE TABLE "public"."telemetry_latest_state" (
  "asset_id" text NOT NULL,
  "platform_variant" text NULL,
  "callsign" text NULL,
  "force_id" text NULL,
  "kinematics" jsonb NULL,
  "sustainment" jsonb NULL,
  "provenance" jsonb NOT NULL DEFAULT '{}',
  "last_sample_at" timestamptz NULL,
  "schema_revision" integer NOT NULL DEFAULT 0,
  "updated_at" timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY ("asset_id")
);
