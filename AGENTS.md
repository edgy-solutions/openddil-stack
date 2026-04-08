# AGENTS.md — OpenDDIL Stack

Guidelines and safety constraints for AI agents working in this repository.

## Repository Scope

This repo contains **infrastructure only** — Docker Compose, Atlas schema definitions, and ElectricSQL configuration. There is no application code here. Application logic lives in the HQ and Edge SDK repos.

## What You CAN Do

- **Modify `schema/schema.hcl`** to add, rename, or restructure tables and columns using Atlas HCL syntax.
- **Modify `docker-compose.yml`** to add services, update images, adjust ports, or change configuration.
- **Modify `electric/electrify.sql`** to add or remove tables from the ElectricSQL publication.
- **Run `docker compose config`** to validate compose file syntax.
- **Run `docker compose run --rm atlas-init`** to re-apply schema changes.
- **Run read-only queries** against the local Postgres for debugging.

## What You MUST NOT Do

- ❌ **Never write raw SQL migrations** (CREATE TABLE, ALTER TABLE). All schema changes go through `schema/schema.hcl` and Atlas.
- ❌ **Never publish `audit_log` to ElectricSQL**. It is HQ-only and must not sync to untrusted Edge nodes.
- ❌ **Never remove the `wal_level=logical` Postgres setting**. ElectricSQL requires logical replication.
- ❌ **Never commit production credentials**. Use environment variables or secrets management.
- ❌ **Never run `docker compose down -v` without user confirmation**. This destroys all data volumes.
- ❌ **Never modify files in sibling repos** (`openddil-contracts`, `openddil-edge-*`, `openddil-hq-*`, `openddil-sensor-ingest`) from this repo's context. Each repo has its own agent guidelines.

## Schema Change Workflow

1. Edit `schema/schema.hcl` with the desired-state change.
2. Run `docker compose run --rm atlas-init` to apply.
3. If a new table needs Edge sync, add it to the `PUBLICATION` in `electric/electrify.sql`.
4. Verify with a `psql` query that the schema matches expectations.
5. Update `llms.txt` and `README.md` to reflect any new tables or columns.

## Expand / Contract Safety

When evolving the schema in a DDIL environment:

- **Phase 1 (Expand)**: Only ADD new columns/tables. Never rename or drop in this phase. Old Edge SDKs and HQ processors must continue to work.
- **Phase 2 (Contract)**: Only REMOVE old columns/tables after ALL consumers (Edge + HQ) have been updated and deployed. Confirm with the user before any contract operation.

> ⚠️ **Contract operations are destructive and irreversible in production.** Always ask for explicit user confirmation before removing columns or tables from `schema.hcl`.

## Documentation Maintenance

After ANY change to this repo, update:

1. `README.md` — Keep the service endpoints table, quick-start commands, and architecture diagram current.
2. `llms.txt` — Keep the key files list, tech stack, and schema evolution notes current.
3. `.cursorrules` — Update if new tech stack components or conventions are introduced.
4. This file (`AGENTS.md`) — Update safety constraints if new dangerous operations are possible.
