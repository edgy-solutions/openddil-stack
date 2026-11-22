-- asset_identity: ADR-0056's first increment, store half (DESIGN-2026-11-08, sections 1 and 2.7).
-- Six tables and the era function. The asset registry service is the only writer; no table is
-- served, and none is added to Electric's publication (section 1.4), so none carries
-- originator_nation / releasable_to. Nothing is ever updated or deleted here except
-- asset_alias.valid_to, from NULL to a time, inside declare_equivalence (section 1.2).
--
-- alias_scheme.namespace is the uuid5 namespace of a scheme's lookup keys. Each one below is a
-- fixed literal, generated once with uuid.uuid4() when this file was written. It is never derived
-- from the scheme's name, and it never changes: a changed namespace changes every lookup key.
--
-- Atlas applies migrations with --tx-mode none, so this file carries its own transaction. The
-- lock and statement timeouts are SET LOCAL and end at COMMIT. The migration adds no extension.

BEGIN;
SET LOCAL lock_timeout = '5s';
SET LOCAL statement_timeout = '60s';

CREATE TABLE "public"."alias_scheme" (
  "scheme" text NOT NULL,
  "owner" text NOT NULL,
  "value_form" text NOT NULL,
  "namespace" uuid NOT NULL,
  "registered_at" timestamptz NOT NULL,
  "registered_by" text NOT NULL,
  PRIMARY KEY ("scheme"),
  CONSTRAINT "alias_scheme_namespace_key" UNIQUE ("namespace")
);

CREATE TABLE "public"."asset_identity" (
  "identity" uuid NOT NULL,
  "minted_at" timestamptz NOT NULL,
  "minting_tier" text NOT NULL,
  "generator" text NOT NULL,
  "generator_version" text NOT NULL,
  "declaration" text NOT NULL,
  PRIMARY KEY ("identity")
);

CREATE TABLE "public"."asset_alias" (
  "scheme" text NOT NULL,
  "value" text NOT NULL,
  "lookup_key" uuid NOT NULL,
  "identity" uuid NOT NULL,
  "authority" text NOT NULL,
  "valid_from" timestamptz NOT NULL,
  "valid_to" timestamptz NULL,
  "declaration" text NOT NULL,
  CONSTRAINT "asset_alias_scheme_fkey" FOREIGN KEY ("scheme") REFERENCES "public"."alias_scheme" ("scheme"),
  CONSTRAINT "asset_alias_identity_fkey" FOREIGN KEY ("identity") REFERENCES "public"."asset_identity" ("identity")
);
-- Create index "asset_alias_open_lookup" to table: "asset_alias". This index is what makes
-- lookup-before-mint atomic (section 2.3): at most one open alias row per (scheme, lookup_key).
CREATE UNIQUE INDEX "asset_alias_open_lookup" ON "public"."asset_alias" ("scheme", "lookup_key") WHERE "valid_to" IS NULL;
CREATE INDEX "idx_asset_alias_identity" ON "public"."asset_alias" ("identity");

CREATE TABLE "public"."asset_binding" (
  "asset_id" text NOT NULL,
  "identity" uuid NOT NULL,
  "bound_at" timestamptz NOT NULL,
  "bound_by" text NOT NULL,
  "declaration" text NOT NULL,
  PRIMARY KEY ("asset_id", "identity"),
  CONSTRAINT "asset_binding_identity_fkey" FOREIGN KEY ("identity") REFERENCES "public"."asset_identity" ("identity")
);
-- One asset binds once in this increment; a later rebinding is a new row under an owned equivalence.
CREATE UNIQUE INDEX "asset_binding_asset_id" ON "public"."asset_binding" ("asset_id");

CREATE TABLE "public"."identity_equivalence" (
  "survivor" uuid NOT NULL,
  "merged" uuid NOT NULL,
  "owner" text NOT NULL,
  "declared_at" timestamptz NOT NULL,
  "reason" text NOT NULL,
  "declaration" text NOT NULL,
  PRIMARY KEY ("merged"),
  CONSTRAINT "identity_equivalence_survivor_fkey" FOREIGN KEY ("survivor") REFERENCES "public"."asset_identity" ("identity"),
  CONSTRAINT "identity_equivalence_merged_fkey" FOREIGN KEY ("merged") REFERENCES "public"."asset_identity" ("identity"),
  CONSTRAINT "identity_equivalence_distinct" CHECK ("survivor" <> "merged")
);

CREATE TABLE "public"."identity_conflict" (
  "declaration" text NULL,
  "asset_id" text NULL,
  "identities" uuid[] NULL,
  "raised_at" timestamptz NULL
);

INSERT INTO "public"."alias_scheme" ("scheme", "owner", "value_form", "namespace", "registered_at", "registered_by") VALUES
  ('asset_id', 'deployment', 'opaque text, never parsed (ADR-0047)', 'b9e2aa09-1434-4a82-b59e-f149e9a3f5d2', now(), 'migration 20261122000000'),
  ('serial', 'declarant', 'opaque text, as the declaration carries it', '3e77136b-26ce-4947-85b8-6ab6fc2012d5', now(), 'migration 20261122000000'),
  ('identity', 'registry', 'urn:uuid:<uuid>, the alias a merged identity leaves on its survivor', 'e0bc965c-1ab8-4c0f-9c7b-8f4f546c0c5e', now(), 'migration 20261122000000'),
  ('uii', 'declarant', 'opaque text, as the declaration carries it', '45c19c89-c3b1-40ef-81c8-85f16302c4dd', now(), 'migration 20261122000000');

-- The era read (section 2.7): 2 when a binding row for asset_id has bound_at at or before
-- first_observed, else 1. Its inputs are an asset_id and a time, never an id.
CREATE OR REPLACE FUNCTION "public"."asset_id_era"(asset_id text, first_observed timestamptz)
RETURNS smallint
LANGUAGE sql
STABLE
AS $$
  SELECT CASE WHEN EXISTS (
    SELECT 1 FROM "public"."asset_binding" b
    WHERE b."asset_id" = asset_id_era.asset_id AND b."bound_at" <= asset_id_era.first_observed
  ) THEN 2 ELSE 1 END::smallint
$$;

COMMIT;
