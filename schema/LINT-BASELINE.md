# Migration lint baseline

CI lints only the migrations a pull request **adds** (the `squawk` job in
`.github/workflows/schema-checks.yml`). The migrations already on `main` are not
re-linted. They have been applied, and an applied migration is never edited. This
file records what the linter finds in them, so the findings are written down
and not silenced.

## Row: findings in the migrations on main before the squawk job

| Status | What |
|---|---|
| recorded, not fixed | The `atlas migrate lint` job never produced a finding. From Atlas v0.38 that command is available only to Atlas Pro users, so every run stopped before any migration was analysed. It is replaced by [squawk](https://github.com/sbdchd/squawk), the open-source Postgres migration linter. Squawk 2.70.0 (`--pg-version=15.0`, no `--assume-in-transaction`, because Atlas applies with `--tx-mode none`) over the 35 migrations on `main` at b5a8507 gives the table below. A later migration that changes one of these tables is linted as a new file, so a risky change to them is still caught. |

Reproduce with:

```sh
pip install squawk-cli==2.70.0
squawk --pg-version=15.0 schema/migrations/*.sql
```

## By migration

| Migration | Findings | By rule |
|---|---:|---|
| `20260514040446_phase4a_pipeline_tables.sql` | 26 | prefer-robust-stmts 10, prefer-bigint-over-int 7, prefer-text-field 4, require-concurrent-index-creation 3, require-lock-timeout 1, require-statement-timeout 1 |
| `20260514110726_phase4a_cm_manual_discrepancies.sql` | 3 | prefer-robust-stmts 1, require-lock-timeout 1, require-statement-timeout 1 |
| `20260514150847_phase4c5_edge_buffer_status.sql` | 1 | prefer-robust-stmts 1 |
| `20260514183155_phase4d_origin_node_provenance.sql` | 12 | prefer-robust-stmts 10, require-lock-timeout 1, require-statement-timeout 1 |
| `20260518113046_phase6b_regional_rollup_tables.sql` | 8 | prefer-bigint-over-int 5, prefer-robust-stmts 3 |
| `20260520010000_phase7_asset_capability_state.sql` | 1 | prefer-robust-stmts 1 |
| `20260529010000_phase5_operational_state_columns.sql` | 7 | prefer-robust-stmts 5, require-lock-timeout 1, require-statement-timeout 1 |
| `20260604202813_phase8_asset_registry.sql` | 9 | prefer-robust-stmts 4, require-concurrent-index-creation 3, require-lock-timeout 1, require-statement-timeout 1 |
| `20260613000000_phase9_asset_element_telemetry.sql` | 9 | prefer-robust-stmts 4, require-concurrent-index-creation 3, require-lock-timeout 1, require-statement-timeout 1 |
| `20260630000000_inventory_per_asset_per_layer.sql` | 5 | ban-drop-default 1, changing-column-type 1, require-concurrent-index-creation 1, require-lock-timeout 1, require-statement-timeout 1 |
| `20260807000000_arc1_releasability_labels.sql` | 8 | require-concurrent-index-creation 6, require-lock-timeout 1, require-statement-timeout 1 |
| `20260906000000_labelling_block_asset_bearing_tables.sql` | 2 | require-lock-timeout 1, require-statement-timeout 1 |
| `20260908000000_region_rollup_releasability.sql` | 2 | require-lock-timeout 1, require-statement-timeout 1 |
| `20260908010000_rollup_releasability_partitions.sql` | 11 | adding-serial-primary-key-field 3, ban-drop-constraint 3, constraint-missing-not-valid 3, require-lock-timeout 1, require-statement-timeout 1 |
| `20260927000000_lifecycle_two_columns.sql` | 6 | prefer-robust-stmts 4, require-lock-timeout 1, require-statement-timeout 1 |
| `20260929000000_asset_lifecycle_summary_view.sql` | 2 | require-lock-timeout 1, require-statement-timeout 1 |
| `20261001000000_region_fleet_summary_terminal_status.sql` | 8 | prefer-bigint-over-int 3, prefer-robust-stmts 3, require-lock-timeout 1, require-statement-timeout 1 |
| `20261002000000_maintenance_actions.sql` | 1 | prefer-robust-stmts 1 |
| `20261003000000_intake_records.sql` | 4 | ban-drop-table 1, prefer-robust-stmts 1, require-lock-timeout 1, require-statement-timeout 1 |
| `20261006000000_effector_launch.sql` | 9 | prefer-bigint-over-int 3, prefer-robust-stmts 3, require-concurrent-index-creation 1, require-lock-timeout 1, require-statement-timeout 1 |
| `20261006010000_asset_registry_platform_variant.sql` | 2 | require-lock-timeout 1, require-statement-timeout 1 |
| `20261006020000_egress_delivered_events.sql` | 3 | prefer-bigint-over-int 2, prefer-robust-stmts 1 |
| `20261006030000_telemetry_posture_status.sql` | 6 | prefer-robust-stmts 3, constraint-missing-not-valid 1, require-lock-timeout 1, require-statement-timeout 1 |
| `20261008000000_egress_delivered_events_failed.sql` | 5 | ban-drop-constraint 1, constraint-missing-not-valid 1, prefer-robust-stmts 1, require-lock-timeout 1, require-statement-timeout 1 |
| `20261008010000_telemetry_latest_state_subsystem.sql` | 3 | prefer-robust-stmts 1, require-lock-timeout 1, require-statement-timeout 1 |
| `20261008020000_effector_launch_munition_asset_id.sql` | 5 | prefer-robust-stmts 2, require-concurrent-index-creation 1, require-lock-timeout 1, require-statement-timeout 1 |
| `20261009000000_link_status.sql` | 1 | prefer-robust-stmts 1 |
| `20261009010000_telemetry_windows_element_rollup.sql` | 3 | prefer-robust-stmts 1, require-lock-timeout 1, require-statement-timeout 1 |
| `20261013000000_effector_resupply_parts_availability.sql` | 9 | prefer-bigint-over-int 3, prefer-robust-stmts 3, require-concurrent-index-creation 1, require-lock-timeout 1, require-statement-timeout 1 |
| `20261015000000_munition_track.sql` | 8 | prefer-robust-stmts 3, require-concurrent-index-creation 2, prefer-bigint-over-int 1, require-lock-timeout 1, require-statement-timeout 1 |
| `20261015120000_asset_arrivals.sql` | 11 | prefer-robust-stmts 6, require-concurrent-index-creation 3, require-lock-timeout 1, require-statement-timeout 1 |
| `20261016000000_munition_track_trail.sql` | 3 | prefer-robust-stmts 1, require-lock-timeout 1, require-statement-timeout 1 |
| `20261017000000_egress_delivered_events_episode.sql` | 8 | prefer-robust-stmts 5, require-concurrent-index-creation 1, require-lock-timeout 1, require-statement-timeout 1 |
| `20261031000000_asset_element_state.sql` | 11 | prefer-robust-stmts 4, require-concurrent-index-creation 2, ban-drop-column 1, prefer-bigint-over-int 1, prefer-bigint-over-smallint 1, require-lock-timeout 1, require-statement-timeout 1 |

Total: 212 findings in 34 of 35 migrations. No finding: `20260906010000_labelling_block_backfill.sql`.
