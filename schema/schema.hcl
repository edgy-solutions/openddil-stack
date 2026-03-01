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
