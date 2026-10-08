# =============================================================================
# OpenDDIL — Declarative Atlas Schema
# =============================================================================
# This file defines the DESIRED STATE of the database. Atlas diffs it against
# the live Postgres instance and generates the migration automatically.
#
# Convention: Expand/Contract migrations are safe for DDIL environments.
#   Phase 1 (Expand): Add new columns/tables — backward compatible.
#   Phase 2 (Contract): Drop old columns/tables after all consumers migrate.
#
# Apply manually:
#   atlas schema apply \
#     --url "postgres://openddil:openddil@localhost:5432/openddil?sslmode=disable" \
#     --to "file://schema/schema.hcl" \
#     --dev-url "sqlite://dev?mode=memory"
# =============================================================================

schema "public" {}

# -----------------------------------------------------------------------------
# inventory_items — Core domain table synced to Edge via ElectricSQL
# -----------------------------------------------------------------------------
# This table is the primary read-model that ElectricSQL replicates to Edge
# clients. Edge nodes query a local SQLite cache of this table for offline
# reads. Writes go through the Outbox → Redpanda → Restate pipeline.
#
# 2026-06-30: gained per-asset + per-layer scoping (asset_id, layer_name)
# to support the maintainer view's per-asset Inventory card. The sim
# (openddil-logistics-sim) emits one row per (asset_id, layer_name) per
# tick to asset-element-inventory; the projector upserts using
# `<asset_id>:<layer_name>` as the row id. asset_id is nullable so
# pre-2026-06-30 FOB-scoped rows (currently zero on the cluster) still
# parse cleanly. Frontend Inventory.tsx filters by selectedAssetId when
# scoped to the maintainer view.
# -----------------------------------------------------------------------------
table "inventory_items" {
  schema = schema.public

  # Was UUID with gen_random_uuid default; switched to TEXT so the sim
  # can produce deterministic ids of shape `<asset_id>:<layer_name>` --
  # makes upsert keyed by id idempotent without needing a composite
  # unique constraint. Pre-2026-06-30 rows (if any) carried UUID
  # strings, which TEXT accepts without coercion.
  column "id" {
    type = text
  }

  column "name" {
    type = varchar(255)
    null = false
  }

  # Asset attribution. Nullable for back-compat with the old FOB-scoped
  # rows (no asset). The sim populates this for every emitted row.
  column "asset_id" {
    type = text
    null = true
  }

  # Layer this inventory bar represents (e.g. "T/R MODULE", "BACKPLANE").
  # Nullable for back-compat; populated by the sim. Combined with
  # asset_id it uniquely identifies a sim-emitted row.
  column "layer_name" {
    type = text
    null = true
  }

  column "available_count" {
    type    = integer
    null    = false
    default = 0
  }

  column "allocated_count" {
    type    = integer
    null    = false
    default = 0
  }

  column "created_at" {
    type    = timestamptz
    null    = false
    default = sql("now()")
  }

  column "updated_at" {
    type    = timestamptz
    null    = false
    default = sql("now()")
  }

  primary_key {
    columns = [column.id]
  }

  # Frontend Inventory.tsx filters rows by selectedAssetId; the index
  # keeps that filter cheap as the table grows (with 4 layers per
  # asset, an 800-asset fleet pushes the table to ~3200 rows).
  index "idx_inventory_items_asset_id" {
    columns = [column.asset_id]
  }

  # ADR-0029 releasability labels. Added by the labelling block
  # (20260906000000) — the "additive later" the Arc-1 migration named. Each
  # row here derives from an already-labelled asset, so stamping is
  # PROPAGATION of an existing decision rather than a new one.
  # Nullable: deny-unlabeled means unstamped rows are releasable to nobody,
  # which is the safe direction while the projector catches up.
  column "originator_nation" {
    type = text
    null = true
  }

  column "releasable_to" {
    type = sql("text[]")
    null = true
  }

}

# -----------------------------------------------------------------------------
# audit_log — Immutable event log for all domain mutations
# -----------------------------------------------------------------------------
# Records every action processed by the Restate handlers at HQ. Provides a
# complete, append-only trail for compliance, debugging, and conflict analysis
# in DDIL scenarios where events may arrive out-of-order or be replayed.
# -----------------------------------------------------------------------------
table "audit_log" {
  schema = schema.public

  column "id" {
    type    = uuid
    default = sql("gen_random_uuid()")
  }

  column "entity_type" {
    type = varchar(100)
    null = false
  }

  column "entity_id" {
    type = uuid
    null = false
  }

  column "action" {
    type = varchar(50)
    null = false
  }

  column "payload" {
    type = jsonb
    null = true
  }

  column "actor" {
    type = varchar(255)
    null = true
  }

  column "occurred_at" {
    type    = timestamptz
    null    = false
    default = sql("now()")
  }

  primary_key {
    columns = [column.id]
  }

  index "idx_audit_log_entity" {
    columns = [column.entity_type, column.entity_id]
  }
}

# -----------------------------------------------------------------------------
# Phase 4a: Kafka-projected read tables
# -----------------------------------------------------------------------------
# Each table below is populated by the openddil-projector service consuming
# one Kafka topic. Compacted topics map to UPSERT-by-PK tables; the
# tactical_events stream maps to an append-only log with TTL pruning.
#
# Nested Protobuf structures are persisted as JSONB blobs rather than
# flattened. The UI consumes them via ElectricSQL shapes and accesses fields
# via JSONB path expressions. Adding new nested fields does not require a
# migration.
# -----------------------------------------------------------------------------

