# Tool reference — inputs and response shapes

Tools return JSON serialized as text in the MCP `TextContent` payload (`indent=2`). Shapes below describe the **objects** clients parse from that JSON.

Severity where used is always one of: `normal` | `elevated` | `high` | `critical` (see README threshold table and `classify_severity`).

> **Metrics-dependent tools** — `diagnose_lag_issue`, `get_performance_baseline`, and `get_lag_trend` require `GG_ENABLE_METRICS=true`, which is off by default. When `GG_ENABLE_METRICS=false` (the default) these tools are not registered and will not appear in `list_tools`. All other tools are available regardless of the metrics setting.

Policy denials return `{ error, code, reason, tool }`, where `code` is one of `tool_disabled`, `insufficient_role`, `deployment_forbidden`, `read_only`, `deployment_not_writable`, `batch_too_large`, `change_ticket_required`, `change_ticket_invalid`. Over REST these are HTTP 403.

---

## `get_fleet_health`

Optional inputs: `deployments[]`, `lag_threshold_seconds` (default 300).

- `summary`: `deployments`, `healthy`, `degraded`, `error`, `processes`, `running`, `needs_attention`
- `attention[]`, worst first: `{ deployment, process, process_type, status, lag_seconds, severity, reason }`. Non-running states other than `stopped` are `critical`, lag at or over the threshold is `high`, `stopped` is `elevated`
- `deployments[]`: `{ deployment, overall_status, processes, running, issues[] }`

---

## `get_process_report`

Inputs: `deployment`, `process_type`, `process_name`, optional `tail_lines` (default 200, max 1000), `report` (e.g. `EXT10.rpt`).

- `report`, `total_lines`, `lines[]` (redacted tail)
- `ogg_messages[]`: last 20 `{ level, code, text }` found in the whole report

---

## `get_process_checkpoints` / `get_deployment_messages`

`get_process_checkpoints`: GoldenGate checkpoint body (`current`, and `history` with the `history` input).

`get_deployment_messages`: `{ deployment, total, messages[] }`, newest last; `limit` defaults to 50, max 500.

---

## Write tools

All accept `dry_run` and `change_ticket`. Successful results include `correlation_id`.

- `dry_run: true`: `{ dry_run, operation, deployment, process_type, targets[], change_ticket_required, note }`, where each target is `{ name, current_status, would_change }` or `{ name, error }`
- `restart_process`: inputs `deployment`, `process_name`, `process_type`, optional `timeout_seconds` (default 120). Returns `{ success, previous_status, status }`, plus `error` if the process would not stop

---

## `save_config_snapshot` / `list_config_backups` / `compare_deployment_configs`

- `save_config_snapshot`: `{ backup_file, deployment, extracts, replicats }`
- `list_config_backups`: `{ deployment, backups[]: { backup_file, size_bytes } }`
- `compare_deployment_configs` with `backup_file`: `identical`, `summary{ added_processes, removed_processes, modified_processes }`, `differences[]: { process, type, field, change, diff[] }` (unified diff lines), `compared_with`, timestamps

---

## Governance tools (admin role)

- `search_audit_log`: filters `action`, `user`, `deployment`, `event_type`, `success`, `since_hours`, `limit` (default 100, max 1000). Returns `{ count, entries[] }`
- `verify_audit_log`: `{ ok, entries, legacy_entries, first_seq, last_seq, head_hash, error }`
- `get_server_posture`: `{ version, read_only, metrics_enabled, policy{}, deployments[], findings[] }`

---

## `get_extract_lag` / `get_replicat_lag`

Structured lag object (Pydantic model `ProcessLag`). Key fields:

| Field | Type | Meaning |
|--------|------|--------|
| `deployment` | string | Deployment name |
| `process_name` | string | Extract or Replicat name |
| `process_type` | `"extract"` \| `"replicat"` | Process kind |
| `status` | string | `running` / `stopped` / `abended` / `unknown` |
| `lag_seconds` | number \| null | Parsed lag |
| `lag_at_checkpoint` | string \| null | API lag-at-checkpoint text |
| `time_since_checkpoint` | string \| null | API time-since-checkpoint text |
| `baseline_mean_seconds` | number \| null | 7-day mean from MetricsStore (null if `GG_ENABLE_METRICS=false`) |
| `baseline_p95_seconds` | number \| null | 7-day p95 (null if metrics disabled) |
| `baseline_std_dev_seconds` | number \| null | 7-day std dev (null if metrics disabled) |
| `baseline_data_points` | int | Count of baseline samples (0 if metrics disabled) |
| `deviation_sigma` | number \| null | (current − mean) / σ when σ > 0 (null if metrics disabled) |
| `severity` | string | Deterministic label |
| `raw_lag` | object | Original API lag payload |
| `collected_at_utc` | string | ISO timestamp |

