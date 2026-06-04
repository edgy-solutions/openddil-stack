"""Faust Apps + HQ producer sidecar for asset-registry-service.

Mirrors the multi-broker shape of openddil-tactical-agents/regional/
source_app.py: one Faust App per source edge consumes that edge's
telemetry-latest-state topic; assignment decisions hit a shared
postgres pool; resulting registry events are published to HQ's
asset-registry-events topic via an aiokafka sidecar.

Per-edge composition is intentional. asset-registry-service is the
authoritative writer for the public.asset_registry table -- a single
writer pod composing multiple-broker Apps via faust.Worker(...) gives
one observability target, one postgres pool, one HQ producer.

See ADR-0028 for the assignment priority + divergence handling that
db.upsert_observation encodes.
"""
from __future__ import annotations

import asyncio
import json
import logging
from typing import Optional

import aiokafka
import faust

from . import db
from . import edge_assignment as ea


log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# HQ producer sidecar -- writes asset-registry-events on the HQ cluster.
# Lifecycle-managed by faust.Worker (start/stop with the other Apps).
# Same shape as source_app's _HqProducerService.
# ---------------------------------------------------------------------------
class _HqProducerService(faust.Service):
    """Wraps an aiokafka.AIOKafkaProducer pointed at the HQ cluster.

    One instance shared by every per-edge App so all assignment events
    land on the same HQ topic. faust.Worker starts/stops it alongside
    the Apps.
    """
    def __init__(self, *, hq_brokers: str, label: str) -> None:
        super().__init__()
        self._hq_brokers = hq_brokers
        self._label = label
        self._producer: Optional[aiokafka.AIOKafkaProducer] = None

    async def on_start(self) -> None:
        self._producer = aiokafka.AIOKafkaProducer(
            bootstrap_servers=self._hq_brokers,
            client_id=f"{self._label}-hq-producer",
            acks="all",
        )
        await self._producer.start()
        log.info("%s: hq producer started (brokers=%s)",
                 self._label, self._hq_brokers)

    async def on_stop(self) -> None:
        if self._producer is not None:
            await self._producer.stop()
            self._producer = None
            log.info("%s: hq producer stopped", self._label)

    async def send(self, topic: str, key: str, value: bytes) -> None:
        if self._producer is None:
            raise RuntimeError("HQ producer not started")
        await self._producer.send_and_wait(
            topic, value=value, key=key.encode("utf-8"),
        )


# ---------------------------------------------------------------------------
# Telemetry-event payload extraction.
# ---------------------------------------------------------------------------
#
# The input topic telemetry-latest-state carries one event per asset
# update. The projector writes binary EntityTelemetryEvent (proto) on
# raw-sensor-stream; faust-edge republishes as latest-state with the
# same proto shape.
#
# asset-registry-service ONLY needs: asset_id + position + (the edge
# this event arrived from). We deliberately avoid taking a hard
# protobuf dependency here -- the schema may evolve, and we don't want
# the registry to crash on every contract bump.
#
# Strategy: try to decode as proto if proto bindings are present;
# otherwise fall back to parsing whatever subset of fields we can find
# in a JSON-ish encoding. The projector pod already extracts kinematics
# via the same pattern; we mirror it here.
def _try_decode_event(raw: bytes) -> Optional[dict]:
    """Decode an event payload into a dict containing at minimum
    {'asset_id': str, 'lat': float, 'lon': float} when possible.
    Returns None if neither decode path yields position-bearing data.
    """
    # Try JSON first (cheap, deterministic). Some upstream pipelines
    # serialize EntityTelemetryEvent as JSON for debugging; supporting
    # that lets us test the service without a proto runtime.
    try:
        if raw.startswith(b"{"):
            data = json.loads(raw)
            asset_id = (data.get("asset") or {}).get("asset_id") or data.get("asset_id")
            if not asset_id:
                return None
            pos = (((data.get("kinematics") or {}).get("position") or {}).get("wgs84") or {})
            lat = ((pos.get("lat") or {}).get("value") if isinstance(pos.get("lat"), dict) else pos.get("lat"))
            lon = ((pos.get("lon") or {}).get("value") if isinstance(pos.get("lon"), dict) else pos.get("lon"))
            return {"asset_id": asset_id, "lat": lat, "lon": lon}
    except Exception:
        pass
    # Proto path. Imported lazily so the service doesn't hard-require
    # the proto bindings at import time -- the bundle init-container
    # populates /proto at pod startup, but a unit-test environment may
    # not have it.
    try:
        from openddil.telemetry.v1 import telemetry_pb2  # type: ignore
        ev = telemetry_pb2.EntityTelemetryEvent()
        ev.ParseFromString(raw)
        asset_id = ev.asset.asset_id
        if not asset_id:
            return None
        wgs = ev.kinematics.position.wgs84
        lat = wgs.lat.value if wgs.HasField("lat") else None
        lon = wgs.lon.value if wgs.HasField("lon") else None
        return {"asset_id": asset_id, "lat": lat, "lon": lon}
    except Exception as exc:
        log.debug("proto decode failed: %s", exc)
        return None


