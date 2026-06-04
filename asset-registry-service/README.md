# asset-registry-service

Canonical writer for `public.asset_registry` — the **single source of
truth for asset → edge_id → region_id** mapping across the OpenDDIL
stack. Implements [ADR-0028][adr].

[adr]: ../../openddil-contracts/decisions/ADR-0028-centralized-asset-registry-edge-region-lineage.md

## Why it exists

Through ADR-0027 / Phase 7 the projector ran `edge_assignment.py`
locally to derive each asset's region for its `telemetry_latest_state`
writes. cm-service and logistics-fusion did NOT — they emitted events
with empty `provenance.region_id`. The faust-regional source-app's
positive-match filter then dropped 100% of them, breaking Regional /
HQ rollups.

ADR-0028 centralizes assignment to ONE writer, with all consumers
caching from a shared changelog topic. asset-registry-service is that
writer.

## What it does

For each telemetry event it observes:

1. Decode `asset_id` + `(lat, lon)` from `EntityTelemetryEvent` (proto
   or JSON, depending on the source).
2. Apply the configured assignment strategy from
   `edge-assignment.yaml` (the same file the projector currently uses
   — mounted at `/etc/openddil/edge-assignment/edge-assignment.yaml`).
3. Upsert into `public.asset_registry` per the **ADR-0028 priority
   rules**:
   - `static > connection > position > unspecified` (lower wins)
   - Existing higher-or-equal priority is **never** overwritten by a
     re-observation — only `last_observed_at`, `observed_edge_id`,
     and `divergent` refresh.
   - Existing strictly-lower priority IS replaced (e.g. when a new
     `static` assignment beats a previous `position`-derived row).
4. Publish a JSON event to `asset-registry-events` (HQ broker) keyed
   by asset_id, so consumer caches (cm-service, logistics-fusion,
   faust-regional source-app, projector) stay current via pub/sub.

The service NEVER overrides a static (warfighter) assignment. When
the warfighter's assignment disagrees with reality, the row's
`divergent` flag is set and the operator sees it surfaced in the
Maintainer view (Phase 4 of ADR-0028).

## Topology

Multi-broker Faust composition (same shape as
`openddil-tactical-agents/regional/source_app.py`):

```
                            +----------------------------------+
   redpanda-edge-01 ─────►  │ Faust App #1                     │
   (telemetry-latest-state) │  agent: on_telemetry             │
                            │   ├─ edge_assignment.resolve_for │
   redpanda-edge-02 ─────►  │ Faust App #2 ─┐                  │
                            │  ...           ├──┬──────────────┤
   redpanda-edge-N  ─────►  │ Faust App #N ─┘  │              │
                            │                   │              │
                            │             asyncpg pool         │
                            │                   │              │
                            │             ┌─────▼─────┐        │
                            │             │ HQ        │        │
                            │             │ producer  │        │
                            │             │ sidecar   │        │
                            │             └─────┬─────┘        │
                            +-------------------|--------------+
                                                │
                          ┌─────────────────────┼──────────────┐
                          ▼                     ▼              ▼
                  HQ postgres            asset-registry-events │
                  (asset_registry)       (HQ Kafka topic)      │
                                                               ▼
                                                  consumer caches:
                                                  cm-service,
                                                  logistics-fusion,
                                                  faust-regional source-app,
                                                  projector
```

One pod. One asyncpg pool. One HQ producer. N edge-app consumers.
Configurable via `ASSET_REGISTRY_EDGES`.

## Configuration

All env-sourced. See `src/asset_registry_service/config.py` for the
canonical list.

