"""The era-2 episode id: a uuid5 of (identity, component, fault code).

ADR-0056 section 5a (#39), ruled 2026-11-16 with the canonical-JSON seed. The
seed is the canonical JSON of the field list, not a "|"-joined string:

    episode_id   := uuid5(EPISODE_NAMESPACE, seed)
    seed         := canonical_json(["episode", identity_iri, component, fault_code])
    identity_iri := "urn:uuid:" + the identity in lowercase canonical form
    canonical_json(v) := json.dumps(v, sort_keys=True, separators=(",", ":"),
                                    ensure_ascii=False, allow_nan=False)
                         encoded as UTF-8 (the encoding declaration_ref uses)

So ("a|b", "c") and ("a", "b|c") give different seeds and different ids: a
field's boundary is the JSON string's, and no character inside a field can
move it.

Kind and owning tier are not in the seed. ``component`` and ``fault_code`` are
hashed exactly as given: no strip, no case fold, no Unicode normalisation. An
empty ``component`` or ``fault_code`` is accepted. A value that cannot be
encoded as UTF-8 (a lone surrogate) is refused with ValueError, because it has
no canonical encoding.

Nothing calls this module yet. No occurrence id is here; the era-2 occurrence id
is not proposed.
"""

from __future__ import annotations

import json
import pathlib
import sys
import uuid

from .ids import Identity, as_iri

# Minted once by openddil_identity.ids.mint_identity. It is never recomputed and
# never derived from a name at run time. Changing it is a third era, and needs a
# decision record.
EPISODE_NAMESPACE = uuid.UUID("01a1e284-45a5-7101-a8c8-9882e4b3e92d")
EPISODE_NAMESPACE_GENERATOR = "openddil_identity.ids.mint_identity"
EPISODE_NAMESPACE_GENERATOR_VERSION = "1"
EPISODE_NAMESPACE_MINTED_AT = "2026-11-16T04:01:21Z"

SEED_FORM = (
    'episode_id := uuid5(EPISODE_NAMESPACE, canonical_json(["episode", identity_iri, component, fault_code]))'
)
CANONICAL_JSON_FORM = (
    'json.dumps(v, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False), UTF-8'
)


def _canonical_json(value: object) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False)


def episode_seed(identity: Identity, component: str, fault_code: str) -> str:
    """The string that is hashed: the canonical JSON of
    ``["episode", <iri>, <component>, <fault_code>]``."""
    if not isinstance(identity, Identity):
        raise TypeError("identity must be an Identity")
    if not isinstance(component, str):
        raise TypeError("component must be a str")
    if not isinstance(fault_code, str):
        raise TypeError("fault_code must be a str")
    for field, value in (("component", component), ("fault_code", fault_code)):
        try:
            value.encode("utf-8")
        except UnicodeEncodeError:
            raise ValueError("%s is not encodable as UTF-8" % field) from None
    return _canonical_json(["episode", as_iri(identity), component, fault_code])


def episode_id(identity: Identity, component: str, fault_code: str) -> uuid.UUID:
    """The era-2 episode id (a version 5 UUID)."""
    return uuid.uuid5(EPISODE_NAMESPACE, episode_seed(identity, component, fault_code))


def generate_episode_vectors() -> dict:
    """Golden vectors for episode_id, as a dict."""
    # Fixed UUIDv7 literals (version nibble 7, RFC 4122 variant); never minted here.
    id_a = "01900000-0000-7000-8000-000000000001"
    id_b = "01900000-0000-7000-8000-000000000002"
    cases = [
        ("plain_1", id_a, "pump", "F001", None),
        ("plain_2", id_a, "valve", "F002", None),
        ("same_identity_other_component", id_a, "motor", "F001", None),
        ("same_identity_other_fault", id_a, "pump", "F003", None),
        ("other_identity_same_inputs", id_b, "pump", "F001", None),
        ("case_upper", id_a, "Pump", "F001", None),
        ("case_lower", id_a, "pump", "F001", None),
        ("leading_whitespace", id_a, " pump", "F001", None),
        ("non_ascii_component", id_a, "pompe-éè", "F001", None),
        ("empty_fault_code", id_a, "pump", "", None),
        ("empty_component", id_a, "", "F001", None),
        ("empty_both", id_a, "", "", None),
        ("json_escapes", id_a, 'a"b\\c/d', "line\nfeed\ttab\u001f", None),
        ("pipe_in_component", id_a, "a|b", "c", "pipe_in_fault_code"),
        ("pipe_in_fault_code", id_a, "a", "b|c", "pipe_in_component"),
    ]
    vectors = []
    for name, ident, component, fault, differs in cases:
        identity = Identity(uuid.UUID(ident))
        v = {
            "name": name,
            "identity": ident,
            "component": component,
            "fault_code": fault,
            "seed": episode_seed(identity, component, fault),
            "episode_id": str(episode_id(identity, component, fault)),
        }
        if differs is not None:
            v["differs_from"] = differs
        vectors.append(v)

    contrast = []
    for asset_id, ident in (("asset-0001", id_a), ("asset-0002", id_b)):
        component, fault = "pump", "F001"
        era1 = uuid.uuid5(uuid.NAMESPACE_URL, "episode|" + asset_id + "|" + component + "|" + fault)
        contrast.append(
            {
                "asset_id": asset_id,
                "component": component,
                "fault_code": fault,
                "era1_id": str(era1),
                "identity": ident,
                "era2_id": str(episode_id(Identity(uuid.UUID(ident)), component, fault)),
            }
        )

    return {
        "namespace": str(EPISODE_NAMESPACE),
        "generator": EPISODE_NAMESPACE_GENERATOR,
        "generator_version": EPISODE_NAMESPACE_GENERATOR_VERSION,
        "minted_at": EPISODE_NAMESPACE_MINTED_AT,
        "seed_form": SEED_FORM,
        "canonical_json": CANONICAL_JSON_FORM,
        "vectors": vectors,
        "era1_contrast": contrast,
    }


def _episode_vectors_path() -> pathlib.Path:
    return pathlib.Path(__file__).resolve().parent / "vectors" / "episode_id.json"


def _write_episode_vectors() -> None:
    path = _episode_vectors_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    text = json.dumps(generate_episode_vectors(), indent=2, sort_keys=True, ensure_ascii=False) + "\n"
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        f.write(text)


if __name__ == "__main__":
    if sys.argv[1:] == ["--write"]:
        _write_episode_vectors()
    else:
        sys.stderr.write("usage: python -m openddil_identity.episode --write\n")
        sys.exit(2)
