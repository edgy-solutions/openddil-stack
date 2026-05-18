-- Create "region_fleet_summary" table
CREATE TABLE "public"."region_fleet_summary" (
  "region_id" text NOT NULL,
  "nominal" integer NOT NULL DEFAULT 0,
  "degraded" integer NOT NULL DEFAULT 0,
  "critical" integer NOT NULL DEFAULT 0,
  "non_operational" integer NOT NULL DEFAULT 0,
  "asset_count" integer NOT NULL DEFAULT 0,
  "observed_at" timestamptz NULL,
  "updated_at" timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY ("region_id")
);
-- Create "region_top_factors" table
CREATE TABLE "public"."region_top_factors" (
  "region_id" text NOT NULL,
  "factors" jsonb NOT NULL DEFAULT '[]',
  "observed_at" timestamptz NULL,
  "updated_at" timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY ("region_id")
);
-- Create "region_wear_trends" table
CREATE TABLE "public"."region_wear_trends" (
  "region_id" text NOT NULL,
  "components" jsonb NOT NULL DEFAULT '[]',
  "observed_at" timestamptz NULL,
  "updated_at" timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY ("region_id")
);