| Env var | Required | Default | Meaning |
|---|---|---|---|
| `ASSET_REGISTRY_POSTGRES_DSN` | yes | — | `postgresql://user:pass@host:5432/db` |
| `ASSET_REGISTRY_EDGES` | yes | — | `edge-01=redpanda-edge-01:9092,edge-02=...` |
| `ASSET_REGISTRY_KAFKA_BROKERS` | no | `redpanda-hq:19092` | HQ brokers for publishing registry events |
| `ASSET_REGISTRY_INPUT_TOPIC` | no | `telemetry-latest-state` | Per-edge topic to consume |
| `ASSET_REGISTRY_OUTPUT_TOPIC` | no | `asset-registry-events` | HQ topic for changelog |
| `EDGE_ASSIGNMENT_CONFIG` | no | `/etc/openddil/edge-assignment/edge-assignment.yaml` | Same path projector uses |
| `ASSET_REGISTRY_APP_ID` | no | `asset-registry-service` | Faust App ID prefix |
| `ASSET_REGISTRY_WEB_PORT` | no | `6070` | Base Faust web port; per-edge apps use port + index |
| `LOG_LEVEL` | no | `INFO` | Standard Python logging level |

## Running locally

Install deps via `uv` (matching the openddil-stack convention):

```bash
uv venv .venv
.venv/Scripts/activate     # Windows
# or: source .venv/bin/activate
uv pip install -e .[dev]
```

Run tests (no postgres or Kafka required — the suite uses a fake
asyncpg pool):

```bash
pytest src/tests/ -v
```

Run the service against a local cluster:

```bash
export ASSET_REGISTRY_POSTGRES_DSN="postgresql://postgres:password@localhost:5432/openddil"
export ASSET_REGISTRY_EDGES="edge-01=localhost:9092"
export EDGE_ASSIGNMENT_CONFIG="./test-fixtures/edge-assignment.yaml"
asset-registry-service
```

## Docker

```bash
docker build -t openddil-asset-registry-service:dev .
```

Image expects the runtime-bundle initContainer to mount
`edge-assignment.yaml` at `/etc/openddil/edge-assignment/` and the
generated proto Python bindings at `/proto/`. The Helm chart wires
both via the `openddil.bundleInit` helper (see
`openddil-helm/openddil-demo/templates/`).

## Tests

`src/tests/test_upsert_priority.py` covers the ADR-0028 priority +
divergence semantics. 7 cases:

- First insert (no existing row) — sets divergent based on
  observed-vs-proposed
- First insert with divergence (observed ≠ proposed)
- Existing `static` beats proposed `position` — keep + flag divergent
- Existing same-priority `position` kept (ties go to incumbent —
  no edge-flapping at FOB Voronoi boundaries)
- New `static` replaces existing `position` — full row update
- `unspecified` always yields to any real assignment
- Priority table sanity check (catches accidental dict reordering)

All run against a fake asyncpg pool that captures SQL strings + args
for assertion. No postgres required.

## What's NOT in this service yet

- **Static assignment input** (admin API write). The `static` priority
  is honored in upsert logic but there's no path for the warfighter
  UI to actually write `static`-source rows. Phase 3 of ADR-0028.
- **Connection-based assignment policy**. The current code labels
  position-derived assignments as `position`; pure-connection (no
  position) classification would land in ADR-0028 Phase 2.
- **Re-assignment hysteresis**. Two position-based observations
  currently tie and keep the incumbent (good — no flap). A
  configurable "asset is >X km from assigned FOB for Y minutes →
  re-assign" policy is Phase 4.
- **Consumer migration**. cm-service, logistics-fusion,
  faust-regional source-app, and projector still derive region_id
  locally (or get empty). They subscribe to `asset-registry-events`
  in subsequent commits.

## Related files

- `openddil-stack/schema/migrations/20260604202813_phase8_asset_registry.sql`
  — table definition + indexes + electric publication
- `openddil-projector/src/edge_assignment.py` — vendored copy of the
  assignment logic; deleted from projector in Phase 2
- `openddil-tactical-agents/regional/source_app.py:277-279` — the
  positive-match filter that exposed the centralization gap
- ADR-0028 — full design rationale + 5-phase rollout plan
