"""Runtime configuration -- all env-var sourced.

Mirrors the env-var pattern used by openddil-tactical-agents/regional
(faust_regional.py:31-39) so deploys feel familiar to anyone who's
configured the regional aggregator.
"""
from __future__ import annotations
import os
from dataclasses import dataclass


@dataclass(frozen=True)
class Config:
    # Kafka brokers the service consumes from + publishes to. Single
    # cluster only for this service -- it reads HQ-side telemetry
    # snapshots (which the edge-HQ bridge has already aggregated) and
    # publishes assignment events on the same cluster.
    kafka_brokers: str

    # Input topic carrying per-asset telemetry with position. Default is
    # telemetry-latest-state (one upsert per asset, position-bearing).
    # The asset-registry service doesn't need every telemetry tick --
    # the latest-state stream gives one event per asset per update,
    # which is the natural cadence for "did this asset's position change
    # enough to warrant re-evaluation?"
    input_topic: str

    # Output topic where asset_registry changes are published. Consumers
    # (cm-service, logistics-fusion, faust-regional source-app, projector)
    # subscribe to this to keep their in-memory caches current.
    output_topic: str

    # Postgres DSN for the HQ instance. Same shape projector + cm-service
    # use: postgresql://user:pass@host:5432/dbname
    postgres_dsn: str

    # Path to the edge-assignment YAML config (FOB list + strategy chain).
    # Currently mounted at /etc/openddil/edge-assignment/edge-assignment.yaml
    # in the projector pod (see openddil-helm/openddil-demo/templates).
    # asset-registry-service mounts the same path.
    edge_assignment_config: str

    # Faust App identifier. Influences Kafka consumer-group naming and
    # the changelog topic prefix.
    app_id: str

    # HTTP port for Faust's built-in metrics/health endpoint.
    web_port: int

    # Logging verbosity. Same convention as faust-regional.
    log_level: str

    # Per-edge static region fallback: maps observed edge_id to a
    # region_id used when edge_assignment yields "region-unspecified"
    # (no FOB list, no overlay). Lets the service produce real
    # (edge, region) mappings in OSS/demo environments where no
    # edge_assignment.yaml is mounted, without changing the strategy
    # chain itself. Parsed from
    #   ASSET_REGISTRY_EDGE_REGIONS="edge-01=region-east,edge-03=region-west"
    # Empty/missing -> no override; service emits "region-unspecified"
    # as before. This is intentionally tier-2: any real
    # edge_assignment result wins.
    edge_regions: dict

    @classmethod
    def from_env(cls) -> "Config":
        return cls(
            kafka_brokers=os.getenv(
                "ASSET_REGISTRY_KAFKA_BROKERS",
                "redpanda-hq:19092",
            ),
            input_topic=os.getenv(
                "ASSET_REGISTRY_INPUT_TOPIC",
                "telemetry-latest-state",
            ),
            output_topic=os.getenv(
                "ASSET_REGISTRY_OUTPUT_TOPIC",
                "asset-registry-events",
            ),
            postgres_dsn=os.environ["ASSET_REGISTRY_POSTGRES_DSN"],
            edge_assignment_config=os.getenv(
                "EDGE_ASSIGNMENT_CONFIG",
                "/etc/openddil/edge-assignment/edge-assignment.yaml",
            ),
            app_id=os.getenv(
                "ASSET_REGISTRY_APP_ID",
                "asset-registry-service",
            ),
            web_port=int(os.getenv("ASSET_REGISTRY_WEB_PORT", "6070")),
            log_level=os.getenv("LOG_LEVEL", "INFO").upper(),
            edge_regions=_parse_edge_regions(
                os.getenv("ASSET_REGISTRY_EDGE_REGIONS", "")
            ),
        )


def _parse_edge_regions(spec: str) -> dict:
    """Parse 'edge-01=region-east,edge-02=region-west' -> {edge_id: region_id}.
    Empty / whitespace-only spec yields an empty dict (no override)."""
    out: dict[str, str] = {}
    for entry in spec.split(","):
        entry = entry.strip()
        if not entry:
            continue
        if "=" not in entry:
            raise RuntimeError(
                f"ASSET_REGISTRY_EDGE_REGIONS entry {entry!r} must be "
                "'edge_id=region_id'"
            )
        edge_id, region_id = entry.split("=", 1)
        out[edge_id.strip()] = region_id.strip()
    return out