---

## `get_deployment_health`

- `deployment`, `timestamp`, `overall_status` (`healthy` \| `degraded` \| `error`)
- `extracts[]`, `replicats[]`: `{ name, status, lag? }` where `lag` is raw API data when fetched
- `issues[]`: human-readable strings (not structured metrics)

---

## `diagnose_lag_issue`

> ⚠️ Requires `GG_ENABLE_METRICS=true`. Not available in pure REST pass-through mode.

Primary numeric fields (always prefer these for reasoning):

- `current_lag_seconds`, `baseline_mean_seconds`, `baseline_std_dev_seconds`, `baseline_p95_seconds`
- `deviation_sigma`, `severity`
- `contributing_factors[]`: objects with `factor` and tool-specific numeric fields (`current_value`, thresholds, etc.)

Supporting context:

- `likely_causes[]`, `recommendations[]`, `evidence{}`, `confidence_score`, `timestamp`

---

## `get_performance_baseline`

> ⚠️ Requires `GG_ENABLE_METRICS=true`. Not available in pure REST pass-through mode.

- `deployment`, `process`, `baseline_stats` (from `MetricsStore.calculate_baseline`), `hourly_pattern`, `note`

> **Note:** Requires history to have accumulated in the metrics store (7-day window). Returns `"Insufficient data"` on a fresh instance until the background collection loop has run for a reasonable period.

## `get_lag_trend`

> ⚠️ Requires `GG_ENABLE_METRICS=true`. Not available in pure REST pass-through mode.

- `deployment`, `process`, `time_range`, `data_points`, `trend{...}`, `history[]`

> **Note:** Returns empty history on a fresh instance. Data populates automatically via the 5-minute background collection loop as long as the process is running.

## `get_troubleshooting_guide`

- `symptom_analysis`, `steps[]`, `common_causes[]`, `prevention[]`

## `check_database_correlation`

- `deployment`, `database`, `timestamp`, `database_health`, `goldengate_health`, `analysis[]`

## `list_deployments`

- `deployments[]`: `{ name, status, version?, base_url, error? }`

## `get_deployment_info`, `list_services`, `list_extracts`, `list_replicats`

- GoldenGate REST JSON as returned by the client (typically `items[]` or deployment wrapper).

## `get_extract_status`, `get_replicat_status`, `get_process_statistics`, `check_process_errors`

- API JSON from the underlying GG endpoints.

## Write tools (`start_*`, `stop_*`, `batch_*`)

- Success: API result object from GG.
- Blocked in read-only: `{ "error", "reason", "operation" }`.

## `get_all_process_health`

- `extracts[]`, `replicats[]`, `summary` with aggregate counts and `total_lag_seconds`.

## `list_trails`, `get_trail_info`

- API JSON for trails. `get_trail_info` accepts an optional `trail_path` parameter (e.g. `"XYZ"`, `"ABCD"`) passed as `?path=` to the GoldenGate API — required when multiple trails share the same name prefix on a deployment.

## `backup_deployment_config`

- Nested object: `timestamp`, `deployment_url`, `extracts{}`, `replicats{}`, `deployment_info` (from `backup_all_configs`). **Not** written to disk by the server.

## `get_process_config`

- Process detail including optional `parameter_file` from REST.

## `compare_deployment_configs`

- With `backup_file`: `{ identical, differences, summary, compared_with, ... }`.
- Without: `{ message, current_config }`.

---

## Source layout

Tool **schemas** and **dispatch** registrations live under `src/goldengate_mcp_server/tools/`:

| Module | Tools |
|--------|--------|
| `deployment.py` | list_deployments, get_deployment_info, list_services |
| `extract.py` | list_extracts, get_extract_status, get_extract_lag |
| `replicat.py` | list_replicats, get_replicat_status, get_replicat_lag |
| `health.py` | get_process_statistics, get_deployment_health, check_process_errors |
| `write_ops.py` | start/stop extract/replicat, batch_start/stop |
| `diagnostics_tools.py` | diagnose_lag_issue, get_performance_baseline, get_lag_trend, check_database_correlation, get_troubleshooting_guide |
| `operational.py` | list_trails, get_trail_info, get_all_process_health |
| `config_mgmt.py` | backup_deployment_config, get_process_config, compare_deployment_configs |

`server.py` wires `all_tools(enable_metrics=...)` and `build_dispatch(self, enable_metrics=...)` into the MCP server. When `GG_ENABLE_METRICS=false`, `METRICS_DEPENDENT_TOOLS` (`diagnose_lag_issue`, `get_performance_baseline`, `get_lag_trend`) are excluded from both the tool list and the dispatch table.
