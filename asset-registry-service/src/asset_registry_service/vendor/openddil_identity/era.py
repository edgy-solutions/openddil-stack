"""The era read.

Implements first-increment spec section 2.7 (decisions/DESIGN-2026-11-08-adr0056-first-increment.md)
and ADR-0056 section 6. Nothing calls this module yet. The era is never read
from an id: the inputs are times only.
"""

from __future__ import annotations

from datetime import datetime


def _aware(value: object) -> bool:
    return isinstance(value, datetime) and value.tzinfo is not None and value.utcoffset() is not None


def era(bound_at: datetime | None, first_observed: datetime) -> int:
    """2 when the asset was bound at or before first observation, else 1."""
    if bound_at is not None and not _aware(bound_at):
        raise TypeError("bound_at must be a tz-aware datetime or None")
    if not _aware(first_observed):
        raise TypeError("first_observed must be a tz-aware datetime")
    return 2 if bound_at is not None and bound_at <= first_observed else 1
