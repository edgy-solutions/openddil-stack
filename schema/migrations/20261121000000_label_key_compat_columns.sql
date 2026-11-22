-- label key compatibility slice, the store half: label_policy, label_registry,
-- label_key and label on the 17 tables that carry the nation pair, one trigger.
--
-- WHAT THIS IS
-- DESIGN-2026-11-06 sections 1.1 and 1.3 (ADR-0055 compatibility slice).
--
--   "public"."label_policy"      the deployment's policy id, one row at most
--                                (singleton). Created EMPTY. Nothing in this
--                                migration or this slice writes a row.
--   "public"."label_registry"    one row per label key, the label body beside
--                                it. A cache (ADR-0055 section 2): losing it
--                                costs a backfill, never data.
--   "public"."label_key_of"(originator_nation, releasable_to)    text
--   "public"."label_body_of"(originator_nation, releasable_to)   jsonb
--                                STABLE wrappers: read label_policy, call
--                                lk1_key / lk1_label of 20261115000000. No
--                                policy row gives NULL.
--   "public"."label_key_stamp"() the trigger function.
--
-- Each of the 17 tables (inventory_items, asset_cm_state,
-- asset_logistics_status, telemetry_latest_state, tactical_events,
-- asset_telemetry_windows, parts_availability, asset_capability_state,
-- asset_element_telemetry, asset_element_state, intake_records,
-- effector_launch, effector_resupply, munition_track, asset_declaration,
-- asset_correlation, asset_fielding) gains
--   label_key text NULL, label jsonb NULL, an index on label_key, and
--   a BEFORE INSERT OR UPDATE ... FOR EACH ROW trigger "trg_label_key_stamp".
-- The trigger sets NEW.label_key and NEW.label from NEW.originator_nation and
-- NEW.releasable_to, and when the key is not NULL runs
--   INSERT INTO label_registry ... ON CONFLICT DO NOTHING.
-- A BEFORE trigger changes the row before it is written, so both columns are
-- in the WAL and replicate like any other column. COPY fires row triggers too.
-- Row triggers are skipped under session_replication_role = replica; a grep of
-- every writer found no use of it (see the PR description).
--
-- WHAT THIS IS NOT
-- Not failure_episodes: it already carries label_key and label, written by its
-- projector, with checks. Not a generated column. Not a backfill: existing rows
-- keep label_key NULL until scripts/backfill_label_key.py runs, as a separate
-- named step with its own prediction (section 1.4). Not a policy row and not
-- the PEP switch. A row with a NULL originator_nation gets no key, whatever
-- releasable_to holds (ADR-0055 section 2). A change of policy_id changes every
-- key, which is a backfill, not something this trigger repairs: it re-stamps
-- only rows that are written.
--
-- HOW IT IS CHECKED
-- tests/test_label_key_compat_sql.py: the chain applies on an empty database
-- and on one with rows written before this file; the lk1 vectors through the
-- trigger; no policy row gives NULL keys; every one of the 17 tables has both
-- columns, the index and the trigger; failure_episodes is unchanged.
--
-- TRANSACTION, LOCKS AND TIMEOUTS
-- Atlas applies with --tx-mode none, so this file carries its own
-- BEGIN/COMMIT and the 17 tables change together or not at all. That rules out
-- CREATE INDEX CONCURRENTLY, which cannot run inside a transaction, so each
-- index is a plain CREATE INDEX. It takes a SHARE lock on its table: reads go
-- on, writes to that table wait for the length of the build. ADD COLUMN with
-- no default is a catalog change under a brief ACCESS EXCLUSIVE lock, and
-- CREATE TRIGGER takes SHARE ROW EXCLUSIVE. lock_timeout 5 s fails the file
-- cleanly if a table is held; statement_timeout 300 s bounds one index build
-- (squawk: require-lock-timeout, require-statement-timeout; the plain index
-- statements carry squawk-ignore for require-concurrent-index-creation).

BEGIN;
SET LOCAL lock_timeout = '5s';
SET LOCAL statement_timeout = '300s';

DO $guard$
BEGIN
  IF current_setting('server_version_num')::int < 110000 THEN
    RAISE EXCEPTION 'label key compat needs postgres 11 or later (sha256()); server_version_num=%',
      current_setting('server_version_num');
  END IF;
END
$guard$;

