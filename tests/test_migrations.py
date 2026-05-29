"""
Structural regression guard for schema/migrations/.

These tests don't need a live postgres or the atlas binary — they check
structural invariants over the migration files that the Atlas runtime
checks at apply time. CI catches these BEFORE a deploy attempt would.

The full Atlas validation (atlas migrate validate / atlas migrate lint)
runs in the CI workflow .github/workflows/schema-checks.yml and uses
the official atlas docker image. These Python tests are the
zero-dependency lightweight guard that runs anywhere pytest does.

What's covered:
  * Every .sql file follows the YYYYMMDDHHMMSS_<name>.sql convention
  * atlas.sum exists alongside the migrations
  * atlas.sum lists every .sql file (forgetting to run `atlas migrate
    hash` after adding a migration is the most common slip)
  * Migrations are in strict chronological order (timestamp ascending)
  * Every migration file is non-empty + parseable as plain SQL

If any of these fail, the apply path will reject the migrations with
the same diagnostic — but at CI time, not at deploy time.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).parent.parent
MIGRATIONS_DIR = REPO_ROOT / "schema" / "migrations"
ATLAS_SUM = MIGRATIONS_DIR / "atlas.sum"

# Atlas migration filename convention: 14-digit timestamp + underscore +
# snake_case slug + .sql. Mirrors Atlas's own default.
MIGRATION_NAME_RE = re.compile(r"^(\d{14})_([a-z0-9_]+)\.sql$")


def _migration_files() -> list[Path]:
    """Every .sql file in schema/migrations, sorted alphabetically (which
    is also chronological since the prefix is a timestamp)."""
    return sorted(MIGRATIONS_DIR.glob("*.sql"))


# ---------------------------------------------------------------------------
# Filename convention
# ---------------------------------------------------------------------------

def test_migrations_dir_exists():
    assert MIGRATIONS_DIR.is_dir(), (
        f"Expected migrations directory at {MIGRATIONS_DIR}"
    )


def test_at_least_one_migration_present():
    """Sanity: if this fires, the directory exists but is empty — likely
    a glob path bug in this test, not an actual stack regression."""
    assert _migration_files(), (
        f"No .sql migrations found in {MIGRATIONS_DIR}"
    )


@pytest.mark.parametrize("migration", _migration_files(), ids=lambda p: p.name)
def test_migration_filename_follows_convention(migration: Path) -> None:
    """Atlas requires YYYYMMDDHHMMSS_name.sql. A filename like
    `add-op-state.sql` or `20260529_phase5.sql` (missing the time portion)
    will be rejected by `atlas migrate apply` with an unhelpful error;
    catch the naming mistake at CI time instead."""
    match = MIGRATION_NAME_RE.match(migration.name)
    assert match, (
        f"Migration {migration.name!r} doesn't follow "
        f"YYYYMMDDHHMMSS_<snake_case_slug>.sql.\n"
        f"Atlas requires exactly 14-digit timestamps + underscore + "
        f"lowercase snake_case slug + .sql extension."
    )


# ---------------------------------------------------------------------------
# Chronological ordering
# ---------------------------------------------------------------------------

def test_migrations_in_chronological_order():
    """Timestamps must strictly ascend. Atlas applies in alphabetical
    order, which equals chronological order ONLY if every filename
    starts with a valid timestamp prefix. A migration backdated to fit
    a phase number would silently apply BEFORE later migrations —
    catch it here."""
    files = _migration_files()
    timestamps = []
    for f in files:
        match = MIGRATION_NAME_RE.match(f.name)
        if not match:
            continue  # caught by the per-file convention test
        timestamps.append((int(match.group(1)), f.name))
    for i in range(1, len(timestamps)):
        prev_ts, prev_name = timestamps[i - 1]
        cur_ts, cur_name = timestamps[i]
        assert prev_ts < cur_ts, (
            f"Migrations out of chronological order: {prev_name} (ts={prev_ts}) "
            f">= {cur_name} (ts={cur_ts}). New migrations must use a timestamp "
            f"AFTER the most recent existing one."
        )


def test_no_duplicate_timestamps():
    """Two migrations sharing a timestamp prefix is undefined-order in
    Atlas. The convention test catches this implicitly via uniqueness,
    but call it out with a specific message for clarity."""
    files = _migration_files()
    timestamps_seen: dict[str, str] = {}
    for f in files:
        match = MIGRATION_NAME_RE.match(f.name)
        if not match:
            continue
        ts = match.group(1)
        if ts in timestamps_seen:
            pytest.fail(
                f"Duplicate migration timestamp {ts}: "
                f"{timestamps_seen[ts]} and {f.name}"
            )
        timestamps_seen[ts] = f.name


# ---------------------------------------------------------------------------
# atlas.sum integrity
# ---------------------------------------------------------------------------

def test_atlas_sum_exists():
    """atlas.sum is the integrity manifest. Without it, `atlas migrate
    apply` refuses to run. Forgetting it on the first commit of a
    migration is a common mistake."""
    assert ATLAS_SUM.is_file(), (
        f"atlas.sum missing at {ATLAS_SUM}. Run `atlas migrate hash --dir "
        f"file://schema/migrations` to regenerate."
    )


def test_atlas_sum_lists_every_migration():
    """Every .sql file must have an entry in atlas.sum. Adding a
    migration without re-running `atlas migrate hash` produces an
    atlas.sum that's missing the new file — the apply path rejects
    with 'sum file out of date'."""
    sum_content = ATLAS_SUM.read_text(encoding="utf-8")
    for migration in _migration_files():
        assert migration.name in sum_content, (
            f"Migration {migration.name} has no entry in atlas.sum. "
            f"Run `atlas migrate hash --dir file://schema/migrations` "
            f"to regenerate."
        )


def test_atlas_sum_has_no_stale_entries():
    """Inverse of the above: every entry in atlas.sum must correspond
    to a real migration file. A deleted migration whose sum entry
    wasn't removed gets a 'file removed' error at apply time."""
    sum_lines = ATLAS_SUM.read_text(encoding="utf-8").splitlines()
    actual_filenames = {f.name for f in _migration_files()}
    for line in sum_lines:
        # atlas.sum format:
        #   first line: h1:<sum-of-sums>
        #   subsequent: <filename> h1:<sum>
        if not line.strip() or line.startswith("h1:"):
            continue
        parts = line.split()
        if not parts:
            continue
        candidate_name = parts[0]
        # Only enforce on candidates that look like migration filenames.
        if not candidate_name.endswith(".sql"):
            continue
        assert candidate_name in actual_filenames, (
            f"atlas.sum lists {candidate_name!r} but the file is missing "
            f"from {MIGRATIONS_DIR}. Either restore the file or "
            f"regenerate atlas.sum."
        )


