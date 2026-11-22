"""openddil_identity: the first slice of ADR-0056 (pure library).

Nothing calls this package yet.

This increment adds the era-2 episode id (episode.py), per ADR-0056 section 5a
(#39), ruled 2026-11-16 with the canonical-JSON seed.
"""

from .declaration import (
    Alias,
    Bind,
    Binding,
    Conflict,
    Mint,
    Replay,
    declaration_ref,
    decide,
)
from .equivalence import (
    ALREADY_MERGED,
    OWNER_MISSING,
    REASON_MISSING,
    SURVIVOR_IS_MERGED,
    Ok,
    Refused,
    check_equivalence,
)
from .episode import EPISODE_NAMESPACE, episode_id, episode_seed
from .era import era
from .ids import (
    GENERATOR,
    GENERATOR_VERSION,
    Identity,
    LookupKey,
    as_iri,
    lookup_key,
    mint_identity,
)

__all__ = [
    "ALREADY_MERGED",
    "Alias",
    "Bind",
    "Binding",
    "Conflict",
    "EPISODE_NAMESPACE",
    "GENERATOR",
    "GENERATOR_VERSION",
    "Identity",
    "LookupKey",
    "Mint",
    "OWNER_MISSING",
    "Ok",
    "REASON_MISSING",
    "Refused",
    "Replay",
    "SURVIVOR_IS_MERGED",
    "as_iri",
    "check_equivalence",
    "declaration_ref",
    "decide",
    "episode_id",
    "episode_seed",
    "era",
    "lookup_key",
    "mint_identity",
]