CREATE TABLE IF NOT EXISTS "public"."label_policy" (
  "singleton" boolean NOT NULL DEFAULT true,
  "policy_id" text NOT NULL,
  "declared_by" text NOT NULL,
  "declared_at" timestamptz NOT NULL,
  PRIMARY KEY ("singleton"),
  CONSTRAINT "label_policy_singleton_check" CHECK ("singleton"),
  CONSTRAINT "label_policy_policy_id_check" CHECK ("policy_id" ~ '^[a-z0-9][a-z0-9:._-]{0,62}$')
);

CREATE TABLE IF NOT EXISTS "public"."label_registry" (
  "label_key" text NOT NULL,
  "label" jsonb NOT NULL,
  "first_seen" timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY ("label_key")
);

-- No policy row: the subquery returns no row and the function returns NULL.
CREATE OR REPLACE FUNCTION "public"."label_key_of"(originator_nation text, releasable_to text[])
RETURNS text
LANGUAGE sql STABLE
AS $fn$
  SELECT "public"."lk1_key"(p.policy_id, originator_nation, releasable_to)
    FROM "public"."label_policy" p
   WHERE p.singleton
$fn$;

CREATE OR REPLACE FUNCTION "public"."label_body_of"(originator_nation text, releasable_to text[])
RETURNS jsonb
LANGUAGE sql STABLE
AS $fn$
  SELECT "public"."lk1_label"(p.policy_id, originator_nation, releasable_to)
    FROM "public"."label_policy" p
   WHERE p.singleton
$fn$;

-- The one trigger function. It reads NEW.originator_nation and
-- NEW.releasable_to by name, so it is attached only to tables that have both.
CREATE OR REPLACE FUNCTION "public"."label_key_stamp"()
RETURNS trigger
LANGUAGE plpgsql
AS $fn$
BEGIN
  NEW.label_key := "public"."label_key_of"(NEW.originator_nation, NEW.releasable_to);
  IF NEW.label_key IS NULL THEN
    NEW.label := NULL;
  ELSE
    NEW.label := "public"."label_body_of"(NEW.originator_nation, NEW.releasable_to);
    INSERT INTO "public"."label_registry" ("label_key", "label")
    VALUES (NEW.label_key, NEW.label)
    ON CONFLICT ("label_key") DO NOTHING;
  END IF;
  RETURN NEW;
END
$fn$;

COMMENT ON TABLE "public"."label_policy"
  IS 'The deployment policy id, one row at most. Created empty; written from the deployment bundle. DESIGN-2026-11-06 section 1.1.';
COMMENT ON TABLE "public"."label_registry"
  IS 'One row per label key; a cache, rebuilt by the backfill. DESIGN-2026-11-06 section 1.3.';
COMMENT ON FUNCTION "public"."label_key_stamp"()
  IS 'Sets label_key and label from originator_nation and releasable_to; registers the key. DESIGN-2026-11-06 section 1.3.';

-- inventory_items
ALTER TABLE "public"."inventory_items"
  ADD COLUMN IF NOT EXISTS "label_key" text NULL,
  ADD COLUMN IF NOT EXISTS "label" jsonb NULL;
-- squawk-ignore require-concurrent-index-creation
CREATE INDEX IF NOT EXISTS "idx_inventory_items_label_key" ON "public"."inventory_items" ("label_key");
CREATE TRIGGER "trg_label_key_stamp"
  BEFORE INSERT OR UPDATE ON "public"."inventory_items"
  FOR EACH ROW EXECUTE FUNCTION "public"."label_key_stamp"();

-- asset_cm_state
ALTER TABLE "public"."asset_cm_state"
  ADD COLUMN IF NOT EXISTS "label_key" text NULL,
  ADD COLUMN IF NOT EXISTS "label" jsonb NULL;
-- squawk-ignore require-concurrent-index-creation
CREATE INDEX IF NOT EXISTS "idx_asset_cm_state_label_key" ON "public"."asset_cm_state" ("label_key");
CREATE TRIGGER "trg_label_key_stamp"
  BEFORE INSERT OR UPDATE ON "public"."asset_cm_state"
  FOR EACH ROW EXECUTE FUNCTION "public"."label_key_stamp"();

-- asset_logistics_status
ALTER TABLE "public"."asset_logistics_status"
  ADD COLUMN IF NOT EXISTS "label_key" text NULL,
  ADD COLUMN IF NOT EXISTS "label" jsonb NULL;
-- squawk-ignore require-concurrent-index-creation
CREATE INDEX IF NOT EXISTS "idx_asset_logistics_status_label_key" ON "public"."asset_logistics_status" ("label_key");
CREATE TRIGGER "trg_label_key_stamp"
  BEFORE INSERT OR UPDATE ON "public"."asset_logistics_status"
  FOR EACH ROW EXECUTE FUNCTION "public"."label_key_stamp"();

