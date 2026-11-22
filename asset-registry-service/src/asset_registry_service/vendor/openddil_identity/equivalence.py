"""The pure refusals of an owned equivalence.

Implements first-increment spec section 2.8 (decisions/DESIGN-2026-11-08-adr0056-first-increment.md).
Nothing calls this module yet. It only checks a declared equivalence; it never
proposes or ranks one.
"""

from __future__ import annotations

from dataclasses import dataclass

from .ids import Identity

OWNER_MISSING = "owner_missing"
REASON_MISSING = "reason_missing"
SURVIVOR_IS_MERGED = "survivor_is_merged"
ALREADY_MERGED = "already_merged"


@dataclass(frozen=True)
class Ok:
    pass


@dataclass(frozen=True)
class Refused:
    reason: str


def _blank(value: object) -> bool:
    return not isinstance(value, str) or not value.strip()


def check_equivalence(
    survivor: Identity,
    merged: Identity,
    owner: str,
    reason: str,
    merged_already: bool,
) -> Ok | Refused:
    if not isinstance(survivor, Identity) or not isinstance(merged, Identity):
        raise TypeError("survivor and merged must be Identity")
    if _blank(owner):
        return Refused(OWNER_MISSING)
    if _blank(reason):
        return Refused(REASON_MISSING)
    if survivor == merged:
        return Refused(SURVIVOR_IS_MERGED)
    if merged_already:
        return Refused(ALREADY_MERGED)
    return Ok()