# Per-asset CM state. Source: topic `asset-cm-state`, compacted, keyed by asset_id.
# Producer: openddil-cm-service (AssetCM Virtual Object).
table "asset_cm_state" {
  schema = schema.public

  # ADR-0029 coalition releasability labels. Real columns (not JSONB) because
  # the read-path PEP composes a WHERE clause over them on every subscription.
  # Nullable until the labelling backfill completes -- deny-unlabeled must not
  # be enabled before the completeness gate returns zero (ADR-0029 s7).
  column "originator_nation" {
    type = text
    null = true
  }

  column "releasable_to" {
    type = sql("text[]")
    null = true
  }

  column "asset_id" {
    type = text
    null = false
  }

  # Origin-node provenance (ADR-0022). OpenDDIL is hierarchical streaming
  # aggregation — edge -> regional -> HQ. The current topology is collapsed
  # to a single flat tier, so these carry constant defaults today, but every
  # per-asset projection row is shaped for the hierarchy from the start:
  # retrofitting an echelon dimension after shapes, rollups, and egress
  # bridges already exist is the expensive path (the Quantity-everywhere
  # lesson from Phase 2.5 — get the schema shape right during the build phase
  # even when the values are trivial). Present on all five per-asset tables.
  column "edge_id" {
    type    = text
    null    = false
    default = "edge-01"
  }

  column "region_id" {
    type    = text
    null    = false
    default = "region-01"
  }

  column "baseline_id" {
    type = text
    null = true
  }

  column "lifecycle" {
    type    = text
    null    = false
    default = "LIFECYCLE_UNSPECIFIED"
  }

  column "overall_status" {
    type    = text
    null    = false
    default = "CONFIG_STATUS_UNSPECIFIED"
  }

  column "last_alerted_status" {
    type = text
    null = true
  }

  column "as_of" {
    type = timestamptz
    null = true
  }

  column "last_observed_at" {
    type = timestamptz
    null = true
  }

  column "installed" {
    type    = jsonb
    null    = false
    default = "[]"
  }

  column "mod_status" {
    type    = jsonb
    null    = false
    default = "[]"
  }

  column "discrepancies" {
    type    = jsonb
    null    = false
    default = "[]"
  }

  # cm-service keeps analyzer-rebuilt `discrepancies` and human-raised
  # `manual_discrepancies` in separate lists (ADR-0009 addendum) — the
  # projector preserves that separation so the UI can too.
  column "manual_discrepancies" {
    type    = jsonb
    null    = false
    default = "[]"
  }

  column "updated_at" {
    type    = timestamptz
    null    = false
    default = sql("now()")
  }

  primary_key {
    columns = [column.asset_id]
  }
}

# Per-asset logistics severity. Source: topic `asset-logistics-status`, compacted.
# Producer: openddil-logistics-fusion-service.
# AssetLogisticsStatusUpdate envelope is unwrapped by the projector — the status
# fields land here, and the envelope flags (is_transition, is_initial,
# previous_severity) are preserved alongside.
table "asset_logistics_status" {
  schema = schema.public

  # ADR-0029 coalition releasability labels. Real columns (not JSONB) because
  # the read-path PEP composes a WHERE clause over them on every subscription.
  # Nullable until the labelling backfill completes -- deny-unlabeled must not
  # be enabled before the completeness gate returns zero (ADR-0029 s7).
  column "originator_nation" {
    type = text
    null = true
  }

  column "releasable_to" {
    type = sql("text[]")
    null = true
  }

  column "asset_id" {
    type = text
    null = false
  }

  # Origin-node provenance — see ADR-0022.
  column "edge_id" {
    type    = text
    null    = false
    default = "edge-01"
  }

  column "region_id" {
    type    = text
    null    = false
    default = "region-01"
  }

  column "platform_variant" {
    type = text
    null = true
  }

  column "overall_severity" {
    type    = text
    null    = false
    default = "LOGISTICS_SEVERITY_UNSPECIFIED"
  }

  column "previous_severity" {
    type = text
    null = true
  }

  column "constraining_factors" {
    type    = jsonb
    null    = false
    default = "[]"
  }

  column "projected_mission_capable_remaining_seconds" {
    type = integer
    null = true
  }

  column "projected_time_to_next_constraint_seconds" {
    type = integer
    null = true
  }

  column "cm_baseline_id" {
    type = text
    null = true
  }

  column "status_revision" {
    type    = bigint
    null    = false
    default = 0
  }

  column "computed_at" {
    type = timestamptz
    null = true
  }

  column "latest_telemetry_sample_time" {
    type = timestamptz
    null = true
  }

  column "is_transition" {
    type    = boolean
    null    = false
    default = false
  }

  column "is_initial" {
    type    = boolean
    null    = false
    default = false
  }

  column "updated_at" {
    type    = timestamptz
    null    = false
    default = sql("now()")
  }

  primary_key {
    columns = [column.asset_id]
  }
}