-- telemetry_latest_state
ALTER TABLE "public"."telemetry_latest_state"
  ADD COLUMN IF NOT EXISTS "label_key" text NULL,
  ADD COLUMN IF NOT EXISTS "label" jsonb NULL;
-- squawk-ignore require-concurrent-index-creation
CREATE INDEX IF NOT EXISTS "idx_telemetry_latest_state_label_key" ON "public"."telemetry_latest_state" ("label_key");
CREATE TRIGGER "trg_label_key_stamp"
  BEFORE INSERT OR UPDATE ON "public"."telemetry_latest_state"
  FOR EACH ROW EXECUTE FUNCTION "public"."label_key_stamp"();

-- tactical_events
ALTER TABLE "public"."tactical_events"
  ADD COLUMN IF NOT EXISTS "label_key" text NULL,
  ADD COLUMN IF NOT EXISTS "label" jsonb NULL;
-- squawk-ignore require-concurrent-index-creation
CREATE INDEX IF NOT EXISTS "idx_tactical_events_label_key" ON "public"."tactical_events" ("label_key");
CREATE TRIGGER "trg_label_key_stamp"
  BEFORE INSERT OR UPDATE ON "public"."tactical_events"
  FOR EACH ROW EXECUTE FUNCTION "public"."label_key_stamp"();

-- asset_telemetry_windows
ALTER TABLE "public"."asset_telemetry_windows"
  ADD COLUMN IF NOT EXISTS "label_key" text NULL,
  ADD COLUMN IF NOT EXISTS "label" jsonb NULL;
-- squawk-ignore require-concurrent-index-creation
CREATE INDEX IF NOT EXISTS "idx_asset_telemetry_windows_label_key" ON "public"."asset_telemetry_windows" ("label_key");
CREATE TRIGGER "trg_label_key_stamp"
  BEFORE INSERT OR UPDATE ON "public"."asset_telemetry_windows"
  FOR EACH ROW EXECUTE FUNCTION "public"."label_key_stamp"();

-- parts_availability
ALTER TABLE "public"."parts_availability"
  ADD COLUMN IF NOT EXISTS "label_key" text NULL,
  ADD COLUMN IF NOT EXISTS "label" jsonb NULL;
-- squawk-ignore require-concurrent-index-creation
CREATE INDEX IF NOT EXISTS "idx_parts_availability_label_key" ON "public"."parts_availability" ("label_key");
CREATE TRIGGER "trg_label_key_stamp"
  BEFORE INSERT OR UPDATE ON "public"."parts_availability"
  FOR EACH ROW EXECUTE FUNCTION "public"."label_key_stamp"();

-- asset_capability_state
ALTER TABLE "public"."asset_capability_state"
  ADD COLUMN IF NOT EXISTS "label_key" text NULL,
  ADD COLUMN IF NOT EXISTS "label" jsonb NULL;
-- squawk-ignore require-concurrent-index-creation
CREATE INDEX IF NOT EXISTS "idx_asset_capability_state_label_key" ON "public"."asset_capability_state" ("label_key");
CREATE TRIGGER "trg_label_key_stamp"
  BEFORE INSERT OR UPDATE ON "public"."asset_capability_state"
  FOR EACH ROW EXECUTE FUNCTION "public"."label_key_stamp"();

-- asset_element_telemetry
ALTER TABLE "public"."asset_element_telemetry"
  ADD COLUMN IF NOT EXISTS "label_key" text NULL,
  ADD COLUMN IF NOT EXISTS "label" jsonb NULL;
-- squawk-ignore require-concurrent-index-creation
CREATE INDEX IF NOT EXISTS "idx_asset_element_telemetry_label_key" ON "public"."asset_element_telemetry" ("label_key");
CREATE TRIGGER "trg_label_key_stamp"
  BEFORE INSERT OR UPDATE ON "public"."asset_element_telemetry"
  FOR EACH ROW EXECUTE FUNCTION "public"."label_key_stamp"();

-- asset_element_state
ALTER TABLE "public"."asset_element_state"
  ADD COLUMN IF NOT EXISTS "label_key" text NULL,
  ADD COLUMN IF NOT EXISTS "label" jsonb NULL;
-- squawk-ignore require-concurrent-index-creation
CREATE INDEX IF NOT EXISTS "idx_asset_element_state_label_key" ON "public"."asset_element_state" ("label_key");
CREATE TRIGGER "trg_label_key_stamp"
  BEFORE INSERT OR UPDATE ON "public"."asset_element_state"
  FOR EACH ROW EXECUTE FUNCTION "public"."label_key_stamp"();

