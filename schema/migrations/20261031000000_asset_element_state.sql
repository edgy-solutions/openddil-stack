-- asset_element_state -- one row per (asset, element), written only when the
-- element changes.
--
-- WHY
-- asset_element_telemetry carried every element of an asset in one jsonb
-- array, rewritten on every tick. That array sits inside a synced shape, so
-- every rewrite re-sent the whole tree to every subscriber of the shape.
-- Change-only state rows replace it: an element that has not crossed its
-- deadband (value class) or flipped a transition-class field writes nothing,
-- and a subscriber receives only the rows that moved.
--
--   value class       temp_c, load_pct        written on a deadband crossing
--   transition class  tx_active, rx_active,   written on any change
--                     moved_by
--
-- observed_at is the sample time of the reading that caused the write, i.e.
-- of the change, not of the latest tick. value_changes counts value-class
-- writes for the element; subtree_changes is the cumulative count over the
-- element's subtree and subtree_changes_window its count in the last window.
--
-- ONE SCHEMA, EVERY TIER
-- The stack has a single schema for all tiers, so this table exists at every
-- tier. Only the edge projector writes it; element rows are not forwarded
-- above the tier that owns the asset. Above that tier the table stays empty
-- and is declared so in the ontology's expected-empty list.
--
-- LABELS AND PROVENANCE
-- originator_nation / releasable_to are typed exactly as on
-- asset_element_telemetry (nullable; deny-unlabeled makes an unlabelled row a
-- real row, not a defect). provenance is the declared jsonb the other
-- non-event tables carry: it names the producing tier and is never parsed.
--
-- DROPPING asset_element_telemetry.elements
-- The array is replaced by the rows above. The remaining per-asset row keeps
-- operational, profile_name, labels and observed_at. Dropping the column
-- invalidates the shapes that selected it, which is intended: those are the
-- shapes that re-sent the whole tree on every tick.
CREATE TABLE "public"."asset_element_state" (
  "asset_id"                  text NOT NULL,
  "element_path"              text NOT NULL,
  "parent_path"               text NULL,
  "depth"                     smallint NOT NULL,
  "layer_name"                text NULL,
  "health"                    double precision NULL,
  "health_band"               text NULL,
  "temp_c"                    double precision NULL,
  "load_pct"                  double precision NULL,
  "tx_active"                 boolean NULL,
  "rx_active"                 boolean NULL,
  "moved_by"                  text NULL,
  "value_changes"             bigint NOT NULL DEFAULT 0,
  "subtree_changes"           bigint NOT NULL DEFAULT 0,
  "subtree_changes_window"    integer NOT NULL DEFAULT 0,
  "observed_at"               timestamptz NOT NULL,
  "updated_at"                timestamptz NOT NULL DEFAULT now(),
  "originator_nation"         text NULL,
  "releasable_to"             text[] NULL,
  "provenance"                jsonb NOT NULL DEFAULT '{}'::jsonb,
  PRIMARY KEY ("asset_id", "element_path")
);

-- Depth-scoped reads for one asset (e.g. the top layers only).
CREATE INDEX "asset_element_state_asset_depth_idx"
  ON "public"."asset_element_state" ("asset_id", "depth");
-- text_pattern_ops so `element_path LIKE 'prefix/%'` snapshot queries use
-- the index regardless of the database collation.
CREATE INDEX "asset_element_state_asset_path_prefix_idx"
  ON "public"."asset_element_state" ("asset_id", "element_path" text_pattern_ops);

ALTER TABLE "public"."asset_element_telemetry" DROP COLUMN "elements";
