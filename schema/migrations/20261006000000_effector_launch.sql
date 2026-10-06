-- effector_launch / effector_declared_load / effector_launcher_counts.
--
-- One row per Fire or Detonation event_urn on the effector-events topic
-- (DIS Fire/Detonation PDUs decoded upstream; see dynamic-mappings/
-- dis-effector.yaml in openddil-demo). A Fire appends the row; a matching
-- Detonation updates it in place -- the row never grows a second entry for
-- the same event_urn, so "how many rounds has this launcher expended" is a
-- straight SUM over the table, not a reconciliation between two tables.
--
-- terminal_state NULL means in flight. It is set once a Detonation (or the
-- projector's own timeout sweep) resolves the event, from the small
-- vocabulary in the CHECK constraint below. There is deliberately no
-- "miss" value anywhere in this schema: DIS carries no such event, so an
-- in-flight round that never gets a Detonation is unresolved, not missed --
-- absence is not an outcome.
CREATE TABLE "public"."effector_launch" (
  "event_urn"          text NOT NULL,
  "launcher_asset_id"  text NOT NULL,
  -- The DIS munition 7-tuple, stored as "k.d.c.cat.sub.spec.extra" (same
  -- dotted-tuple convention the wire record uses), not decomposed into
  -- seven columns -- nothing here needs to filter or group on a single
  -- element of the tuple independently of the others.
  "munition_type"      text NOT NULL,
  "quantity"           int NOT NULL,
  "target_asset_id"    text NULL,
  "launched_at"        timestamptz NOT NULL,
  -- NULL = in flight. Set by a Detonation or by the timeout sweep.
  "terminal_state"      text NULL,
  -- Raw DIS detonationResult enum, carried alongside the mapped
  -- terminal_state so a reader who needs the original wire value never has
  -- to invert the map.
  "detonation_result"  int NULL,
  "terminated_at"       timestamptz NULL,
  -- true when a real Detonation resolved a row the timeout sweep had
  -- already marked 'unresolved' -- a termination event outranks a timeout
  -- inference, but the fact that the inference fired first is worth
  -- keeping rather than silently overwriting.
  "late_terminal"       boolean NOT NULL DEFAULT false,
  -- Origin-node provenance -- see ADR-0022 / handlers/base.py
  -- origin_provenance(). Filled from the launcher, the same way
  -- tactical_events is.
  "edge_id"             text NULL,
  "region_id"           text NULL,
  -- ADR-0029 coalition releasability labels, labelled exactly as
  -- tactical_events is (20260906000000_labelling_block_asset_bearing_
  -- tables.sql): carried from the launcher's provenance, never derived
  -- here. Nullable -- an unlabelled launch is a real, deny-unlabeled row,
  -- not a defect.
  "originator_nation"   text NULL,
  "releasable_to"       text[] NULL,
  "updated_at"          timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY ("event_urn"),
  CONSTRAINT "effector_launch_terminal_state_check" CHECK (
    "terminal_state" IN (
      'entity_impact', 'ground_impact', 'detonated', 'dud', 'other',
      'unresolved'
    )
  )
);

CREATE INDEX "idx_effector_launch_launcher_asset_id"
  ON "public"."effector_launch" ("launcher_asset_id");

-- A launcher's declared load, by exact asset id OR by platform_variant
-- (never both for the same munition_type -- the view resolves asset
-- beating variant when both exist). Reloaded wholesale from
-- EFFECTOR_DECLARED_LOAD_PATH at projector startup; there is no producer
-- that writes single rows here, so there is no updated_at to keep current.
CREATE TABLE "public"."effector_declared_load" (
  "load_key"      text NOT NULL,
  "key_kind"      text NOT NULL,
  "munition_type" text NOT NULL,
  "declared"      int NOT NULL,
  PRIMARY KEY ("load_key", "munition_type"),
  CONSTRAINT "effector_declared_load_key_kind_check"
    CHECK ("key_kind" IN ('asset', 'variant')),
  CONSTRAINT "effector_declared_load_declared_check"
    CHECK ("declared" >= 0)
);

-- Per-(launcher, munition) counts: expended, in flight, unresolved, and
-- remaining against the declared load.
--
-- declared resolves asset-keyed before variant-keyed: a launcher's own
-- declared_load row (key_kind='asset', load_key=launcher_asset_id) wins
-- over the platform_variant's row (key_kind='variant',
-- load_key=telemetry_latest_state.platform_variant for that launcher) when
-- both exist, via COALESCE(asset, variant) -- asset is the more specific
-- fact.
--
-- remaining is declared - expended, NULL (never 0) when declared is NULL,
-- because an unknown declared load is not the same claim as "zero rounds
-- declared". It is deliberately NOT clamped at 0: a negative remaining
-- means expended beyond the declared load, which is a fact worth keeping
-- visible rather than hiding under a floor.
CREATE VIEW "public"."effector_launcher_counts" AS
WITH "agg" AS (
  SELECT
    "launcher_asset_id",
    "munition_type",
    SUM("quantity")                                            AS "expended",
    COUNT(*) FILTER (WHERE "terminal_state" IS NULL)            AS "in_flight",
    COUNT(*) FILTER (WHERE "terminal_state" = 'unresolved')      AS "unresolved"
  FROM "public"."effector_launch"
  GROUP BY "launcher_asset_id", "munition_type"
)
SELECT
  "agg"."launcher_asset_id",
  "agg"."munition_type",
  "agg"."expended",
  "agg"."in_flight",
  "agg"."unresolved",
  COALESCE("dl_asset"."declared", "dl_variant"."declared")           AS "declared",
  COALESCE("dl_asset"."declared", "dl_variant"."declared")
    - "agg"."expended"                                               AS "remaining"
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
  AND "dl_variant"."munition_type" = "agg"."munition_type";
