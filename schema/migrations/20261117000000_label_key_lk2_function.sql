-- lk2 label key -- the compile entry points, as pure SQL functions.
--
-- WHAT THIS IS
-- ADR-0055 section 2a (the lk2 canonical form and key: more than one author,
-- a rollup marker, or more than one dimension) and section 2b (the urn:oid:
-- policy id, and the life member that rides in the body outside the key), as
-- pure functions of their arguments. Nothing here reads a table or a setting.
-- It builds on the lk1 functions of 20261115000000 and sorts after them.
--
--   "public"."lk2_policy_ok"(policy_id jsonb)           boolean
--       true for a JSON string that is a urn:oid: id and also a valid
--       policy id; false for anything else, NULL included.
--   "public"."lk2_life_ok"(life jsonb)                  boolean
--       true for an object with exactly CreationDateTime (a valid time) and
--       ReviewDateTime (a valid time or JSON null). A valid time is checked
--       by integer arithmetic, not by a cast.
--   "public"."lk2_compile"(declaration, dims, life)     jsonb
--       the lk2 form of a label, or the reason there is no key.
--   "public"."label_compile"(declaration, dims, life)   jsonb
--       the one stamp entry point: lk1 for a one-author label under a
--       one-dimension nation-with-author declaration, else lk2_compile.
--
-- Both compile functions return one of
--   {"form": "lk1"|"lk2", "canonical", "label_key", "label"}
--   {"no_key": reason, "no_key_dimension": name or null}
-- and never NULL and never raise, whatever the jsonb arguments hold. SQL NULL
-- and JSON null both mean "absent" for declaration and life. life never enters
-- any canonical string or key: an lk1 label that carries life keeps its lk1
-- key (ruled 2026-11-17).
--
-- The declaration is trusted to have been validated as a Declaration when it
-- was loaded. The functions refuse a structurally broken one with
-- declaration_not_lk2 and check what the Python _declaration_problem checks.
-- The key compiler checks format only, never vocabulary.
--
-- Private helpers (lk2_no_key, lk2_time_ok, lk2_join, lk2_distinct,
-- lk2_decl_shape_ok, lk2_check_keys, lk2_parse_nation) carry the lk2_ prefix.
--
-- WHAT THIS IS NOT
-- No label_policy table, no loader, no trigger, no column, no backfill.
--
-- HOW IT IS CHECKED
-- The Python reference (openddil_labels.lk2) and these functions agree
-- because both are tested against one vector table (tests/vectors/lk2.json),
-- not because one was ported from the other. See
-- tests/test_label_key_lk2_sql.py.
--
-- TRANSACTION AND TIMEOUTS
-- Atlas applies with --tx-mode none, so this file carries its own
-- BEGIN/COMMIT: the functions land together or not at all. SET LOCAL holds
-- the timeouts to this transaction only. Nothing here touches a table; the
-- only locks are catalog rows for the functions, so 5 s waiting on a lock and
-- 30 s per statement are far above what it needs (squawk: require-lock-timeout,
-- require-statement-timeout). No function has an EXCEPTION block, which would
-- make it parallel unsafe; every input is guarded instead.

BEGIN;
SET LOCAL lock_timeout = '5s';
SET LOCAL statement_timeout = '30s';

DO $guard$
BEGIN
  IF current_setting('server_version_num')::int < 110000 THEN
    RAISE EXCEPTION 'lk2 needs postgres 11 or later (sha256()); server_version_num=%',
      current_setting('server_version_num');
  END IF;
END
$guard$;

-- The no-key result.
CREATE OR REPLACE FUNCTION "public"."lk2_no_key"(reason text, dimension text)
RETURNS jsonb
LANGUAGE sql IMMUTABLE PARALLEL SAFE
AS $fn$
  SELECT jsonb_build_object(
    'no_key', reason,
    'no_key_dimension', CASE WHEN dimension IS NULL THEN 'null'::jsonb ELSE to_jsonb(dimension) END
  )
$fn$;

