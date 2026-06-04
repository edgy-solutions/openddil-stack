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
        )