# Per-asset latest telemetry snapshot. Source: topic `telemetry-latest-state`,
# compacted. Producer: faust-edge (openddil-tactical-agents).
table "telemetry_latest_state" {
  schema = schema.public

  # ADR-0029 coalition releasability labels. Real columns (not JSONB) because
  # the read-path PEP composes a WHERE clause over them on every subscription.
  # Nullable until the labelling backfill completes -- deny-unlabeled must not
  # be enabled before the completeness gate returns zero (ADR-0029 s7).
  column "originator_nation" {
    type = text
    null = true
  }

  column "releasable_to" {
    type = sql("text[]")
    null = true
  }

  column "asset_id" {
    type = text
    null = false
  }

  # Origin-node provenance — see ADR-0022. Distinct from the free-form
  # `provenance` jsonb below (producer_id / source_protocol / sample_time):
  # these two are the structured, filterable echelon dimension.
  column "edge_id" {
    type    = text
    null    = false
    default = "edge-01"
  }

  column "region_id" {
    type    = text
    null    = false
    default = "region-01"
  }

  column "platform_variant" {
    type = text
    null = true
  }

  column "callsign" {
    type = text
    null = true
  }

  column "force_id" {
    type = text
    null = true
  }

  column "kinematics" {
    type = jsonb
    null = true
  }

  column "sustainment" {
    type = jsonb
    null = true
  }

  # Phase 5 — operational_state 3-axis breakout. Distinct from `sustainment`
  # (which is consumables / wear / fault codes — measured fields). These
  # five columns mirror EntityTelemetryEvent.operational_state proto fields
  # (ADR-0026): power_state × functional_mode × health_state plus the two
  # discrete activity flags. Stored as columns rather than a nested jsonb
  # so the SPA can render them in the Maintainer GROUND DIAGNOSTICS panel
  # without parsing a JSON blob on every row, and so future filters
  # ("show me every asset where power_state='MAINTENANCE'") are SQL-cheap.
  #
  # All NULL-able — assets without operational_state in their telemetry
  # (legacy DIS messages, capability-only assets) leave them unset, and
  # the SPA panel renders "—" for any axis it can't read.
  column "power_state" {
    type = text
    null = true
  }

  column "functional_mode" {
    type = text
    null = true
  }

  column "health_state" {
    type = text
    null = true
  }

  column "actively_receiving" {
    type = boolean
    null = true
  }

  column "actively_transmitting" {
    type = boolean
    null = true
  }

  # ADR-0044 lifecycle slice 1 — two independent, timestamped columns.
  # `operational_status` answers "what is true of the asset" and moves only
  # on a signal about the asset itself (e.g. a DIS damage=DESTROYED report).
  # `reporting_status` answers "what is true of our knowledge of it" and
  # moves only on the arrival or non-arrival of records, evaluated per
  # reading tier (ADR §4) — never shared or computed globally. Keeping
  # these as two columns is the entire point of the ADR: Finding 1 is that
  # the legacy `lifecycle`/`health_state` style fields conflate both
  # questions into one value, so a destroyed-but-still-transmitting asset
  # and a quiet-but-healthy asset become indistinguishable. Defaults assume
  # the common case (present and accounted for) so existing rows written
  # before this migration read as intended rather than as unknown/null.
  column "operational_status" {
    type    = text
    null    = false
    default = "operational"
  }

  column "operational_status_at" {
    type = timestamptz
    null = true
  }

  column "reporting_status" {
    type    = text
    null    = false
    default = "reporting"
  }

  column "reporting_status_at" {
    type = timestamptz
    null = true
  }

  # ADR-0044 amendment ("posture, a third column") -- independent of the two
  # lifecycle columns above: a moving launcher is still fully operational
  # and fully reporting. Decided once, at the edge tier that owns the
  # sensor, by a per-asset state machine; every tier stores the decided
  # value and none re-derives it. Does not feed readiness. Default assumes
  # no claim, same reasoning as operational_status/reporting_status above
  # defaulting to the common case for rows written before this migration --
  # except here "no claim" IS the common/cold-start case, not an assumption
  # of health.
  column "posture_status" {
    type    = text
    null    = false
    default = "unspecified"
  }

  column "posture_since" {
    type = timestamptz
    null = true
  }

  # Which part of its platform the record describes, declared by the
  # boundary mapper (AssetIdentity.subsystem; enum name as text). NULL = not
  # declared = the platform itself (ADR-0047).
  column "subsystem" {
    type = text
    null = true
  }

  column "provenance" {
    type    = jsonb
    null    = false
    default = "{}"
  }

  column "last_sample_at" {
    type = timestamptz
    null = true
  }

  column "schema_revision" {
    type    = integer
    null    = false
    default = 0
  }

  column "updated_at" {
    type    = timestamptz
    null    = false
    default = sql("now()")
  }

  primary_key {
    columns = [column.asset_id]
  }

  check "telemetry_latest_state_posture_status_check" {
    expr = "posture_status IN ('unspecified', 'emplaced', 'march_ordered', 'moving', 'emplacing')"
  }
}

