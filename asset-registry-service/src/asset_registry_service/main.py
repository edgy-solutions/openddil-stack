"""asset-registry-service entrypoint.

Wires the multi-broker Faust composition together (mirrors
faust-regional's main entrypoint) and starts faust.Worker with one App
per source edge + the HQ producer sidecar.

Env config (see config.py for defaults):
  ASSET_REGISTRY_POSTGRES_DSN   -- required, no default
  ASSET_REGISTRY_EDGES          -- required, comma-separated
                                   "edge-01=redpanda-edge-01:9092,
                                    edge-02=redpanda-edge-02:9092"
  ASSET_REGISTRY_KAFKA_BROKERS  -- HQ brokers for publishing registry
                                   events. Default "redpanda-hq:19092"
  ASSET_REGISTRY_INPUT_TOPIC    -- default "telemetry-latest-state"
  ASSET_REGISTRY_OUTPUT_TOPIC   -- default "asset-registry-events"
  EDGE_ASSIGNMENT_CONFIG        -- path to edge-assignment.yaml.
                                   Default /etc/openddil/edge-assignment/edge-assignment.yaml
  LOG_LEVEL                     -- default "INFO"

NOTE: edge_assignment.configure_from_config() must be called BEFORE the
worker starts so the strategy chain is wired. We do it synchronously
here -- read the YAML, configure the module-level strategy/fallback.
"""
from __future__ import annotations

import asyncio
import logging
import os
import sys

import faust
import yaml

from . import db
from . import edge_assignment as ea
from .config import Config
from .registry_app import _HqProducerService, make_edge_app


log = logging.getLogger("asset_registry_service.main")


def _parse_edges(spec: str) -> list[tuple[str, str]]:
    """Parse "edge-01=host:port,edge-02=host:port" into [(id, brokers), ...]."""
    out: list[tuple[str, str]] = []
    for entry in spec.split(","):
        entry = entry.strip()
        if not entry:
            continue
        if "=" not in entry:
            raise RuntimeError(
                f"ASSET_REGISTRY_EDGES entry {entry!r} must be 'edge_id=host:port'"
            )
        edge_id, brokers = entry.split("=", 1)
        out.append((edge_id.strip(), brokers.strip()))
    return out


def _load_edge_assignment(path: str) -> None:
    """Read the FOB list + strategy chain YAML and configure the
    module-level strategy. Same file projector mounts at
    /etc/openddil/edge-assignment/edge-assignment.yaml."""
    with open(path, "r") as f:
        cfg = yaml.safe_load(f)
    if not isinstance(cfg, dict):
        raise RuntimeError(f"edge_assignment config at {path!r} is not a dict")
    ea.configure_from_config(cfg)
    log.info("edge_assignment configured from %s (strategy=%s)",
             path, cfg.get("strategy", "<missing>"))


def main() -> int:
    logging.basicConfig(
        level=os.getenv("LOG_LEVEL", "INFO").upper(),
        format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
        stream=sys.stdout,
    )

    cfg = Config.from_env()

    edges_spec = os.environ["ASSET_REGISTRY_EDGES"]
    edges = _parse_edges(edges_spec)
    if not edges:
        raise RuntimeError("ASSET_REGISTRY_EDGES yielded no edges to subscribe to")

    log.info(
        "asset-registry-service starting: edges=%s hq=%s input=%s output=%s",
        [e[0] for e in edges], cfg.kafka_brokers, cfg.input_topic, cfg.output_topic,
    )

    _load_edge_assignment(cfg.edge_assignment_config)

    # Initialize the shared postgres pool BEFORE Faust starts -- so the
    # first event hitting the agent doesn't race against pool creation.
    asyncio.run(db.init_pool(cfg.postgres_dsn))

    hq_producer = _HqProducerService(
        hq_brokers=cfg.kafka_brokers, label=cfg.app_id,
    )

    # One App per edge. The first becomes the "primary"; the rest are
    # passed as additional services. Same composition pattern as
    # openddil-tactical-agents/regional/faust_regional.py.
    apps: list[faust.App] = []
    for i, (edge_id, edge_broker) in enumerate(edges):
        # Each App needs a distinct web port so they can all run in one
        # process without colliding. Increment from the configured base.
        port = cfg.web_port + i
        apps.append(make_edge_app(
            edge_id=edge_id,
            edge_broker_url=edge_broker,
            input_topic=cfg.input_topic,
            output_topic=cfg.output_topic,
            hq_producer=hq_producer,
            web_port=port,
        ))

    primary, *secondaries = apps
    worker = faust.Worker(
        primary,
        *secondaries,
        hq_producer,           # sidecar service, starts/stops with the worker
        loglevel=cfg.log_level.lower(),
        logging_config=None,    # we already called logging.basicConfig above
    )
    worker.execute_from_commandline()
    return 0


if __name__ == "__main__":
    sys.exit(main())