# ---------------------------------------------------------------------------
# Per-edge App factory.
# ---------------------------------------------------------------------------
def make_edge_app(
    *,
    edge_id: str,
    edge_broker_url: str,
    input_topic: str,
    output_topic: str,
    hq_producer: _HqProducerService,
    web_port: int,
) -> faust.App:
    """Build a Faust App bound to ONE edge broker.

    Reads telemetry-latest-state from that edge. For each event:
      * decode asset_id + (lat, lon)
      * run edge_assignment to compute proposed (edge_id, region_id)
      * upsert into asset_registry (postgres) per ADR-0028 priority rules
      * publish the resulting row to asset-registry-events on HQ
    """
    app_id = f"asset-registry-{edge_id}"
    app = faust.App(
        app_id,
        broker=f"kafka://{edge_broker_url}",
        store="memory://",
        value_serializer="raw",
        web_port=web_port,
    )
    in_topic = app.topic(input_topic, value_type=bytes)

    @app.agent(in_topic)
    async def on_telemetry(stream):
        async for raw in stream:
            if not raw:
                continue
            event = _try_decode_event(raw)
            if event is None:
                # Couldn't extract asset_id -- skip silently. Unknown
                # shapes are noise here; the projector is the
                # authoritative consumer of EntityTelemetryEvent.
                continue
            asset_id = event["asset_id"]
            lat = event.get("lat")
            lon = event.get("lon")

            # Run the configured assignment strategy. resolve_for()
            # always returns an EdgeAssignment -- the strategy chain
            # falls back to the configured fallback (typically
            # edge-unspecified / region-unspecified) when no strategy
            # in the chain succeeds.
            result = ea.resolve_for(
                asset_id=asset_id, lat=lat, lon=lon,
                handler_label=f"asset-registry/{edge_id}",
            )

            # Decide the assignment_source label for postgres. The chain
            # may have fired any strategy -- we map back to one of the
            # ADR-0028-defined source values for the registry row.
            method = result.derivation_basis.get("method", "")
            if method in ("nearest_fob",):
                proposed_source = "position"
            elif method in ("asset_id_prefix", "static"):
                # asset_id_prefix is operator-supplied static config -- it
                # ARRIVES via YAML, not telemetry observation, so treat
                # it as a static assignment for priority purposes.
                proposed_source = "static"
            else:
                proposed_source = "unspecified"

            try:
                row = await db.upsert_observation(
                    asset_id=asset_id,
                    observed_edge_id=edge_id,
                    proposed_edge_id=result.edge_id,
                    proposed_region_id=result.region_id,
                    proposed_source=proposed_source,
                    proposed_by=f"edge_assignment.yaml/{method}",
                )
            except Exception as exc:
                log.error("upsert failed for %s: %s", asset_id, exc)
                continue

            # Publish to asset-registry-events on HQ broker. Consumers
            # (cm-service, logistics-fusion, faust-regional source-app,
            # projector) subscribe to this topic to keep their caches
            # current per ADR-0028.
            payload = json.dumps({
                "asset_id": row.asset_id,
                "edge_id": row.edge_id,
                "region_id": row.region_id,
                "assignment_source": row.assignment_source,
                "observed_edge_id": row.observed_edge_id,
                "divergent": row.divergent,
            }).encode("utf-8")
            try:
                await hq_producer.send(output_topic, key=asset_id, value=payload)
            except Exception as exc:
                # A failed publish is recoverable -- the row IS in
                # postgres; consumers will catch up at startup via the
                # bulk SELECT, or on the next observation publish.
                log.warning("publish failed for %s: %s", asset_id, exc)

    # Faust requires agents to be named in the module scope so the
    # @app.agent decorator-registered function isn't garbage-collected.
    # Reference it on the app to keep the linter happy.
    app._registry_agent = on_telemetry  # type: ignore[attr-defined]
    return app