# ADR-0044 §4 rollup over telemetry_latest_state's two lifecycle columns.
# Grouped by (region_id, originator_nation, releasable_to) -- the same
# partitioning discipline region_fleet_summary/top_factors/wear_trends use
# for a different reason (see 20260908000000_region_rollup_releasability.sql):
# those three compose ACROSS assets and need an intersection rule computed
# upstream, while this view groups assets that already share one
# releasability partition, so there is no cross-partition arithmetic inside
# it for a PEP to get wrong. It does NOT compose by summing rows: two rows
# for the same region_id but different releasable_to are two different
# viewers' worlds (ADR-0043 §4), and adding their fleet_total columns would
# manufacture a number no single viewer is entitled to see. See the
# migration file for the full rationale, including why a NULL-labelled row
# is real and not merged away.
view "asset_lifecycle_summary" {
  schema = schema.public

  column "region_id" {
    type = text
  }
  column "originator_nation" {
    type = text
  }
  column "releasable_to" {
    type = sql("text[]")
  }
  column "fleet_total" {
    type = bigint
  }
  column "operational" {
    type = bigint
  }
  column "destroyed" {
    type = bigint
  }
  column "deactivated" {
    type = bigint
  }
  column "removed" {
    type = bigint
  }
  column "reporting" {
    type = bigint
  }
  column "not_reporting" {
    type = bigint
  }

  as = <<-SQL
  SELECT
      "region_id",
      "originator_nation",
      "releasable_to",
      COUNT(*)                                                     AS "fleet_total",
      COUNT(*) FILTER (WHERE "operational_status" = 'operational') AS "operational",
      COUNT(*) FILTER (WHERE "operational_status" = 'destroyed')   AS "destroyed",
      COUNT(*) FILTER (WHERE "operational_status" = 'deactivated') AS "deactivated",
      COUNT(*) FILTER (WHERE "operational_status" = 'removed')     AS "removed",
      COUNT(*) FILTER (WHERE "reporting_status" = 'reporting')     AS "reporting",
      COUNT(*) FILTER (WHERE "reporting_status" = 'not_reporting') AS "not_reporting"
  FROM "public"."telemetry_latest_state"
  GROUP BY "region_id", "originator_nation", "releasable_to"
  SQL
}

# Append-only CloudEvents log. Source: topic `tactical-events`. Producers:
# openddil-cm-service (config alerts), openddil-logistics-fusion-service
# (logistics-CRITICAL transitions). Retained 24h by a projector-side pruner.
table "tactical_events" {
  schema = schema.public

  column "id" {
    type = text
    null = false
  }

  # Origin-node provenance — see ADR-0022. The echelon an event originated
  # at; `subject` carries the asset_id by OpenDDIL convention.
  column "edge_id" {
    type    = text
    null    = false
    default = "edge-01"
  }

  column "region_id" {
    type    = text
    null    = false
    default = "region-01"
  }

  column "source" {
    type = text
    null = false
  }

  column "type" {
    type = text
    null = false
  }

  column "subject" {
    type = text
    null = false
  }

  column "severity" {
    type = text
    null = true
  }

  column "time" {
    type = timestamptz
    null = false
  }

  column "data" {
    type    = jsonb
    null    = false
    default = "{}"
  }

  column "ingested_at" {
    type    = timestamptz
    null    = false
    default = sql("now()")
  }

  primary_key {
    columns = [column.id]
  }

  index "idx_tactical_events_time" {
    columns = [column.time]
  }

  index "idx_tactical_events_subject_time" {
    columns = [column.subject, column.time]
  }

  # ADR-0029 releasability labels. Added by the labelling block
  # (20260906000000) — the "additive later" the Arc-1 migration named. Each
  # row here derives from an already-labelled asset, so stamping is
  # PROPAGATION of an existing decision rather than a new one.
  # Nullable: deny-unlabeled means unstamped rows are releasable to nobody,
  # which is the safe direction while the projector catches up.
  column "originator_nation" {
    type = text
    null = true
  }

  column "releasable_to" {
    type = sql("text[]")
    null = true
  }

}

# Per-asset rolling-window aggregations. Source: topic `asset-telemetry-windows`,
# compacted. Producer: faust-edge windowing agent. JSONB blobs match the
# WindowedTelemetry proto structure (fluid_trends[], consumable_trends[],
# component_wear_trends[]).
table "asset_telemetry_windows" {
  schema = schema.public

  column "asset_id" {
    type = text
    null = false
  }

  # Origin-node provenance — see ADR-0022.
  column "edge_id" {
    type    = text
    null    = false
    default = "edge-01"
  }

  column "region_id" {
    type    = text
    null    = false
    default = "region-01"
  }

  column "platform_variant" {
    type = text
    null = true
  }

  column "fluid_trends" {
    type    = jsonb
    null    = false
    default = "[]"
  }

  column "consumable_trends" {
    type    = jsonb
    null    = false
    default = "[]"
  }

  column "component_wear_trends" {
    type    = jsonb
    null    = false
    default = "[]"
  }

  column "window_duration_seconds" {
    type = integer
    null = true
  }

  column "sample_count" {
    type = integer
    null = true
  }

  column "computed_at" {
    type = timestamptz
    null = true
  }

  column "updated_at" {
    type    = timestamptz
    null    = false
    default = sql("now()")
  }

  primary_key {
    columns = [column.asset_id]
  }

  # ADR-0029 releasability labels. Added by the labelling block
  # (20260906000000) — the "additive later" the Arc-1 migration named. Each
  # row here derives from an already-labelled asset, so stamping is
  # PROPAGATION of an existing decision rather than a new one.
  # Nullable: deny-unlabeled means unstamped rows are releasable to nobody,
  # which is the safe direction while the projector catches up.
  column "originator_nation" {
    type = text
    null = true
  }

  column "releasable_to" {
    type = sql("text[]")
    null = true
  }

}

