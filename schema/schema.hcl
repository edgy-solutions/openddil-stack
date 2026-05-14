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
# -----------------------------------------------------------------------------
table "inventory_items" {
  schema = schema.public

  column "id" {
    type    = uuid
    default = sql("gen_random_uuid()")
  }

  column "name" {
    type = varchar(255)
    null = false
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

  column "asset_id" {
    type = text
    null = false
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

  column "asset_id" {
    type = text
    null = false
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

  column "asset_id" {
    type = text
    null = false
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