-- intake_records
ALTER TABLE "public"."intake_records"
  ADD COLUMN IF NOT EXISTS "label_key" text NULL,
  ADD COLUMN IF NOT EXISTS "label" jsonb NULL;
-- squawk-ignore require-concurrent-index-creation
CREATE INDEX IF NOT EXISTS "idx_intake_records_label_key" ON "public"."intake_records" ("label_key");
CREATE TRIGGER "trg_label_key_stamp"
  BEFORE INSERT OR UPDATE ON "public"."intake_records"
  FOR EACH ROW EXECUTE FUNCTION "public"."label_key_stamp"();

-- effector_launch
ALTER TABLE "public"."effector_launch"
  ADD COLUMN IF NOT EXISTS "label_key" text NULL,
  ADD COLUMN IF NOT EXISTS "label" jsonb NULL;
-- squawk-ignore require-concurrent-index-creation
CREATE INDEX IF NOT EXISTS "idx_effector_launch_label_key" ON "public"."effector_launch" ("label_key");
CREATE TRIGGER "trg_label_key_stamp"
  BEFORE INSERT OR UPDATE ON "public"."effector_launch"
  FOR EACH ROW EXECUTE FUNCTION "public"."label_key_stamp"();

-- effector_resupply
ALTER TABLE "public"."effector_resupply"
  ADD COLUMN IF NOT EXISTS "label_key" text NULL,
  ADD COLUMN IF NOT EXISTS "label" jsonb NULL;
-- squawk-ignore require-concurrent-index-creation
CREATE INDEX IF NOT EXISTS "idx_effector_resupply_label_key" ON "public"."effector_resupply" ("label_key");
CREATE TRIGGER "trg_label_key_stamp"
  BEFORE INSERT OR UPDATE ON "public"."effector_resupply"
  FOR EACH ROW EXECUTE FUNCTION "public"."label_key_stamp"();

-- munition_track
ALTER TABLE "public"."munition_track"
  ADD COLUMN IF NOT EXISTS "label_key" text NULL,
  ADD COLUMN IF NOT EXISTS "label" jsonb NULL;
-- squawk-ignore require-concurrent-index-creation
CREATE INDEX IF NOT EXISTS "idx_munition_track_label_key" ON "public"."munition_track" ("label_key");
CREATE TRIGGER "trg_label_key_stamp"
  BEFORE INSERT OR UPDATE ON "public"."munition_track"
  FOR EACH ROW EXECUTE FUNCTION "public"."label_key_stamp"();

-- asset_declaration
ALTER TABLE "public"."asset_declaration"
  ADD COLUMN IF NOT EXISTS "label_key" text NULL,
  ADD COLUMN IF NOT EXISTS "label" jsonb NULL;
-- squawk-ignore require-concurrent-index-creation
CREATE INDEX IF NOT EXISTS "idx_asset_declaration_label_key" ON "public"."asset_declaration" ("label_key");
CREATE TRIGGER "trg_label_key_stamp"
  BEFORE INSERT OR UPDATE ON "public"."asset_declaration"
  FOR EACH ROW EXECUTE FUNCTION "public"."label_key_stamp"();

-- asset_correlation
ALTER TABLE "public"."asset_correlation"
  ADD COLUMN IF NOT EXISTS "label_key" text NULL,
  ADD COLUMN IF NOT EXISTS "label" jsonb NULL;
-- squawk-ignore require-concurrent-index-creation
CREATE INDEX IF NOT EXISTS "idx_asset_correlation_label_key" ON "public"."asset_correlation" ("label_key");
CREATE TRIGGER "trg_label_key_stamp"
  BEFORE INSERT OR UPDATE ON "public"."asset_correlation"
  FOR EACH ROW EXECUTE FUNCTION "public"."label_key_stamp"();

-- asset_fielding
ALTER TABLE "public"."asset_fielding"
  ADD COLUMN IF NOT EXISTS "label_key" text NULL,
  ADD COLUMN IF NOT EXISTS "label" jsonb NULL;
-- squawk-ignore require-concurrent-index-creation
CREATE INDEX IF NOT EXISTS "idx_asset_fielding_label_key" ON "public"."asset_fielding" ("label_key");
CREATE TRIGGER "trg_label_key_stamp"
  BEFORE INSERT OR UPDATE ON "public"."asset_fielding"
  FOR EACH ROW EXECUTE FUNCTION "public"."label_key_stamp"();

COMMIT;