# -----------------------------------------------------------------------------
# Phase 4c.5: edge->HQ DDIL link / buffer status
# -----------------------------------------------------------------------------
# Singleton row (id = 'edge') describing the real edge->HQ bridge state:
# the `bridge-group` consumer-group lag on redpanda-edge (the genuine
# edge-buffer depth — messages queued at the edge because the HQ link is
# severed) and whether the toxiproxy hq-link proxy currently has a timeout
# toxic applied. Written by the openddil-projector's edge-buffer monitor
# task; exposed to the UI via ElectricSQL so the buffer/link widgets show
# a real, honestly-backed number instead of a client-side simulation.
# -----------------------------------------------------------------------------
table "edge_buffer_status" {
  schema = schema.public

  column "id" {
    type = text
    null = false
  }

  # Sum of `bridge-group` consumer-group lag across redpanda-edge
  # partitions of raw-sensor-stream + tactical-events. The real
  # edge-buffer depth: climbs when the HQ link is severed and the bridge
  # cannot drain, falls when the link is restored.
  column "bridge_group_lag" {
    type    = bigint
    null    = false
    default = 0
  }

  # True when the toxiproxy hq-link proxy has a timeout toxic applied
  # (the WAN is "severed").
  column "hq_link_severed" {
    type    = boolean
    null    = false
    default = false
  }

  # Whether the projector's monitor could actually reach Kafka + toxiproxy
  # to compute the above. False => the widgets should show "probe down"
  # rather than a stale number presenting as real.
  column "probe_healthy" {
    type    = boolean
    null    = false
    default = false
  }

  column "updated_at" {
    type    = timestamptz
    null    = false
    default = sql("now()")
  }

  primary_key {
    columns = [column.id]
  }
}

# -----------------------------------------------------------------------------
# HQ per-link reachability
# -----------------------------------------------------------------------------
# One row per tier link id (an edge id or a region id), written by HQ's link
# monitor from the arrival of each tier's heartbeat. It holds no asset data,
# so it carries no releasability label columns; it is served role-wide like
# edge_buffer_status.
# -----------------------------------------------------------------------------
table "link_status" {
  schema = schema.public

  # The link id.
  column "id" {
    type = text
    null = false
  }

  # One of up / idle / down / unknown.
  column "link_state" {
    type = text
    null = false
  }

  # One of active / idle / unspecified.
  column "traffic" {
    type = text
    null = false
  }

  column "declared_idle" {
    type    = boolean
    null    = false
    default = false
  }

  # Seconds since the last heartbeat arrived; NULL = none seen.
  column "heartbeat_age_s" {
    type = double_precision
    null = true
  }

  column "last_heartbeat_at" {
    type = timestamptz
    null = true
  }

  # The emitting tier's relay lag as last reported; -1 unknown.
  column "bridge_lag" {
    type    = bigint
    null    = false
    default = -1
  }

  column "updated_at" {
    type    = timestamptz
    null    = false
    default = sql("now()")
  }

  primary_key {
    columns = [column.id]
  }

  check "link_status_link_state_check" {
    expr = "link_state IN ('up', 'idle', 'down', 'unknown')"
  }

  check "link_status_traffic_check" {
    expr = "traffic IN ('active', 'idle', 'unspecified')"
  }
}

# -----------------------------------------------------------------------------
# Phase 6b §B: regional rollups (ADR-0023)
# -----------------------------------------------------------------------------
# Three per-region aggregate tables populated by the openddil-projector's new
# region_* handler modules consuming faust-regional's three rolled-up topics
# on redpanda-hq. Each table is keyed by region_id alone — rollups are
# region-level, not per-asset. Wide-JSONB shape for variable-length payloads
# (top-factors, wear-trends components); narrow-column shape for the fixed-
# severity-bucket fleet summary. Row count stays tiny (≤ a handful of
# regions per deployment); ElectricSQL Shapes against region_id stay simple
# for §C.
#
# All three CREATE TABLEs land in one logical schema change: the regional
# rollup tier is one architectural unit (the §B observable claim), so
# Atlas computes one migration for the trio.
# -----------------------------------------------------------------------------

# region-fleet-summary — per-region severity counts. Headline rollup.
# Producer: faust-regional aggregator App. Compaction key: region_id.
table "region_fleet_summary" {
  schema = schema.public

  column "region_id" {
    type = text
    null = false
  }

  # Severity buckets. The aggregator computes per-asset bucket assignment
  # as the WORST of logistics severity and cm-state derived severity, then
  # counts. asset_count is the sum so the UI can render "X of N nominal"
  # without computing it client-side.
  column "nominal" {
    type    = integer
    null    = false
    default = 0
  }
  column "degraded" {
    type    = integer
    null    = false
    default = 0
  }
  column "critical" {
    type    = integer
    null    = false
    default = 0
  }
  column "non_operational" {
    type    = integer
    null    = false
    default = 0
  }

  # ADR-0044 §3: terminal operational-status partitions. nominal/degraded/
  # critical/non_operational above count only assets WITHOUT a terminal
  # operational-status claim; these three count the assets that have one.
  # asset_count is the whole partition (buckets + terminal), so a destroyed
  # asset stays in asset_count. Adopted verbatim from the wire message
  # (faust-regional's aggregator has already done the counting), never
  # re-derived here.
  column "destroyed" {
    type    = integer
    null    = false
    default = 0
  }
  column "deactivated" {
    type    = integer
    null    = false
    default = 0
  }
  column "removed" {
    type    = integer
    null    = false
    default = 0
  }

  column "asset_count" {
    type    = integer
    null    = false
    default = 0
  }

  column "observed_at" {
    type = timestamptz
    null = true
  }

  column "updated_at" {
    type    = timestamptz
    null    = false
    default = sql("now()")
  }

  primary_key {
    columns = [column.region_id]
  }
}

