-- lk1 label key -- the canonical form and the key, as pure SQL functions.
--
-- WHAT THIS IS
-- The lk1 canonical form and key from DESIGN-2026-11-06 section 1.2, as pure
-- functions of their arguments (policy id, originator nation, releasable-to
-- nations). Nothing here reads a table or a setting.
--
--   "public"."lk1_canonical"(policy_id, originator_nation, releasable_to)
--       'lk1|<policy>|nation|<originator>|<tail>' or NULL when there is no
--       key. tail is '~' when releasable_to is absent (NULL), '' when it is
--       declared empty, otherwise the distinct nations sorted in "C"
--       collation and joined by ','. A policy_id that is non-empty but
--       malformed raises 'lk1: invalid policy_id'.
--   "public"."lk1_key"(...)       'lk1:' || sha256 hex of the canonical form.
--   "public"."lk1_label"(...)     the label body as jsonb, NULL without a key.
--   "public"."lk1_sorted_release"(text[])
--       the sorted distinct array both of the above use for the tail and the
--       body's release list (private helper).
--
-- WHAT THIS IS NOT
-- No label_policy table, no trigger, no column, no backfill: those are the
-- compatibility slice's later steps. The STABLE wrapper that reads
-- label_policy comes with that table.
--
-- HOW IT IS CHECKED
-- The Python reference (openddil_labels.lk1) and these functions agree
-- because both are tested against one vector table
-- (tests/vectors/lk1.json), not because one was ported from the other.
-- See tests/test_label_key_lk1_sql.py.

DO $guard$
BEGIN
  IF current_setting('server_version_num')::int < 110000 THEN
    RAISE EXCEPTION 'lk1 needs postgres 11 or later (sha256()); server_version_num=%',
      current_setting('server_version_num');
  END IF;
END
$guard$;

-- Sorted distinct nations. NULL in -> empty array out. Sorting is in "C"
-- collation (byte order), independent of the database collation. DISTINCT is
-- applied in a subquery so the ORDER BY expression need not appear in an
-- aggregate argument list.
CREATE OR REPLACE FUNCTION "public"."lk1_sorted_release"(releasable_to text[])
RETURNS text[]
LANGUAGE sql IMMUTABLE PARALLEL SAFE
AS $fn$
  SELECT coalesce(
    (SELECT array_agg(y ORDER BY y COLLATE "C")
       FROM (SELECT DISTINCT x AS y FROM unnest(releasable_to) AS t(x)) s),
    '{}'::text[]
  )
$fn$;

CREATE OR REPLACE FUNCTION "public"."lk1_canonical"(
  policy_id text,
  originator_nation text,
  releasable_to text[]
)
RETURNS text
LANGUAGE plpgsql IMMUTABLE PARALLEL SAFE
AS $fn$
DECLARE
  tail text;
  bad boolean;
BEGIN
  IF policy_id IS NULL OR policy_id = '' THEN
    RETURN NULL;
  END IF;
  IF policy_id !~ '^[a-z0-9][a-z0-9:._-]{0,62}$' THEN
    RAISE EXCEPTION 'lk1: invalid policy_id' USING ERRCODE = '22023';
  END IF;
  IF originator_nation IS NULL OR originator_nation = '' THEN
    RETURN NULL;
  END IF;
  IF originator_nation !~ '^[A-Z]{3}$' THEN
    RETURN NULL;
  END IF;
  IF releasable_to IS NULL THEN
    tail := '~';
  ELSE
    SELECT EXISTS (
      SELECT 1 FROM unnest(releasable_to) AS t(x)
       WHERE x IS NULL OR x !~ '^[A-Z]{3}$'
    ) INTO bad;
    IF bad THEN
      RETURN NULL;
    END IF;
    tail := array_to_string("public"."lk1_sorted_release"(releasable_to), ',');
  END IF;
  RETURN 'lk1|' || policy_id || '|nation|' || originator_nation || '|' || tail;
END
$fn$;

CREATE OR REPLACE FUNCTION "public"."lk1_key"(
  policy_id text,
  originator_nation text,
  releasable_to text[]
)
RETURNS text
LANGUAGE sql IMMUTABLE PARALLEL SAFE
AS $fn$
  SELECT 'lk1:' || encode(
    sha256(convert_to("public"."lk1_canonical"(policy_id, originator_nation, releasable_to), 'UTF8')),
    'hex'
  )
$fn$;

CREATE OR REPLACE FUNCTION "public"."lk1_label"(
  policy_id text,
  originator_nation text,
  releasable_to text[]
)
RETURNS jsonb
LANGUAGE sql IMMUTABLE PARALLEL SAFE
AS $fn$
  SELECT CASE
    WHEN "public"."lk1_canonical"(policy_id, originator_nation, releasable_to) IS NULL THEN NULL
    ELSE jsonb_build_object(
      'policy', policy_id,
      'v', 1,
      'dims', jsonb_build_object(
        'nation', jsonb_build_object(
          'author', originator_nation,
          'release', CASE
            WHEN releasable_to IS NULL THEN NULL
            ELSE to_jsonb("public"."lk1_sorted_release"(releasable_to))
          END
        )
      )
    )
  END
$fn$;

COMMENT ON FUNCTION "public"."lk1_sorted_release"(text[])
  IS 'Sorted ("C") distinct nations for the lk1 tail and body; DESIGN-2026-11-06 section 1.2.';
COMMENT ON FUNCTION "public"."lk1_canonical"(text, text, text[])
  IS 'lk1 canonical form, NULL when there is no key; DESIGN-2026-11-06 section 1.2.';
COMMENT ON FUNCTION "public"."lk1_key"(text, text, text[])
  IS 'lk1 label key: lk1: plus sha256 hex of the canonical form; DESIGN-2026-11-06 section 1.2.';
COMMENT ON FUNCTION "public"."lk1_label"(text, text, text[])
  IS 'lk1 label body as jsonb, NULL when there is no key; DESIGN-2026-11-06 section 1.2.';
