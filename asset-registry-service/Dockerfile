# =============================================================================
# openddil-asset-registry-service -- ADR-0028 Phase 1
# =============================================================================
# Two-stage build matching openddil-sensor-ingest's pattern:
#   1. builder -- installs deps into a venv via uv + pyproject.toml
#   2. runtime -- slim image that runs the Faust worker
#
# Service consumes per-edge telemetry-latest-state, computes asset->
# edge/region assignment via the bundle-mounted edge-assignment.yaml,
# upserts asset_registry on HQ postgres, and publishes asset-registry-
# events to HQ broker for consumer cache invalidation.
# =============================================================================

# ---------- Stage 1: Builder ----------
FROM python:3.11-slim AS builder

# librdkafka is dragged in by confluent-kafka (transitively via faust's
# ecosystem) on some installs; install build deps in case wheels are
# missing for the target arch.
RUN apt-get update && apt-get install -y --no-install-recommends \
        gcc g++ make librdkafka-dev \
    && rm -rf /var/lib/apt/lists/*

RUN pip install --no-cache-dir uv && uv venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"

WORKDIR /app
COPY pyproject.toml .
COPY src ./src
# Compile a pinned requirements set from pyproject.toml and install
# from it. Same uv flow sensor-ingest uses; it's faster than `pip
# install -e .` and the resulting image only contains the runtime
# dependency tree, not the editable-install metadata.
RUN uv pip compile pyproject.toml -o requirements.txt \
    && uv pip install --no-cache -r requirements.txt \
    && uv pip install --no-cache --no-deps -e .

# ---------- Stage 2: Runtime ----------
FROM python:3.11-slim AS runtime

RUN apt-get update && apt-get install -y --no-install-recommends \
        librdkafka1 \
    && rm -rf /var/lib/apt/lists/*

COPY --from=builder /opt/venv /opt/venv
COPY --from=builder /app/src /app/src
ENV PATH="/opt/venv/bin:$PATH"
ENV PYTHONUNBUFFERED=1
ENV PYTHONPATH=/app/src

WORKDIR /app

# Faust web port for metrics/health -- one app per source edge, the
# entrypoint increments from the base port. Range 6070-6079 covers up
# to 10 edges in one process. Helm Service exposes 6070 (the primary).
EXPOSE 6070/tcp

# `asset-registry-service` script entry comes from pyproject.toml's
# [project.scripts] -- it calls into asset_registry_service.main.main().
# Equivalent to `python -m asset_registry_service.main` but follows
# the cm-service / sensor-ingest convention of a named entrypoint.
CMD ["asset-registry-service"]