# region-top-factors — per-region top-N constraining factors by frequency.
# Producer: faust-regional aggregator. Wide-JSONB column for the factors
# array because N is variable (default 10 but tunable) and the per-factor
# severity_breakdown map is itself variable-length. UI consumes the JSON
# directly to render the stacked-bar per factor.
table "region_top_factors" {
  schema = schema.public

  column "region_id" {
    type = text
    null = false
  }

  # JSON array shape: [{factor_id, count, severity_breakdown: {LEVEL: count}}].
  # Sorted DESC by count. Empty under cold start — projector handler does NOT
  # write a row until faust-regional has emitted at least once; UI cold-
  # state renders "Awaiting first emission..." in that gap.
  column "factors" {
    type    = jsonb
    null    = false
    default = "[]"
  }

  column "observed_at" {
    type = timestamptz
    null = true
  }

  column "updated_at" {
    type    = timestamptz
    null    = false
    default = sql("now()")
  }

  primary_key {
    columns = [column.region_id]
  }
}

# region-wear-trends — per-region aggregate wear by component.
# Producer: faust-regional aggregator. JSONB array of ComponentWearTrend
# entries; each entry is keyed by (component_id, unit) per the mixed-unit
# handling decision (Q3) — the same component_id appears multiple times if
# the source data uses different units. The aggregator REFUSES to mean
# across mixed units; the UI must render each (component_id, unit) row
# distinctly.
#
# §B ASYMMETRIC COVERAGE: sources from derived-sustainment only (live in
# 6a). asset-telemetry-windows is wired in the fan-in envelope but does
# NOT drive emissions in §B; full-join verification deferred to follow-up
# #11 (sustainment-data test fixtures). Recipe-greenlit pre-build.
table "region_wear_trends" {
  schema = schema.public

  column "region_id" {
    type = text
    null = false
  }

  # JSON array shape: [{component_id, unit, mean_rul_remaining, asset_count}].
  column "components" {
    type    = jsonb
    null    = false
    default = "[]"
  }

  column "observed_at" {
    type = timestamptz
    null = true
  }

  column "updated_at" {
    type    = timestamptz
    null    = false
    default = sql("now()")
  }

  primary_key {
    columns = [column.region_id]
  }
}

# Producer: openddil-projector capability_state handler, from the
# `asset-capability-snapshot` Silver topic. Recipe v3 Sub-phase E.
# Compaction key: asset_id. One row per asset holding the latest
# weapons-capability snapshot; `capabilities` is the per-store array
# stored verbatim as JSONB (the projector handler returns one Write
# per message, so per-store rows would need a multi-Write signature
# change -- the JSONB array keeps the projector model intact while
# still carrying per-store granularity for the UI and the engagement-
# worthiness factor).
table "asset_capability_state" {
  schema = schema.public

  # ADR-0029 coalition releasability labels. Real columns (not JSONB) because
  # the read-path PEP composes a WHERE clause over them on every subscription.
  # Nullable until the labelling backfill completes -- deny-unlabeled must not
  # be enabled before the completeness gate returns zero (ADR-0029 s7).
  column "originator_nation" {
    type = text
    null = true
  }

  column "releasable_to" {
    type = sql("text[]")
    null = true
  }

  column "asset_id" {
    type = text
    null = false
  }

  # JSON array shape: [{capability_id, store_location, store_category,
  # ammo, simulated, accepted_interface, interrupt_other_activities}].
  # One entry per loaded store on the asset.
  column "capabilities" {
    type    = jsonb
    null    = false
    default = "[]"
  }

  column "schema_version" {
    type = text
    null = true
  }
  column "mode" {
    type = text
    null = true
  }

  # Origin-node provenance — see ADR-0022. asset_capability_state is a
  # per-asset projection row, so it carries edge_id/region_id like the
  # other five per-asset tables.
  column "edge_id" {
    type    = text
    null    = false
    default = "edge-01"
  }
  column "region_id" {
    type    = text
    null    = false
    default = "region-01"
  }

  column "observed_at" {
    type = timestamptz
    null = true
  }

  column "updated_at" {
    type    = timestamptz
    null    = false
    default = sql("now()")
  }

  primary_key {
    columns = [column.asset_id]
  }
}

