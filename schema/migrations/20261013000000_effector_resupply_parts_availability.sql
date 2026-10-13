-- effector_resupply: one row per supply line of a DIS Resupply Received on
-- the effector-events topic. Append-only; excluded from pruning and TTL for
-- the same reason as effector_launch (a pruned resupply would make a
-- replayed remaining wrong). Labelled from the launcher, stamped at ingress,
-- never derived here.
CREATE TABLE "public"."effector_resupply" (
  "event_urn"          text NOT NULL,
  "launcher_asset_id"  text NOT NULL,
  "supplier_asset_id"  text NULL,
  -- "k.d.c.cat.sub.spec.extra", same form as effector_launch.
  "munition_type"      text NOT NULL,
  "quantity"           int NOT NULL,
  "received_at"        timestamptz NOT NULL,
  "edge_id"            text NULL,
  "region_id"          text NULL,
  "originator_nation"  text NULL,
  "releasable_to"      text[] NULL,
  "ingested_at"        timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY ("event_urn", "munition_type"),
  CONSTRAINT "effector_resupply_quantity_check" CHECK (quantity >= 0)
);
-- Create index "idx_effector_resupply_launcher_asset_id" to table: "effector_resupply"
CREATE INDEX "idx_effector_resupply_launcher_asset_id" ON "public"."effector_resupply" ("launcher_asset_id");

-- parts_availability: the current row per (site, part_ref) of the
-- parts-availability stand-in. Not a maintenance-system inventory; published
-- at HQ only. Absent keys stay NULL (unknown is not zero).
CREATE TABLE "public"."parts_availability" (
  "site"                     text NOT NULL,
  "part_ref"                 text NOT NULL,
  "on_hand"                  int NULL,
  "lead_time_days"           double precision NULL,
  "nearest_site_with_stock"  text NULL,
  "nearest_on_hand"          int NULL,
  "source"                   text NULL,
  "observed_at"              timestamptz NOT NULL,
  "originator_nation"        text NULL,
  "releasable_to"            text[] NULL,
  "updated_at"               timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY ("site", "part_ref")
);