# ---------------------------------------------------------------------------
# Per-file content sanity
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("migration", _migration_files(), ids=lambda p: p.name)
def test_migration_file_is_non_empty(migration: Path) -> None:
    """Empty migration file = nothing to apply, which is almost
    certainly an authoring mistake. Atlas applies an empty file
    silently, which makes the regression hard to spot in logs."""
    assert migration.stat().st_size > 0, (
        f"Migration {migration.name} is empty. Drop the file (and its "
        f"atlas.sum entry) or fill it in."
    )


@pytest.mark.parametrize("migration", _migration_files(), ids=lambda p: p.name)
def test_migration_file_has_some_sql_statement(migration: Path) -> None:
    """At least one of the common DDL/DML keywords should appear in
    every migration. Catches accidentally-commented-out files where
    the operator left a TODO and forgot to write the actual SQL."""
    content = migration.read_text(encoding="utf-8")
    # Strip line comments to avoid matching commented-out examples.
    stripped = "\n".join(
        line for line in content.splitlines()
        if not line.lstrip().startswith("--")
    ).upper()
    keywords = ("CREATE", "ALTER", "DROP", "INSERT", "UPDATE", "DELETE",
                "GRANT", "REVOKE", "TRUNCATE", "COMMENT")
    assert any(kw in stripped for kw in keywords), (
        f"Migration {migration.name} has no executable SQL statement. "
        f"Every uncommented line appears empty or comment-only."
    )
