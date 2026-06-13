-- Phase 9: mrad_element_telemetry -- per-element radar telemetry for MRAD-class
-- assets, populated by openddil-mrad-sim.
--
-- The customer's AMQP feed (and AFSim / VRForces for the DIS path) does not
-- produce per-element radar telemetry, but the MaintainerApp's SensorArrayView
-- needs that level of detail to make the MRAD 3D drill-down interesting.
-- mrad-sim synthesizes per-element health/temp/load values per discovered MRAD
-- asset and publishes whole-asset snapshots to mrad-element-telemetry. The
-- projector handler upserts the latest snapshot per asset into this table.
--
-- Shape choice: one row per asset, elements stored as JSONB. The frontend
-- consumes the WHOLE element set in one render pass (SensorArrayView's
-- liveTelemetry prop is a flat map keyed by element_id), so per-element rows
-- would just force the projector to gather-then-flush per asset and complicate
-- ordering. JSONB matches both the publisher envelope shape AND the consumer's
-- read pattern.
CREATE TABLE "public"."mrad_element_telemetry" (
  "asset_id"        text NOT NULL,
  -- Element snapshot. JSON array shape (matches the mrad-sim publisher
  -- envelope):
  --   [{element_id, layer_depth, layer_name, health, temp_c, load_pct}, ...]
  -- The frontend SensorArrayView builds a flat lookup map keyed by
  -- element_id; element_id format is documented in mrad-sim/README.md.
  "elements"        jsonb NOT NULL DEFAULT '[]'::jsonb,
  -- When the sim observed this snapshot. Lets the UI show "last sim
  -- update" and the projector / monitoring detect stale sims.
  "observed_at"     timestamptz NOT NULL DEFAULT now(),
  -- Origin-node provenance -- per-asset projection rows carry edge_id /
  -- region_id like every other per-asset table. Sim's records have no
  -- edge attribution of their own (the sim runs on HQ), so the projector
  -- backfills these from the asset_registry lookup -- same path
  -- capability_state uses for strike-only launchers.
  "edge_id"         text NOT NULL DEFAULT 'edge-unspecified',
  "region_id"       text NOT NULL DEFAULT 'region-unspecified',
  "updated_at"      timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY ("asset_id")
);

-- Region partition index so source-app's region cache lookups + the regional
-- aggregator's per-region scans don't full-table scan once this fills out.
CREATE INDEX "mrad_element_telemetry_region_idx"
  ON "public"."mrad_element_telemetry" ("region_id");

-- Edge partition index -- same rationale for per-edge maintainer views.
CREATE INDEX "mrad_element_telemetry_edge_idx"
  ON "public"."mrad_element_telemetry" ("edge_id");
