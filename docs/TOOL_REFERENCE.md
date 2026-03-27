# Tool reference — inputs and response shapes

Tools return JSON serialized as text in the MCP `TextContent` payload (`indent=2`). Shapes below describe the **objects** clients parse from that JSON.

Severity where used is always one of: `normal` | `elevated` | `high` | `critical` (see README threshold table and `classify_severity`).

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
| `baseline_mean_seconds` | number \| null | 7-day mean from MetricsStore |
| `baseline_p95_seconds` | number \| null | 7-day p95 |
| `baseline_std_dev_seconds` | number \| null | 7-day std dev |
| `baseline_data_points` | int | Count of baseline samples |
| `deviation_sigma` | number \| null | (current − mean) / σ when σ > 0 |
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

Primary numeric fields (always prefer these for reasoning):

- `current_lag_seconds`, `baseline_mean_seconds`, `baseline_std_dev_seconds`, `baseline_p95_seconds`
- `deviation_sigma`, `severity`
- `contributing_factors[]`: objects with `factor` and tool-specific numeric fields (`current_value`, thresholds, etc.)

Supporting context:

- `likely_causes[]`, `recommendations[]`, `evidence{}`, `confidence_score`, `timestamp`

---

## `get_performance_baseline`

- `deployment`, `process`, `baseline_stats` (from `MetricsStore.calculate_baseline`), `hourly_pattern`, `note`

> **Note:** Requires history to have accumulated in the metrics store (7-day window). Returns `"Insufficient data"` on a fresh instance until the background collection loop has run for a reasonable period.

## `get_lag_trend`

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

- API JSON for trails.

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

`server.py` wires `all_tools()` and `build_dispatch(self)` into the MCP server.
