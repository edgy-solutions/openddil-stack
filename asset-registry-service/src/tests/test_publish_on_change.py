"""Registry events publish on change, with a periodic re-publish.

Drives registry_app._handle_observation with fakes (no Faust, no
postgres, no Kafka): a fixed-row db, a recording producer, a fixed
assignment result and an injectable clock.
"""
from __future__ import annotations

import json
from types import SimpleNamespace

from asset_registry_service.registry_app import _PublishGate, _handle_observation

REPUBLISH = 300.0


def _raw(asset_id: str) -> bytes:
    return json.dumps({
        "asset": {"asset_id": asset_id},
        "kinematics": {"position": {"wgs84": {"lat": 1.0, "lon": 2.0}}},
    }).encode("utf-8")


class FakeDb:
    def __init__(self, **overrides):
        self.overrides = overrides

    async def upsert_observation(self, *, asset_id, **_kw):
        row = dict(
            asset_id=asset_id, edge_id="edge-t", region_id="region-t",
            assignment_source="unspecified", observed_edge_id="edge-t",
            divergent=False, platform_variant="",
        )
        row.update(self.overrides)
        return SimpleNamespace(**row)


class FakeEa:
    @staticmethod
    def resolve_for(*, asset_id, lat, lon, handler_label):
        return SimpleNamespace(
            region_id="region-t", edge_id="edge-t",
            derivation_basis={"method": "fallback"},
        )


class FakeProducer:
    def __init__(self, fail_first: int = 0):
        self.sent = []
        self.attempts = 0
        self.fail_first = fail_first

    async def send(self, topic, key, value):
        self.attempts += 1
        if self.attempts <= self.fail_first:
            raise RuntimeError("broker down")
        self.sent.append((topic, key, value))


class Clock:
    def __init__(self):
        self.t = 1000.0

    def __call__(self):
        return self.t


async def _feed(producer, gate, clock, asset_id="dis:9:9:1", db=None):
    await _handle_observation(
        _raw(asset_id), edge_id="edge-t", static_region=None,
        output_topic="out", hq_producer=producer, gate=gate, clock=clock,
        db=db or FakeDb(), ea=FakeEa,
    )


async def test_identical_records_publish_once():
    p, c = FakeProducer(), Clock()
    g = _PublishGate(REPUBLISH)
    await _feed(p, g, c)
    await _feed(p, g, c)
    assert len(p.sent) == 1


async def test_republish_zero_publishes_every_observation():
    p, c = FakeProducer(), Clock()
    g = _PublishGate(0)
    await _feed(p, g, c)
    await _feed(p, g, c)
    assert len(p.sent) == 2


async def test_changed_row_publishes_again():
    p, c = FakeProducer(), Clock()
    g = _PublishGate(REPUBLISH)
    await _feed(p, g, c, db=FakeDb(divergent=False))
    await _feed(p, g, c, db=FakeDb(divergent=True))
    assert len(p.sent) == 2


async def test_republish_after_interval():
    p, c = FakeProducer(), Clock()
    g = _PublishGate(REPUBLISH)
    await _feed(p, g, c)
    c.t += REPUBLISH - 1
    await _feed(p, g, c)
    assert len(p.sent) == 1
    c.t += 1  # now exactly REPUBLISH since the last send
    await _feed(p, g, c)
    assert len(p.sent) == 2


async def test_failed_send_is_retried_and_not_recorded():
    p, c = FakeProducer(fail_first=1), Clock()
    g = _PublishGate(REPUBLISH)
    await _feed(p, g, c)
    await _feed(p, g, c)
    assert p.attempts == 2
    assert len(p.sent) == 1


async def test_distinct_assets_each_publish_once():
    p, c = FakeProducer(), Clock()
    g = _PublishGate(REPUBLISH)
    for _ in range(2):
        await _feed(p, g, c, asset_id="dis:9:9:1")
        await _feed(p, g, c, asset_id="dis:9:9:2")
    assert sorted(k for _, k, _ in p.sent) == ["dis:9:9:1", "dis:9:9:2"]