# Phase 9: asset_element_telemetry -- per-element sub-component telemetry
# for multi-element assets, populated by openddil-logistics-sim. See
# migration 20260613000000_phase9_asset_element_telemetry.sql for
# rationale + the multi-profile shape (MRAD first, LTAMDS / Patriot
# to follow via config).
table "asset_element_telemetry" {
  schema = schema.public

  # ADR-0029 coalition releasability labels. Real columns (not JSONB) because
  # the read-path PEP composes a WHERE clause over them on every subscription.
  # Nullable until the labelling backfill completes -- deny-unlabeled must not
  # be enabled before the completeness gate returns zero (ADR-0029 s7).
  column "originator_nation" {
    type = text
    null = true
  }

  column "releasable_to" {
    type = sql("text[]")
    null = true
  }

  column "asset_id" {
    type = text
    null = false
  }

  # Denormalized from elements/operational so per-type filters
  # ("how many MRADs are degraded") don't need JSONB unpacking.
  column "platform_variant" {
    type    = text
    null    = false
    default = ""
  }
  column "profile_name" {
    type    = text
    null    = false
    default = ""
  }

  # JSON array per logistics-sim publisher envelope:
  #   [{element_id, layer_depth, layer_name, health, temp_c, load_pct,
  #     tx_active, rx_active}, ...]
  # element_id format documented in openddil-logistics-sim/README.md;
  # must match the frontend SensorArrayView byte-for-byte.
  column "elements" {
    type    = jsonb
    null    = false
    default = "[]"
  }

  # Mirror of the customer feed's OperationalState (power_state,
  # health_state, actively_transmitting, actively_receiving, degraded).
  # Lets a consumer render the asset-level status banner without
  # unpacking the elements array.
  column "operational" {
    type    = jsonb
    null    = false
    default = "{}"
  }

  column "observed_at" {
    type    = timestamptz
    null    = false
    default = sql("now()")
  }

  # Backfilled from asset_registry. Sim has no edge attribution of its own.
  column "edge_id" {
    type    = text
    null    = false
    default = "edge-unspecified"
  }
  column "region_id" {
    type    = text
    null    = false
    default = "region-unspecified"
  }

  column "updated_at" {
    type    = timestamptz
    null    = false
    default = sql("now()")
  }

  primary_key {
    columns = [column.asset_id]
  }
}

# ADR-0046 v2 s5-6: intake_records -- artifacts returned by a destination
# with intake, by declared kind; the local decision (gate shape plus
# approver subjects) made about each one.
table "intake_records" {
  schema = schema.public

  # Real columns, not nested in body or decision, so a reader finds an
  # artifact's label exactly where every other labelled table in this
  # schema puts it -- one shape for a label, no second place to look for
  # it.
  column "originator_nation" {
    type = text
    null = true
  }

  column "releasable_to" {
    type    = sql("text[]")
    null    = false
    default = sql("'{}'::text[]")
  }

  column "kind" {
    type = text
    null = false
  }
  column "key" {
    type = text
    null = false
  }
  column "owning_tier" {
    type = text
    null = false
  }

  # The artifact as received.
  column "body" {
    type = jsonb
    null = false
  }

  column "body_sha256" {
    type = text
    null = false
  }

  # The full local decision (gate shape plus approver subjects) logged for
  # this artifact.
  column "decision" {
    type = jsonb
    null = false
  }

  column "decided_at" {
    type = timestamptz
    null = false
  }

  primary_key {
    columns = [column.kind, column.key]
  }
}

# One row per Fire or Detonation event_urn on topic `effector-events` (the
# ingest-side DIS Fire/Detonation decode). A Fire appends the row; a matching
# Detonation updates it in place, so "expended" is a straight SUM over this
# table, never a reconciliation between two tables. terminal_state NULL
# means in flight; there is deliberately no "miss" value — DIS carries no
# such event, so an unresolved round is not the same claim as a missed one.
# Excluded from both lifecycle pruning (build_prune_sql) and the
# retention/TTL sweep: expended must never decrease, or `remaining` in
# effector_launcher_counts would rebound upward with no real event behind
# it.
table "effector_launch" {
  schema = schema.public

  column "event_urn" {
    type = text
    null = false
  }

  column "launcher_asset_id" {
    type = text
    null = false
  }

  # The Fire PDU's munition expendable entity, as an asset_id. NULL when the
  # Fire names no distinct expendable (DIS 0:0:0).
  column "munition_asset_id" {
    type = text
    null = true
  }

  # The DIS munition 7-tuple, stored as "k.d.c.cat.sub.spec.extra" — nothing
  # here groups or filters on one element of the tuple independently.
  column "munition_type" {
    type = text
    null = false
  }

  column "quantity" {
    type = int
    null = false
  }

  column "target_asset_id" {
    type = text
    null = true
  }

  column "launched_at" {
    type = timestamptz
    null = false
  }

  # NULL = in flight. Set by a Detonation or by the timeout sweep.
  column "terminal_state" {
    type = text
    null = true
  }

  # Raw DIS detonationResult enum, alongside the mapped terminal_state.
  column "detonation_result" {
    type = int
    null = true
  }

  column "terminated_at" {
    type = timestamptz
    null = true
  }

  # true when a real Detonation resolved a row the timeout sweep had
  # already marked 'unresolved' — a termination event outranks a timeout
  # inference, but the fact that the inference fired first is kept.
  column "late_terminal" {
    type    = bool
    null    = false
    default = false
  }

  # Origin-node provenance — see ADR-0022. Filled from the launcher, the
  # same way tactical_events is.
  column "edge_id" {
    type = text
    null = true
  }

  column "region_id" {
    type = text
    null = true
  }

  # ADR-0029 releasability labels, filled exactly as tactical_events is
  # (20260906000000_labelling_block_asset_bearing_tables.sql): carried from
  # the launcher's own provenance, never derived here. Nullable —
  # deny-unlabeled means an unlabelled launch is a real row, not a defect.
  column "originator_nation" {
    type = text
    null = true
  }

  column "releasable_to" {
    type = sql("text[]")
    null = true
  }

  column "updated_at" {
    type    = timestamptz
    null    = false
    default = sql("now()")
  }

  primary_key {
    columns = [column.event_urn]
  }

  index "idx_effector_launch_launcher_asset_id" {
    columns = [column.launcher_asset_id]
  }

  index "idx_effector_launch_munition_asset_id" {
    columns = [column.munition_asset_id]
  }

  check "effector_launch_terminal_state_check" {
    expr = "terminal_state IN ('entity_impact', 'ground_impact', 'detonated', 'dud', 'other', 'unresolved')"
  }
}

