-- ADR-0046 s5: maintenance_actions -- the owning tier's local decision about
-- an arriving MaintenanceAction, and the row the maintenance egress gate
-- (source maint-actions-decided, destination system:mmis-stand-in) reads to
-- release a work order toward the maintenance-management stand-in.
--
-- originator_nation / releasable_to are REAL COLUMNS, not nested in
-- work_order or provenance, so the egress gate reads this table's label
-- exactly the way it reads asset_logistics_status's -- one predicate, same
-- clauses, no second label-extraction path for a second record source
-- (ADR-0043). Per ADR-0046 s4 the label is copied from the action's event
-- rather than re-derived, but it is stored flat here for that reason.
--
-- work_order and approval_chain are NOT NULL with no default: every row this
-- table holds is a decided action (ADR-0046 s1, "the decision is stored
-- locally ... so the record of what was approved survives severance"),
-- never a pending placeholder -- OpenDDIL holds no workflow state, so there
-- is no shape for "not yet decided" to default into.
CREATE TABLE "public"."maintenance_actions" (
  "action_id"          text NOT NULL,
  "event_id"           text NOT NULL,
  "asset_id"           text NOT NULL,
  "owning_tier"        text NOT NULL,
  "originator_nation"  text NULL,
  "releasable_to"      text[] NOT NULL DEFAULT '{}'::text[],
  "work_order"         jsonb NOT NULL,
  "approval_chain"     jsonb NOT NULL,
  "provenance"         jsonb NOT NULL DEFAULT '{}'::jsonb,
  "decided_at"         timestamptz NOT NULL,
  PRIMARY KEY ("action_id")
);
