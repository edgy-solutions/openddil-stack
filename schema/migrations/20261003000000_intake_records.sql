-- ADR-0046 v2 s5-6: intake_records -- artifacts returned by a destination
-- with intake, by declared kind; the local decision (gate shape plus
-- approver subjects) made about each one.
--
-- originator_nation / releasable_to are REAL COLUMNS, not nested in body or
-- decision, so a reader finds an artifact's label exactly where every other
-- labelled table in this schema puts it -- one shape for a label, no second
-- place to look for it.
--
-- body and decision are stored whole: body is the artifact as received;
-- decision is the full local decision (gate shape plus approver subjects)
-- logged for it. Primary key is (kind, key) -- an artifact's identity is its
-- declared key within its declared kind, and a later poll of the same
-- artifact updates this row rather than adding a second one.
CREATE TABLE "public"."intake_records" (
  "kind"               text NOT NULL,
  "key"                text NOT NULL,
  "originator_nation"  text NULL,
  "releasable_to"      text[] NOT NULL DEFAULT '{}'::text[],
  "owning_tier"        text NOT NULL,
  "body"               jsonb NOT NULL,
  "body_sha256"        text NOT NULL,
  "decision"           jsonb NOT NULL,
  "decided_at"         timestamptz NOT NULL,
  PRIMARY KEY ("kind", "key")
);

-- Superseded by the generic table above.
DROP TABLE IF EXISTS "public"."maintenance_actions";
