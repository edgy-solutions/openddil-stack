"""asset-registry-service -- canonical asset->edge->region lineage.

Implements ADR-0028. Single writer to the public.asset_registry table on
the HQ postgres. Reads telemetry events off the per-edge Kafka stream,
applies the configured assignment policy (initially position-based via
edge_assignment.py), upserts the row, and publishes a changelog entry
to asset-registry-events for cache invalidation in consumer services.

See README.md for runtime configuration and the surrounding ADR for the
priority order, divergence handling, and re-assignment policy.
"""

__version__ = "0.1.0"
