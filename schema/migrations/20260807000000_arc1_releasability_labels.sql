-- Arc 1 Phase 1 (step-zero schema) — coalition releasability label columns.
--
-- ADR-0029 makes releasability an ABAC resource attribute: a row's
-- originating nation and its releasable-to set are what the policy engine
-- decides over. ADR-0032 §b requires every tier-local store to be born with
-- the final schema, so these columns land BEFORE any store is distributed --
-- one migration definition rather than N divergent ones.
--
-- WHY REAL COLUMNS AND NOT JSONB
-- The read-path PEP composes a WHERE clause from the Topaz decision:
--     originator_nation = ANY(:user_nations)
--     OR :user_nation = ANY(releasable_to)
-- That is a hot filter path on every subscription. JSONB extraction there is
-- both slower and harder to express in a subscription filter grammar, so the
-- labels are first-class columns. (ADR-0029 Phase 1.)
--
-- WHY NULLABLE, AND WHY THAT IS TEMPORARY
-- Nullable so this migration is purely additive and deployable ahead of the
-- labelling work -- no backfill required to apply it, nothing breaks for
-- producers that do not yet stamp labels.
--
-- The TARGET policy is deny-unlabeled: a row with no originator_nation is
-- releasable to nobody. That policy must NOT be enabled until every row is
-- labelled, because enforcing against partially-labelled data silently blanks
-- legitimate results and an operator cannot distinguish that from correct
-- enforcement -- both look like an empty screen. Hence the ADR-0029 §7 gate,
-- which must return zero in an environment before enforcement is enabled
-- there:
--
--     SELECT count(*) FROM telemetry_latest_state WHERE originator_nation IS NULL;
--
-- Label first, enforce second, never the reverse. (decisions/PRINCIPLES.md)
--
-- SCOPE — WHICH TABLES
-- The asset-scoped, operator-visible relations that the read-path PEP will
-- filter, i.e. the ones ADR-0031 §2.2 names as the reasoning/presentation read
-- surface, plus the asset-keyed rows those views join against:
--
--     telemetry_latest_state    -- the fleet picture; the primary filter target
--     asset_logistics_status    -- severity + constraining factors
--     asset_capability_state    -- capability / munitions snapshots
--     asset_cm_state            -- As-Maintained configuration records
--     asset_element_telemetry   -- per-element drill-down
--
-- Deliberately NOT labelled in this migration:
--   * region_* rollup tables -- aggregates over many assets, potentially of
--     mixed nationality. Labelling an aggregate requires deciding what the
--     label of a mixed-origin rollup even means (most-restrictive? union?),
--     which is an ADR-0029 Slice-2/3 question, not a step-zero one.
--   * inventory_items, edge_buffer_status, tactical_events, audit_log --
--     not part of the Slice-1 read-path filter surface. Additive later.
--
-- policy_label (structured STANAG-4774-style confidentiality label) is
-- deliberately absent: ADR-0029 reserves the proto field but does not define
-- a shape, and guessing one here would lock it into the most permanent layer.
--
-- text[] for releasable_to mirrors the proto's `repeated string`. ADR-0029
-- Phase 4(b) requires verifying array-containment expressiveness in the
-- subscription filter grammar BEFORE the gateway commits to this shape; if
-- that verification fails, this column changes shape then (cheap -- nothing
-- reads it yet).

BEGIN;

ALTER TABLE "telemetry_latest_state"
    ADD COLUMN IF NOT EXISTS "originator_nation" text   NULL,
    ADD COLUMN IF NOT EXISTS "releasable_to"     text[] NULL;

ALTER TABLE "asset_logistics_status"
    ADD COLUMN IF NOT EXISTS "originator_nation" text   NULL,
    ADD COLUMN IF NOT EXISTS "releasable_to"     text[] NULL;

ALTER TABLE "asset_capability_state"
    ADD COLUMN IF NOT EXISTS "originator_nation" text   NULL,
    ADD COLUMN IF NOT EXISTS "releasable_to"     text[] NULL;

ALTER TABLE "asset_cm_state"
    ADD COLUMN IF NOT EXISTS "originator_nation" text   NULL,
    ADD COLUMN IF NOT EXISTS "releasable_to"     text[] NULL;

ALTER TABLE "asset_element_telemetry"
    ADD COLUMN IF NOT EXISTS "originator_nation" text   NULL,
    ADD COLUMN IF NOT EXISTS "releasable_to"     text[] NULL;

-- Filter-path indexes. originator_nation is a plain equality/ANY target;
-- releasable_to is an array-containment target, hence GIN.
CREATE INDEX IF NOT EXISTS "idx_telemetry_latest_state_originator_nation"
    ON "telemetry_latest_state" ("originator_nation");
CREATE INDEX IF NOT EXISTS "idx_telemetry_latest_state_releasable_to"
    ON "telemetry_latest_state" USING GIN ("releasable_to");

CREATE INDEX IF NOT EXISTS "idx_asset_logistics_status_originator_nation"
    ON "asset_logistics_status" ("originator_nation");

CREATE INDEX IF NOT EXISTS "idx_asset_capability_state_originator_nation"
    ON "asset_capability_state" ("originator_nation");

CREATE INDEX IF NOT EXISTS "idx_asset_cm_state_originator_nation"
    ON "asset_cm_state" ("originator_nation");

CREATE INDEX IF NOT EXISTS "idx_asset_element_telemetry_originator_nation"
    ON "asset_element_telemetry" ("originator_nation");

COMMIT;
