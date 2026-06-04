-- Phase 8 / ADR-0028: asset_registry — canonical asset->edge->region lineage
--
-- Single source of truth for which edge_id and region_id an asset belongs
-- to. Written by asset-registry-service. Read by every consumer that needs
-- to know an asset's edge/region: cm-service, logistics-fusion,
-- faust-regional source-app, projector. Replaces the three-different-
-- derivation-paths-across-three-services state that silently dropped
-- 100% of cm-service + logistics-fusion events at the regional source-
-- app's positive-region filter.
--
-- Priority of assignment_source values (highest first):
--   'static'      — warfighter input (admin UI / config file write). Wins.
--   'connection'  — derived from "which edge's aggregator first observed
--                   this asset". Reliable for fixed sensors with no
--                   position-bearing telemetry.
--   'position'    — nearest-FOB lookup via edge_assignment.yaml. The
--                   policy moved out of the projector for centralization.
--   'unspecified' — fallback when no input yields a clear answer. Asset
--                   still tracked, assignment marked unknown.
--
-- divergent + observed_edge_id together let the registry honor static
-- assignment (the warfighter's authoritative answer) while still
-- surfacing real-vs-intent drift as a logistics signal. See ADR-0028's
-- "Real-vs-intent divergence" section -- the registry never overrides
-- a static assignment, just flags when reality disagrees.
CREATE TABLE "public"."asset_registry" (
  "asset_id"          text NOT NULL,
  "edge_id"           text NOT NULL,
  "region_id"         text NOT NULL,
  "assignment_source" text NOT NULL,
  "assigned_at"       timestamptz NOT NULL DEFAULT now(),
  "assigned_by"       text NULL,
  "last_observed_at"  timestamptz NULL,
  "observed_edge_id"  text NULL,
  "divergent"         boolean NOT NULL DEFAULT false,
  PRIMARY KEY ("asset_id"),
  -- The four valid assignment_source values. Future re-assignment policies
  -- (e.g. 'failover', 'manual-override') must add a new value here AND
  -- update the registry-service's priority logic in the same change.
  CONSTRAINT "asset_registry_assignment_source_check" CHECK (
    "assignment_source" IN ('static', 'connection', 'position', 'unspecified')
  )
);

-- Partial index supporting the "show me divergent assignments" panel
-- planned for ADR-0028 Phase 4. Partial because divergent=true is the
-- exception, not the rule -- full-table-index waste.
CREATE INDEX "idx_asset_registry_divergent"
  ON "public"."asset_registry" ("divergent")
  WHERE "divergent";

-- Secondary index for "which assets are at edge X". Used by per-edge
-- projector and faust-regional source-app caches when bulk-loading a
-- region's worth of assignments at startup.
CREATE INDEX "idx_asset_registry_edge_id"
  ON "public"."asset_registry" ("edge_id");

CREATE INDEX "idx_asset_registry_region_id"
  ON "public"."asset_registry" ("region_id");

-- Add to Electric replication so the SPA can subscribe to assignments
-- (will be used by future Maintainer divergence banner + admin
-- assignment UI per ADR-0028 Phase 4).
ALTER PUBLICATION "electric_publication" ADD TABLE "public"."asset_registry";