# A launcher's declared load, by exact asset id OR by platform_variant
# (never both for the same munition_type). Reloaded wholesale from
# EFFECTOR_DECLARED_LOAD_PATH at projector startup — there is no producer
# that writes single rows here. Deliberately left out of reset-scenario.sh's
# TABLES array: it is config, reloaded at projector start, not scenario
# state.
table "effector_declared_load" {
  schema = schema.public

  column "load_key" {
    type = text
    null = false
  }

  column "key_kind" {
    type = text
    null = false
  }

  column "munition_type" {
    type = text
    null = false
  }

  column "declared" {
    type = int
    null = false
  }

  primary_key {
    columns = [column.load_key, column.munition_type]
  }

  check "effector_declared_load_key_kind_check" {
    expr = "key_kind IN ('asset', 'variant')"
  }

  check "effector_declared_load_declared_check" {
    expr = "declared >= 0"
  }
}

# Per-(launcher, munition) counts: expended, in flight, unresolved, and
# remaining against the declared load. declared resolves asset-keyed
# before variant-keyed (COALESCE(asset, variant) — asset is the more
# specific fact). remaining is NULL, never 0, when declared is NULL, and is
# never clamped at 0: negative means expended beyond the declared load,
# which is worth keeping visible.
view "effector_launcher_counts" {
  schema = schema.public

  column "launcher_asset_id" {
    type = text
  }
  column "munition_type" {
    type = text
  }
  column "expended" {
    type = bigint
  }
  column "in_flight" {
    type = bigint
  }
  column "unresolved" {
    type = bigint
  }
  column "declared" {
    type = int
  }
  column "remaining" {
    type = bigint
  }

  as = <<-SQL
  WITH "agg" AS (
    SELECT
      "launcher_asset_id",
      "munition_type",
      SUM("quantity")                                          AS "expended",
      COUNT(*) FILTER (WHERE "terminal_state" IS NULL)          AS "in_flight",
      COUNT(*) FILTER (WHERE "terminal_state" = 'unresolved')   AS "unresolved"
    FROM "public"."effector_launch"
    GROUP BY "launcher_asset_id", "munition_type"
  )
  SELECT
      "agg"."launcher_asset_id",
      "agg"."munition_type",
      "agg"."expended",
      "agg"."in_flight",
      "agg"."unresolved",
      COALESCE("dl_asset"."declared", "dl_variant"."declared") AS "declared",
      COALESCE("dl_asset"."declared", "dl_variant"."declared") - "agg"."expended" AS "remaining"
  FROM "agg"
  LEFT JOIN "public"."telemetry_latest_state" AS "tls"
    ON "tls"."asset_id" = "agg"."launcher_asset_id"
  LEFT JOIN "public"."effector_declared_load" AS "dl_asset"
    ON "dl_asset"."key_kind" = 'asset'
    AND "dl_asset"."load_key" = "agg"."launcher_asset_id"
    AND "dl_asset"."munition_type" = "agg"."munition_type"
  LEFT JOIN "public"."effector_declared_load" AS "dl_variant"
    ON "dl_variant"."key_kind" = 'variant'
    AND "dl_variant"."load_key" = "tls"."platform_variant"
    AND "dl_variant"."munition_type" = "agg"."munition_type"
  SQL
}

# A durable record of what the forwarder has already sent (or deliberately
# withheld) to a destination, consulted before every send on a route that
# sets dedupe_field. outcome 'held' is not terminal and never blocks a
# later send; outcome 'delivered' is never downgraded back to 'held'
# (enforced at the one place that writes the row, forwarder.py's
# DeliveredStore). http_status/case_id are only ever set for a 'delivered'
# row.
table "egress_delivered_events" {
  schema = schema.public

  column "route" {
    type = text
    null = false
  }
  column "event_id" {
    type = text
    null = false
  }
  column "outcome" {
    type = text
    null = false
  }
  column "http_status" {
    type = int
    null = true
  }
  column "case_id" {
    type = text
    null = true
  }
  column "topic" {
    type = text
    null = false
  }
  column "partition" {
    type = int
    null = false
  }
  column "kafka_offset" {
    type = bigint
    null = false
  }
  column "first_recorded_at" {
    type = timestamptz
    null = false
  }
  column "recorded_at" {
    type = timestamptz
    null = false
  }

  primary_key {
    columns = [column.route, column.event_id]
  }

  check "egress_delivered_events_outcome_check" {
    expr = "outcome IN ('held', 'delivered')"
  }
}