CREATE OR REPLACE FUNCTION "public"."lk2_policy_ok"(policy_id jsonb)
RETURNS boolean
LANGUAGE sql IMMUTABLE PARALLEL SAFE
AS $fn$
  SELECT coalesce(
    jsonb_typeof(policy_id) = 'string'
    AND (policy_id #>> '{}') ~ '^urn:oid:[0-2](\.(0|[1-9][0-9]*))+$'
    AND (policy_id #>> '{}') ~ '^[a-z0-9][a-z0-9:._-]{0,62}$',
    false
  )
$fn$;

-- A valid time: the shape, and the fields as a datetime accepts them (year
-- 1..9999, a real day of the month in the Gregorian calendar, hour 0..23,
-- minute 0..59, second 0..59). Integer arithmetic; no timestamp cast.
CREATE OR REPLACE FUNCTION "public"."lk2_time_ok"(t text)
RETURNS boolean
LANGUAGE plpgsql IMMUTABLE PARALLEL SAFE
AS $fn$
DECLARE
  y int;
  mo int;
  d int;
  h int;
  mi int;
  s int;
  dmax int;
BEGIN
  IF t IS NULL
     OR t !~ '^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}(\.[0-9]+)?Z$' THEN
    RETURN false;
  END IF;
  y := substr(t, 1, 4)::int;
  mo := substr(t, 6, 2)::int;
  d := substr(t, 9, 2)::int;
  h := substr(t, 12, 2)::int;
  mi := substr(t, 15, 2)::int;
  s := substr(t, 18, 2)::int;
  IF y < 1 OR mo < 1 OR mo > 12 OR h > 23 OR mi > 59 OR s > 59 OR d < 1 THEN
    RETURN false;
  END IF;
  IF mo IN (1, 3, 5, 7, 8, 10, 12) THEN
    dmax := 31;
  ELSIF mo IN (4, 6, 9, 11) THEN
    dmax := 30;
  ELSIF y % 4 = 0 AND (y % 100 <> 0 OR y % 400 = 0) THEN
    dmax := 29;
  ELSE
    dmax := 28;
  END IF;
  RETURN d <= dmax;
END
$fn$;

CREATE OR REPLACE FUNCTION "public"."lk2_life_ok"(life jsonb)
RETURNS boolean
LANGUAGE plpgsql IMMUTABLE PARALLEL SAFE
AS $fn$
DECLARE
  review jsonb;
BEGIN
  IF life IS NULL OR jsonb_typeof(life) IS DISTINCT FROM 'object' THEN
    RETURN false;
  END IF;
  IF NOT (life ? 'CreationDateTime') OR NOT (life ? 'ReviewDateTime')
     OR (SELECT count(*) FROM jsonb_object_keys(life)) <> 2 THEN
    RETURN false;
  END IF;
  IF jsonb_typeof(life -> 'CreationDateTime') IS DISTINCT FROM 'string'
     OR NOT "public"."lk2_time_ok"(life ->> 'CreationDateTime') THEN
    RETURN false;
  END IF;
  review := life -> 'ReviewDateTime';
  RETURN jsonb_typeof(review) = 'null'
    OR (jsonb_typeof(review) = 'string' AND "public"."lk2_time_ok"(review #>> '{}'));
END
$fn$;

-- The members of a jsonb array of strings joined by ',', in array order.
CREATE OR REPLACE FUNCTION "public"."lk2_join"(arr jsonb)
RETURNS text
LANGUAGE sql IMMUTABLE PARALLEL SAFE
AS $fn$
  SELECT coalesce(
    (SELECT string_agg(x, ',' ORDER BY o)
       FROM jsonb_array_elements_text(arr) WITH ORDINALITY AS t(x, o)),
    ''
  )
$fn$;

-- Sorted ("C" collation, byte order) distinct strings of a jsonb array, or
-- NULL when v is not an array of strings that all match the pattern.
CREATE OR REPLACE FUNCTION "public"."lk2_distinct"(v jsonb, pattern text)
RETURNS text[]
LANGUAGE plpgsql IMMUTABLE PARALLEL SAFE
AS $fn$
BEGIN
  IF jsonb_typeof(v) IS DISTINCT FROM 'array' THEN
    RETURN NULL;
  END IF;
  IF EXISTS (
    SELECT 1 FROM jsonb_array_elements(v) AS e
     WHERE jsonb_typeof(e) IS DISTINCT FROM 'string' OR (e #>> '{}') !~ pattern
  ) THEN
    RETURN NULL;
  END IF;
  RETURN coalesce(
    (SELECT array_agg(y ORDER BY y COLLATE "C")
       FROM (SELECT DISTINCT (e #>> '{}') AS y FROM jsonb_array_elements(v) AS e) s),
    '{}'::text[]
  );
END
$fn$;

-- Structure only: an object, a policy id of the lk1 shape, a non-empty array
-- of dimension objects each with a string name and type and a vocabulary that
-- is an array of strings. Every dimension must also have a has_author member
-- that is a JSON boolean, and true only where its type is 'permissive'; absent,
-- null, a number or a string is refused, as is true on any other type. This
-- mirrors the library's declaration_from_data.
CREATE OR REPLACE FUNCTION "public"."lk2_decl_shape_ok"(declaration jsonb)
RETURNS boolean
LANGUAGE plpgsql IMMUTABLE PARALLEL SAFE
AS $fn$
BEGIN
  IF jsonb_typeof(declaration) IS DISTINCT FROM 'object' THEN
    RETURN false;
  END IF;
  IF jsonb_typeof(declaration -> 'policy_id') IS DISTINCT FROM 'string'
     OR (declaration ->> 'policy_id') !~ '^[a-z0-9][a-z0-9:._-]{0,62}$' THEN
    RETURN false;
  END IF;
  IF jsonb_typeof(declaration -> 'dimensions') IS DISTINCT FROM 'array' THEN
    RETURN false;
  END IF;
  IF jsonb_array_length(declaration -> 'dimensions') = 0 THEN
    RETURN false;
  END IF;
  RETURN NOT EXISTS (
    SELECT 1 FROM jsonb_array_elements(declaration -> 'dimensions') AS e
     WHERE jsonb_typeof(e) IS DISTINCT FROM 'object'
        OR jsonb_typeof(e -> 'name') IS DISTINCT FROM 'string'
        OR jsonb_typeof(e -> 'type') IS DISTINCT FROM 'string'
        OR jsonb_typeof(e -> 'vocabulary') IS DISTINCT FROM 'array'
        OR jsonb_typeof(e -> 'has_author') IS DISTINCT FROM 'boolean'
        OR (e -> 'has_author' = 'true'::jsonb AND (e ->> 'type') <> 'permissive')
        OR EXISTS (
          SELECT 1
            FROM jsonb_array_elements(
                   CASE WHEN jsonb_typeof(e -> 'vocabulary') = 'array'
                        THEN e -> 'vocabulary' ELSE '[]'::jsonb END
                 ) AS x
           WHERE jsonb_typeof(x) IS DISTINCT FROM 'string'
        )
  );
END
$fn$;

-- _check_keys: NULL when dims is an object with no undeclared key and every
-- declared dimension present and not null; else the no-key result. Of several
-- undeclared keys the first in "C" (byte) order is named, as in the library.
CREATE OR REPLACE FUNCTION "public"."lk2_check_keys"(dims jsonb, declared text[], others text[])
RETURNS jsonb
LANGUAGE plpgsql IMMUTABLE PARALLEL SAFE
AS $fn$
DECLARE
  stray text;
  nm text;
BEGIN
  IF jsonb_typeof(dims) IS DISTINCT FROM 'object' THEN
    RETURN "public"."lk2_no_key"('value_malformed', NULL);
  END IF;
  SELECT k INTO stray
    FROM jsonb_object_keys(dims) AS k
   WHERE NOT (k = ANY (declared))
   ORDER BY k COLLATE "C"
   LIMIT 1;
  IF FOUND THEN
    RETURN "public"."lk2_no_key"('dimension_undeclared', stray);
  END IF;
  FOREACH nm IN ARRAY (ARRAY['nation']::text[] || others) LOOP
    IF dims -> nm IS NULL OR jsonb_typeof(dims -> nm) = 'null' THEN
      RETURN "public"."lk2_no_key"('dimension_missing', nm);
    END IF;
  END LOOP;
  RETURN NULL;
END
$fn$;

-- _parse_nation: the no-key result, or
--   {"form": "author"|"rollup", "authors": [..] or null, "release": [..] or null}
CREATE OR REPLACE FUNCTION "public"."lk2_parse_nation"(v jsonb)
RETURNS jsonb
LANGUAGE plpgsql IMMUTABLE PARALLEL SAFE
AS $fn$
DECLARE
  bad jsonb := "public"."lk2_no_key"('value_malformed', 'nation');
  a jsonb;
  rel jsonb;
  authors text[];
  release text[];
BEGIN
  IF jsonb_typeof(v) IS DISTINCT FROM 'object' THEN
    RETURN bad;
  END IF;
  IF EXISTS (
    SELECT 1 FROM jsonb_object_keys(v) AS k WHERE k NOT IN ('author', 'rollup', 'release')
  ) THEN
    RETURN bad;
  END IF;
  IF v ? 'rollup' THEN
    IF v ? 'author' THEN
      RETURN "public"."lk2_no_key"('rollup_with_author', 'nation');
    END IF;
    IF v -> 'rollup' IS DISTINCT FROM 'true'::jsonb THEN
      RETURN bad;
    END IF;
    rel := v -> 'release';
    IF rel IS NULL OR jsonb_typeof(rel) = 'null' THEN
      RETURN "public"."lk2_no_key"('release_absent', 'nation');
    END IF;
    release := "public"."lk2_distinct"(rel, '^[A-Z]{3}$');
    IF release IS NULL THEN
      RETURN bad;
    END IF;
    RETURN jsonb_build_object('form', 'rollup', 'authors', 'null'::jsonb, 'release', to_jsonb(release));
  END IF;
  IF NOT (v ? 'author' AND v ? 'release') THEN
    RETURN bad;
  END IF;
  a := v -> 'author';
  IF jsonb_typeof(a) = 'null' OR a = '""'::jsonb OR a = '[]'::jsonb THEN
    RETURN "public"."lk2_no_key"('author_empty', 'nation');
  END IF;
  IF jsonb_typeof(a) = 'string' THEN
    a := jsonb_build_array(a);
  END IF;
  authors := "public"."lk2_distinct"(a, '^[A-Z]{3}$');
  IF authors IS NULL THEN
    RETURN bad;
  END IF;
  rel := v -> 'release';
  IF jsonb_typeof(rel) = 'null' THEN
    release := NULL;
  ELSE
    release := "public"."lk2_distinct"(rel, '^[A-Z]{3}$');
    IF release IS NULL THEN
      RETURN bad;
    END IF;
  END IF;
  IF cardinality(authors) >= 2 AND release IS NULL THEN
    RETURN "public"."lk2_no_key"('release_absent', 'nation');
  END IF;
  RETURN jsonb_build_object(
    'form', 'author',
    'authors', to_jsonb(authors),
    'release', CASE WHEN release IS NULL THEN 'null'::jsonb ELSE to_jsonb(release) END
  );
END
$fn$;

CREATE OR REPLACE FUNCTION "public"."lk2_compile"(declaration jsonb, dims jsonb, life jsonb)
RETURNS jsonb
LANGUAGE plpgsql IMMUTABLE PARALLEL SAFE
AS $fn$
DECLARE
  policy text;
  declared text[];
  others text[];
  n_authors int;
  chk jsonb;
  parsed jsonb;
  nm text;
  v jsonb;
  vals text[];
  is_level boolean;
  body_dims jsonb;
  parts text[] := '{}'::text[];
  has_life boolean;
  author_text text;
  release_text text;
  canonical text;
  body jsonb;
BEGIN
  IF declaration IS NULL OR jsonb_typeof(declaration) = 'null' THEN
    RETURN "public"."lk2_no_key"('no_policy', NULL);
  END IF;
  IF NOT "public"."lk2_decl_shape_ok"(declaration) THEN
    RETURN "public"."lk2_no_key"('declaration_not_lk2', NULL);
  END IF;
  -- _declaration_problem, in its order.
  IF NOT "public"."lk2_policy_ok"(declaration -> 'policy_id') THEN
    RETURN "public"."lk2_no_key"('policy_id_not_urn_oid', NULL);
  END IF;
  SELECT count(*) INTO n_authors
    FROM jsonb_array_elements(declaration -> 'dimensions') AS d
   WHERE d -> 'has_author' = 'true'::jsonb;
  IF n_authors <> 1 OR NOT EXISTS (
    SELECT 1 FROM jsonb_array_elements(declaration -> 'dimensions') AS d
     WHERE d -> 'has_author' = 'true'::jsonb AND d ->> 'name' = 'nation'
  ) THEN
    RETURN "public"."lk2_no_key"('declaration_not_lk2', NULL);
  END IF;
  IF EXISTS (
    SELECT 1
      FROM jsonb_array_elements(declaration -> 'dimensions') AS d,
           jsonb_array_elements_text(d -> 'vocabulary') AS x
     WHERE (d ->> 'name' = 'nation' AND x !~ '^[A-Z]{3}$')
        OR (d ->> 'name' <> 'nation' AND x !~ '^[A-Za-z0-9][A-Za-z0-9._:-]{0,62}$')
  ) THEN
    RETURN "public"."lk2_no_key"('declaration_not_lk2', NULL);
  END IF;
  policy := declaration ->> 'policy_id';
  SELECT array_agg(d ->> 'name') INTO declared
    FROM jsonb_array_elements(declaration -> 'dimensions') AS d;
  SELECT coalesce(array_agg(nm2 ORDER BY nm2 COLLATE "C"), '{}'::text[]) INTO others
    FROM (SELECT d ->> 'name' AS nm2
            FROM jsonb_array_elements(declaration -> 'dimensions') AS d
           WHERE d ->> 'name' <> 'nation') s;
  -- _core
  chk := "public"."lk2_check_keys"(dims, declared, others);
  IF chk IS NOT NULL THEN
    RETURN chk;
  END IF;
  parsed := "public"."lk2_parse_nation"(dims -> 'nation');
  IF parsed ? 'no_key' THEN
    RETURN parsed;
  END IF;
  body_dims := '{}'::jsonb;
  FOREACH nm IN ARRAY others LOOP
    v := dims -> nm;
    is_level := EXISTS (
      SELECT 1 FROM jsonb_array_elements(declaration -> 'dimensions') AS d
       WHERE d ->> 'name' = nm AND d ->> 'type' = 'hierarchical'
    );
    IF is_level THEN
      IF jsonb_typeof(v) IS DISTINCT FROM 'string'
         OR (v #>> '{}') !~ '^[A-Za-z0-9][A-Za-z0-9._:-]{0,62}$' THEN
        RETURN "public"."lk2_no_key"('value_malformed', nm);
      END IF;
      body_dims := body_dims || jsonb_build_object(nm, v);
      parts := parts || ARRAY[nm, v #>> '{}'];
    ELSE
      vals := "public"."lk2_distinct"(v, '^[A-Za-z0-9][A-Za-z0-9._:-]{0,62}$');
      IF vals IS NULL THEN
        RETURN "public"."lk2_no_key"('value_malformed', nm);
      END IF;
      body_dims := body_dims || jsonb_build_object(nm, to_jsonb(vals));
      parts := parts || ARRAY[nm, array_to_string(vals, ',')];
    END IF;
  END LOOP;
  has_life := life IS NOT NULL AND jsonb_typeof(life) <> 'null';
  IF has_life AND NOT "public"."lk2_life_ok"(life) THEN
    RETURN "public"."lk2_no_key"('life_malformed', NULL);
  END IF;
  IF parsed ->> 'form' = 'author' THEN
    IF cardinality(declared) = 1 AND jsonb_array_length(parsed -> 'authors') = 1 THEN
      RETURN "public"."lk2_no_key"('form_is_lk1', 'nation');
    END IF;
    body_dims := body_dims || jsonb_build_object(
      'nation', jsonb_build_object('author', parsed -> 'authors', 'release', parsed -> 'release')
    );
    author_text := "public"."lk2_join"(parsed -> 'authors');
  ELSE
    body_dims := body_dims || jsonb_build_object(
      'nation', jsonb_build_object('rollup', true, 'release', parsed -> 'release')
    );
    author_text := '@rollup';
  END IF;
  IF jsonb_typeof(parsed -> 'release') = 'null' THEN
    release_text := '~';
  ELSE
    release_text := "public"."lk2_join"(parsed -> 'release');
  END IF;
  canonical := array_to_string(
    ARRAY['lk2', policy, 'nation', author_text, release_text] || parts, '|'
  );
  body := jsonb_build_object('policy', policy, 'v', 2, 'dims', body_dims);
  IF has_life THEN
    body := body || jsonb_build_object(
      'life', jsonb_build_object(
        'CreationDateTime', life -> 'CreationDateTime',
        'ReviewDateTime', life -> 'ReviewDateTime'
      )
    );
  END IF;
  RETURN jsonb_build_object(
    'form', 'lk2',
    'canonical', canonical,
    'label_key', 'lk2:' || encode(sha256(convert_to(canonical, 'UTF8')), 'hex'),
    'label', body
  );
END
$fn$;

CREATE OR REPLACE FUNCTION "public"."label_compile"(declaration jsonb, dims jsonb, life jsonb)
RETURNS jsonb
LANGUAGE plpgsql IMMUTABLE PARALLEL SAFE
AS $fn$
DECLARE
  chk jsonb;
  parsed jsonb;
  author text;
  release text[];
  body jsonb;
BEGIN
  IF declaration IS NULL OR jsonb_typeof(declaration) = 'null' THEN
    RETURN "public"."lk2_no_key"('no_policy', NULL);
  END IF;
  -- _lk1_candidate: one dimension, named nation, with the author.
  IF NOT "public"."lk2_decl_shape_ok"(declaration) THEN
    RETURN "public"."lk2_no_key"('declaration_not_lk2', NULL);
  END IF;
  IF jsonb_array_length(declaration -> 'dimensions') = 1
     AND (declaration -> 'dimensions' -> 0) ->> 'name' = 'nation'
     AND (declaration -> 'dimensions' -> 0) -> 'has_author' = 'true'::jsonb THEN
    chk := "public"."lk2_check_keys"(dims, ARRAY['nation']::text[], '{}'::text[]);
    IF chk IS NOT NULL THEN
      RETURN chk;
    END IF;
    parsed := "public"."lk2_parse_nation"(dims -> 'nation');
    IF parsed ? 'no_key' THEN
      RETURN parsed;
    END IF;
    IF parsed ->> 'form' = 'author' THEN
     IF jsonb_array_length(parsed -> 'authors') = 1 THEN
      IF life IS NOT NULL AND jsonb_typeof(life) <> 'null'
         AND NOT "public"."lk2_life_ok"(life) THEN
        RETURN "public"."lk2_no_key"('life_malformed', NULL);
      END IF;
      author := parsed -> 'authors' ->> 0;
      IF jsonb_typeof(parsed -> 'release') = 'null' THEN
        release := NULL;
      ELSE
        release := ARRAY(SELECT jsonb_array_elements_text(parsed -> 'release'));
      END IF;
      body := "public"."lk1_label"(declaration ->> 'policy_id', author, release);
      IF life IS NOT NULL AND jsonb_typeof(life) <> 'null' THEN
        body := body || jsonb_build_object(
          'life', jsonb_build_object(
            'CreationDateTime', life -> 'CreationDateTime',
            'ReviewDateTime', life -> 'ReviewDateTime'
          )
        );
      END IF;
      RETURN jsonb_build_object(
        'form', 'lk1',
        'canonical', "public"."lk1_canonical"(declaration ->> 'policy_id', author, release),
        'label_key', "public"."lk1_key"(declaration ->> 'policy_id', author, release),
        'label', body
      );
     END IF;
    END IF;
  END IF;
  RETURN "public"."lk2_compile"(declaration, dims, life);
END
$fn$;

COMMENT ON FUNCTION "public"."lk2_policy_ok"(jsonb)
  IS 'True for a JSON string that is a urn:oid: policy id; ADR-0055 section 2b.';
COMMENT ON FUNCTION "public"."lk2_life_ok"(jsonb)
  IS 'True for a well-formed life object (CreationDateTime, ReviewDateTime); ADR-0055 section 2b.';
COMMENT ON FUNCTION "public"."lk2_compile"(jsonb, jsonb, jsonb)
  IS 'lk2 label key, canonical form and body as jsonb, or the no-key reason; ADR-0055 sections 2a and 2b.';
COMMENT ON FUNCTION "public"."label_compile"(jsonb, jsonb, jsonb)
  IS 'The stamp entry point: lk1 for a one-author nation-only label, else lk2; life never enters a key; ADR-0055 sections 2a and 2b.';

COMMIT;
