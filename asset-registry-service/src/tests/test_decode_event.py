"""_try_decode_event: platform_variant extraction.

The registry records platform_variant exactly like it records the
labels -- read from whichever of the two wire shapes the payload
turns out to be, empty string when the shape doesn't carry one. These
tests cover the JSON path directly; the proto path is covered with a
minimal fake module injected at the same import point
_try_decode_event itself uses (telemetry_pb2 isn't vendored into this
service's dependencies -- see the module's own comment on why the
import is lazy).
"""
from __future__ import annotations
import json
import sys
import types

import pytest

from asset_registry_service.registry_app import _try_decode_event


# ---------------------------------------------------------------------------
# JSON path
# ---------------------------------------------------------------------------

def test_json_decode_carries_platform_variant():
    raw = json.dumps({
        "asset": {"asset_id": "ASSET-A", "platform_variant": "variant-x"},
        "kinematics": {"position": {"wgs84": {"lat": 1.0, "lon": 2.0}}},
    }).encode("utf-8")
    event = _try_decode_event(raw)
    assert event is not None
    assert event["platform_variant"] == "variant-x"


def test_json_decode_defaults_empty_when_absent():
    raw = json.dumps({
        "asset": {"asset_id": "ASSET-A"},
        "kinematics": {"position": {"wgs84": {"lat": 1.0, "lon": 2.0}}},
    }).encode("utf-8")
    event = _try_decode_event(raw)
    assert event is not None
    assert event["platform_variant"] == ""


# ---------------------------------------------------------------------------
# Proto path -- fake telemetry_pb2 injected at the exact import path
# _try_decode_event uses, so the lazy import picks it up.
# ---------------------------------------------------------------------------

class _FakeValue:
    def __init__(self, value):
        self.value = value


class _FakeWgs84:
    def __init__(self, lat=None, lon=None):
        self._lat = _FakeValue(lat) if lat is not None else None
        self._lon = _FakeValue(lon) if lon is not None else None

    def HasField(self, name):
        return getattr(self, f"_{name}") is not None

    @property
    def lat(self):
        return self._lat

    @property
    def lon(self):
        return self._lon


class _FakeProvenance:
    originator_nation = ""
    releasable_to: list = []


class _FakeAsset:
    def __init__(self, asset_id, platform_variant):
        self.asset_id = asset_id
        self.platform_variant = platform_variant


class _FakeEvent:
    """Stands in for telemetry_pb2.EntityTelemetryEvent(). Not a real
    protobuf message -- ParseFromString just unpacks a pre-baked dict
    the test stashed on the class, since what's under test is
    _try_decode_event's field access, not actual deserialization."""
    _next_payload: dict = {}

    def __init__(self):
        payload = _FakeEvent._next_payload
        self.asset = _FakeAsset(payload["asset_id"], payload.get("platform_variant", ""))
        wgs = payload.get("wgs84", {})
        self.kinematics = types.SimpleNamespace(
            position=types.SimpleNamespace(wgs84=_FakeWgs84(**wgs))
        )
        self.provenance = _FakeProvenance()

    def ParseFromString(self, raw):
        pass


@pytest.fixture
def fake_telemetry_pb2(monkeypatch):
    module = types.ModuleType("telemetry_pb2")
    module.EntityTelemetryEvent = _FakeEvent
    monkeypatch.setitem(
        sys.modules, "openddil.telemetry.v1.telemetry_pb2", module
    )
    monkeypatch.setitem(sys.modules, "openddil", types.ModuleType("openddil"))
    monkeypatch.setitem(
        sys.modules, "openddil.telemetry", types.ModuleType("openddil.telemetry")
    )
    monkeypatch.setitem(
        sys.modules, "openddil.telemetry.v1", types.ModuleType("openddil.telemetry.v1")
    )
    yield


def test_proto_decode_carries_platform_variant(fake_telemetry_pb2):
    _FakeEvent._next_payload = {
        "asset_id": "ASSET-B",
        "platform_variant": "variant-y",
        "wgs84": {"lat": 1.0, "lon": 2.0},
    }
    # Not JSON (doesn't start with b"{"), so _try_decode_event falls
    # through to the proto path.
    event = _try_decode_event(b"\x08\x01")
    assert event is not None
    assert event["platform_variant"] == "variant-y"


def test_proto_decode_defaults_empty_when_absent(fake_telemetry_pb2):
    _FakeEvent._next_payload = {
        "asset_id": "ASSET-B",
        "wgs84": {"lat": 1.0, "lon": 2.0},
    }
    event = _try_decode_event(b"\x08\x01")
    assert event is not None
    assert event["platform_variant"] == ""
