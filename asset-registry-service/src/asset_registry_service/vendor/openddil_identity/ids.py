"""Identity and lookup-key types, and the UUIDv7 generator.

Implements ADR-0056 sections 1, 3 and 6, and the first-increment spec
sections 1.1 and 2.5 (decisions/DESIGN-2026-11-08-adr0056-first-increment.md).
Nothing calls this module yet.

The first-increment spec 2.5 puts ``mint_identity()`` in the registry package;
here it is in contracts ``lib/``, so the registry would import it and
``GENERATOR`` names this module. That change to the spec's wording is a ruling
owed to the project owner.

There is deliberately no function that reads a time out of an identity
(ADR-0056 section 1).
"""

from __future__ import annotations

import os
import threading
import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass

GENERATOR = "openddil_identity.ids.mint_identity"
GENERATOR_VERSION = "1"


def _check_uuid(value: object, version: int) -> None:
    if not isinstance(value, uuid.UUID):
        raise TypeError("value must be a uuid.UUID")
    if value.variant != uuid.RFC_4122 or value.version != version:
        raise ValueError(f"value must be an RFC 4122 UUID version {version}")


@dataclass(frozen=True)
class Identity:
    """The one identity of a thing: a UUIDv7."""

    value: uuid.UUID

    def __post_init__(self) -> None:
        _check_uuid(self.value, 7)


@dataclass(frozen=True)
class LookupKey:
    """A uuid5 of (scheme namespace, alias value). Never an identity."""

    value: uuid.UUID

    def __post_init__(self) -> None:
        _check_uuid(self.value, 5)


_MASK_62 = (1 << 62) - 1


class _Minter:
    """RFC 9562 section 5.7 UUIDv7 with a section 6.2 method 1 counter."""

    def __init__(self, clock_ms: Callable[[], int], urandom: Callable[[int], bytes]):
        self._clock_ms = clock_ms
        self._urandom = urandom
        self._lock = threading.Lock()
        self._last_ms = -1
        self._counter = 0

    def _seed(self) -> int:
        # Top bit clear (11 bits) leaves headroom before the 12-bit counter overflows.
        return int.from_bytes(self._urandom(2), "big") & 0x7FF

    def mint(self) -> Identity:
        with self._lock:
            now = self._clock_ms()
            if now > self._last_ms:
                self._last_ms = now
                self._counter = self._seed()
            else:
                # Same millisecond, or the clock went back: keep the last ms.
                self._counter += 1
                if self._counter > 0xFFF:
                    self._last_ms += 1
                    self._counter = self._seed()
            ms = self._last_ms
            counter = self._counter
            rand_b = int.from_bytes(self._urandom(8), "big") & _MASK_62
        value = (
            ((ms & ((1 << 48) - 1)) << 80)
            | (0b0111 << 76)
            | (counter << 64)
            | (0b10 << 62)
            | rand_b
        )
        return Identity(uuid.UUID(int=value))


_MINTER = _Minter(lambda: time.time_ns() // 1_000_000, os.urandom)


def mint_identity() -> Identity:
    """Mint a new UUIDv7 identity; one process's ids strictly increase."""
    return _MINTER.mint()


def as_iri(identity: Identity) -> str:
    if not isinstance(identity, Identity):
        raise TypeError("as_iri takes an Identity")
    return "urn:uuid:" + str(identity.value)


def lookup_key(namespace: uuid.UUID, value: str) -> LookupKey:
    """uuid5 lookup key; the value is hashed as given, never normalised."""
    if not isinstance(namespace, uuid.UUID):
        raise TypeError("namespace must be a uuid.UUID")
    if not isinstance(value, str):
        raise TypeError("value must be a str")
    return LookupKey(uuid.uuid5(namespace, value))
