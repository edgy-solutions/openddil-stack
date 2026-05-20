-- =============================================================================
-- OpenDDIL — ElectricSQL Read-Path Configuration
-- =============================================================================
-- ElectricSQL (v0.9+) is a READ-PATH-ONLY sync engine. It does NOT require
-- special DDL extensions. Instead, it connects to Postgres using logical
-- replication and exposes tables via an HTTP Shape API.
--
-- This script configures the Postgres side to support ElectricSQL:
--   1. Creates a PUBLICATION for the tables we want to expose to Edge clients.
--   2. Grants the necessary replication privileges to the application user.
--
-- ElectricSQL will automatically create a replication slot when it connects.
--
-- Run against the openddil database after Atlas has applied the schema:
--   psql -h localhost -U openddil -d openddil -f electric/electrify.sql
-- =============================================================================

-- -----------------------------------------------------------------------------
-- 1. Publication — Controls which tables ElectricSQL replicates
-- -----------------------------------------------------------------------------
-- Tables exposed to Edge clients:
--   * inventory_items           — FOB inventory (Phase 0)
--   * asset_cm_state            — per-asset CM state (Phase 4a)
--   * asset_logistics_status    — per-asset logistics severity (Phase 4a)
--   * telemetry_latest_state    — per-asset latest telemetry snapshot (Phase 4a)
--   * tactical_events           — CloudEvents alert log (Phase 4a)
--   * asset_telemetry_windows   — per-asset windowed aggregations (Phase 4a)
--   * edge_buffer_status        — edge->HQ DDIL link/buffer status (Phase 4c.5)
--
-- audit_log is HQ-only and is intentionally NOT included.
-- -----------------------------------------------------------------------------
-- Postgres has no `CREATE PUBLICATION IF NOT EXISTS`; guard with a DO block
-- so this file is safely re-runnable (atlas-init / electric-publish-init run
-- it on every stack bring-up).
DO $$
BEGIN
  IF NOT EXISTS (
    SELECT 1 FROM pg_publication WHERE pubname = 'electric_publication'
  ) THEN
    CREATE PUBLICATION electric_publication
      FOR TABLE public.inventory_items,
                public.asset_cm_state,
                public.asset_logistics_status,
                public.telemetry_latest_state,
                public.tactical_events,
                public.asset_telemetry_windows,
                public.edge_buffer_status;
  END IF;
END
$$;

-- Idempotently ensure edge_buffer_status is in the publication even on a
-- deployment whose publication was created before this table existed.
-- ALTER PUBLICATION ADD TABLE errors if the table is already a member,
-- so guard on pg_publication_tables.
DO $$
BEGIN
  IF NOT EXISTS (
    SELECT 1 FROM pg_publication_tables
    WHERE pubname = 'electric_publication' AND tablename = 'edge_buffer_status'
  ) THEN
    ALTER PUBLICATION electric_publication ADD TABLE public.edge_buffer_status;
  END IF;
END
$$;

-- Phase 6b §B (ADR-0023): regional rollups (region_fleet_summary,
-- region_top_factors, region_wear_trends). Same idempotent ALTER pattern;
-- exposes the three to ElectricSQL Shapes so the REGION FLEET SUMMARY
-- panel can read them live.
DO $$
DECLARE
  t text;
BEGIN
  FOREACH t IN ARRAY ARRAY['region_fleet_summary', 'region_top_factors', 'region_wear_trends']
  LOOP
    IF NOT EXISTS (
      SELECT 1 FROM pg_publication_tables
      WHERE pubname = 'electric_publication' AND tablename = t
    ) THEN
      EXECUTE format('ALTER PUBLICATION electric_publication ADD TABLE public.%I', t);
    END IF;
  END LOOP;
END
$$;

-- Recipe v3 Sub-phase E: asset_capability_state (capability snapshots from
-- the customer-overlay StrikeCapabilityMessage feed). Same idempotent ALTER
-- pattern; exposes the table to ElectricSQL Shapes so the Inventory panel
-- (Sub-phase G) can read per-asset loaded-store / Ammo state live.
DO $$
BEGIN
  IF NOT EXISTS (
    SELECT 1 FROM pg_publication_tables
    WHERE pubname = 'electric_publication' AND tablename = 'asset_capability_state'
  ) THEN
    ALTER PUBLICATION electric_publication ADD TABLE public.asset_capability_state;
  END IF;
END
$$;

-- -----------------------------------------------------------------------------
-- 2. Replication Role — Required for ElectricSQL's logical replication
-- -----------------------------------------------------------------------------
-- The application user needs the REPLICATION attribute to create replication
-- slots. Run conditionally so this file works both in openddil-stack (user
-- `openddil`) and openddil-demo (user `postgres`, already superuser).
-- -----------------------------------------------------------------------------
DO $$
BEGIN
  IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'openddil') THEN
    ALTER ROLE openddil WITH REPLICATION;
  END IF;
END
$$;

-- =============================================================================
-- Edge Client Usage (for reference — not executed here)
-- =============================================================================
-- Edge SDKs connect to ElectricSQL's Shape API to subscribe to table changes:
--
--   GET http://localhost:3000/v1/shape?table=inventory_items&offset=-1
--
-- The response is a stream of row-level changes that the Edge SDK applies
-- to the local SQLite read-cache. The offset parameter enables resumable
-- sync — critical for DDIL environments where connections drop frequently.
--
-- To filter the shape (partial replication):
--   GET http://localhost:3000/v1/shape?table=inventory_items&where=available_count>0
--
-- See: https://electric-sql.com/docs/guides/shapes
-- =============================================================================
