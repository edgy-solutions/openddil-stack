-- asset_declaration: one row per declared asset id (ADR-0050 section 2), written at
-- the owning tier; the file-labelled fleet never appears here. It is the
-- releasability row for the id. Never pruned or TTL'd, like munition_track.
CREATE TABLE "public"."asset_declaration" (
  "asset_id" text NOT NULL,
  "serial" text NULL,
  "unit" text NOT NULL,
  "originator_nation" text NOT NULL,
  "releasable_to" text[] NOT NULL DEFAULT '{}',
  "baseline_id" text NULL,
  "declared_by" text NOT NULL,
  "declared_at" timestamptz NOT NULL,
  "source" text NOT NULL,
  "edge_id" text NOT NULL,
  "region_id" text NULL,
  "owning_tier" text NOT NULL,
  "updated_at" timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY ("asset_id"),
  CONSTRAINT "asset_declaration_source_check" CHECK (source IN ('declare', 'expect-confirm'))
);
-- Create index "idx_asset_declaration_edge_id" to table: "asset_declaration"
CREATE INDEX "idx_asset_declaration_edge_id" ON "public"."asset_declaration" ("edge_id");

-- asset_correlation: serial <-> asset_id, written when a declaration gives
-- a serial (ADR-0050 section 2). A serial is expected until a row names it.
-- Never pruned or TTL'd, like munition_track.
CREATE TABLE "public"."asset_correlation" (
  "serial" text NOT NULL,
  "asset_id" text NOT NULL,
  "edge_id" text NOT NULL,
  "region_id" text NULL,
  "originator_nation" text NOT NULL,
  "releasable_to" text[] NOT NULL DEFAULT '{}',
  "declared_by" text NOT NULL,
  "declared_at" timestamptz NOT NULL,
  "source" text NOT NULL,
  "updated_at" timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY ("serial"),
  CONSTRAINT "asset_correlation_source_check" CHECK (source IN ('declare', 'expect-confirm'))
);
-- Create index "idx_asset_correlation_asset_id" to table: "asset_correlation"
CREATE UNIQUE INDEX "idx_asset_correlation_asset_id" ON "public"."asset_correlation" ("asset_id");

-- asset_fielding: the expected-arrival record from the logistics system of
-- record (ADR-0050 section 4), keyed by serial. originator_nation is the record's
-- owning nation. Never pruned or TTL'd, like munition_track.
CREATE TABLE "public"."asset_fielding" (
  "serial" text NOT NULL,
  "platform_variant" text NOT NULL,
  "unit" text NOT NULL,
  "originator_nation" text NOT NULL,
  "releasable_to" text[] NOT NULL DEFAULT '{}',
  "fielded_to" text NOT NULL,
  "region_id" text NULL,
  "due_by" timestamptz NOT NULL,
  "recorded_at" timestamptz NOT NULL,
  "updated_at" timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY ("serial")
);
-- Create index "idx_asset_fielding_fielded_to" to table: "asset_fielding"
CREATE INDEX "idx_asset_fielding_fielded_to" ON "public"."asset_fielding" ("fielded_to");
