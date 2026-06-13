-- Phase 9: asset_element_telemetry -- per-element sub-component telemetry for
-- multi-element assets, populated by openddil-logistics-sim.
--
-- The customer feeds (customer-overlay AMQP, DIS via AFSim / VRForces) do not emit
-- per-element radar / sub-component telemetry, but the maintainer 3D drill-
-- down needs that level of detail. logistics-sim subscribes to telemetry-
-- latest-state, builds an AssetRoster of assets whose platform_variant
-- matches any configured asset_profile (MRAD first; LTAMDS / Patriot to
-- follow as config additions), and on every tick publishes a per-asset
-- snapshot to asset-element-telemetry. The projector handler upserts the
-- latest snapshot per asset into this table; the SensorArrayView consumes
-- the JSONB elements column via the useAssetElementTelemetry ElectricSQL
-- hook.
--
-- operational + per-element tx_active / rx_active fields land in the JSONB
-- array verbatim -- the customer feed's operational_state propagates per-
-- element so what the maintainer sees on the 3D drill-down stays in sync
-- with what the customer sim reports.
--
-- platform_variant is denormalized into a top-level column for fast
-- per-type queries (e.g. "how many MRADs are degraded" without unpacking
-- JSONB).
CREATE TABLE "public"."asset_element_telemetry" (
  "asset_id"          text NOT NULL,
  "platform_variant"  text NOT NULL DEFAULT '',
  "profile_name"      text NOT NULL DEFAULT '',
  -- Element snapshot. JSON array shape (matches the logistics-sim
  -- publisher envelope):
  --   [{element_id, layer_depth, layer_name, health, temp_c, load_pct,
  --     tx_active, rx_active}, ...]
  -- element_id format documented in openddil-logistics-sim/README.md.
  "elements"          jsonb NOT NULL DEFAULT '[]'::jsonb,
  -- Mirror of the customer feed's OperationalState on this asset at the
  -- tick the snapshot was taken. Carried separately from elements so a
  -- consumer doesn't need to unpack JSONB just to render an asset-level
  -- status banner.
  "operational"       jsonb NOT NULL DEFAULT '{}'::jsonb,
  "observed_at"       timestamptz NOT NULL DEFAULT now(),
  "edge_id"           text NOT NULL DEFAULT 'edge-unspecified',
  "region_id"         text NOT NULL DEFAULT 'region-unspecified',
  "updated_at"        timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY ("asset_id")
);

CREATE INDEX "asset_element_telemetry_region_idx"
  ON "public"."asset_element_telemetry" ("region_id");
CREATE INDEX "asset_element_telemetry_edge_idx"
  ON "public"."asset_element_telemetry" ("edge_id");
CREATE INDEX "asset_element_telemetry_variant_idx"
  ON "public"."asset_element_telemetry" ("platform_variant");
