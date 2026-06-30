-- Maintainer-view per-asset Inventory card (2026-06-30).
--
-- inventory_items gains per-asset + per-layer scoping so the sim
-- (openddil-logistics-sim) can drive the maintainer view's
-- "Local FOB Inventory" card directly from the same per-element
-- snapshot that drives the 3D drill-down's tile colours.
--
-- Per-element health/temp/load already flows on asset-element-telemetry.
-- The sim adds an aggregator that, per asset per tick, counts the
-- DEGRADED/FAULT/FAILED elements at each layer (T/R MODULE, BACKPLANE,
-- PROCESSOR, GaN CHIP) and emits one row per (asset_id, layer_name) on
-- a new topic asset-element-inventory:
--
--   allocated = count of elements with health > 0.90 at that layer
--   available = total - allocated
--   name      = layer_name (rendered as the bar label)
--
-- The projector's asset_element_inventory handler upserts using a
-- deterministic id of shape `<asset_id>:<layer_name>` so each tick is
-- idempotent (same key -> same row). asset_id + layer_name are
-- nullable to keep pre-2026-06-30 FOB-scoped rows (currently zero on
-- the cluster, per Inventory.tsx's #17 follow-up empty state) parsing
-- cleanly if a future FOB-roster source ever fills them.
--
-- Frontend Inventory.tsx filters by selectedAssetId (maintainer view's
-- focused asset). idx_inventory_items_asset_id keeps that filter cheap.
--
-- Migration order matters:
--   1. Change id from uuid to text. The table is empty on every
--      known deployment (verified pre-cutover by query); the cast is
--      cosmetic. If a deployment has rows, the cast preserves the
--      UUID string representation -- TEXT accepts it without
--      coercion.
--   2. Drop the gen_random_uuid default since the sim supplies the id.
--   3. Add asset_id, layer_name columns (NULL, no defaults).
--   4. Add the asset_id index.

BEGIN;

ALTER TABLE inventory_items
    ALTER COLUMN id DROP DEFAULT,
    ALTER COLUMN id TYPE text USING id::text;

ALTER TABLE inventory_items
    ADD COLUMN IF NOT EXISTS asset_id   text NULL,
    ADD COLUMN IF NOT EXISTS layer_name text NULL;

CREATE INDEX IF NOT EXISTS idx_inventory_items_asset_id
    ON inventory_items (asset_id);

COMMIT;
