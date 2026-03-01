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
-- Only inventory_items is exposed to Edge clients. The audit_log is HQ-only
-- and should NOT be synced to untrusted Edge nodes.
-- -----------------------------------------------------------------------------
CREATE PUBLICATION IF NOT EXISTS electric_publication
  FOR TABLE public.inventory_items;

-- -----------------------------------------------------------------------------
-- 2. Replication Role — Required for ElectricSQL's logical replication
-- -----------------------------------------------------------------------------
-- The openddil user needs the REPLICATION attribute to create replication
-- slots. In containerized Postgres, the superuser already has this, but we
-- set it explicitly for clarity and for non-superuser deployments.
-- -----------------------------------------------------------------------------
ALTER ROLE openddil WITH REPLICATION;

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
