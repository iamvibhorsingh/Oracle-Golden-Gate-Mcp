# Oracle GoldenGate MCP Server

**Your Replicat is lagging. Do you know why?** This [Model Context Protocol](https://modelcontextprotocol.io/) server talks to the Oracle GoldenGate Microservices REST API and returns **structured facts** (seconds, baselines, σ, severity enums) so assistants such as Claude or GPT can reason from actual, real evidence without hallucinating.

### Example conversation

1. **User:** “Why is `RPT01` behind on `prod`?”
2. **Assistant** calls `get_replicat_lag` / `diagnose_lag_issue`.
3. **Tool** returns JSON like `lag_seconds`, `baseline_mean_seconds`, `deviation_sigma`, `severity: "high"`, and `contributing_factors` with numeric thresholds.
4. **Assistant** answers strictly from those fields (e.g. “142s vs 7‑day mean 12.3s, ~31σ, discard rate above 5%…”).

### Lag severity labels (deterministic)

Classification uses the current lag, optional 7‑day baseline from the local metrics store, and process status (`running` / otherwise). Summary:

| Severity | When |
|----------|-------------------|
| **critical** | Process not running (`stopped` / `abended` / unknown non‑running), or lag **> 5σ** above baseline mean |
| **high** | **> 3σ**, or lag **> 2×** baseline mean, or (no σ) **> 3×** mean |
| **elevated** | **> 1.5σ**, or above **p95**, or **> 1.5×** mean |
| **normal** | Below the above gates or insufficient baseline (no mean → no alarm on lag alone) |

Full rules are implemented in `goldengate_mcp_server.models.classify_severity`. Feel free to tailor them according to your needs.

### Platform note

**This MCP server does not need to run on the GG host**—it only needs HTTPS reachability to the GG REST API. Run it in a container, on a jump host, or on a developer workstation with network access to the deployment.

## Why This vs OCI GoldenGate Dashboard?

| Feature | This MCP Server | OCI Dashboard (GG 23+) |
|---------|----------------|----------------------|
| **Natural Language** | ✅ Ask "why is replication slow?" | ❌ Click through menus |
| **Root Cause Analysis** | ✅ AI diagnoses issues | ❌ Shows symptoms only |
| **Optional Historical Analysis** | ✅ 30-day baselines & trends | ❌ Real-time only |
| **Multi-Deployment** | ✅ On-prem + cloud unified | ❌ Per-deployment only |
| **GG 21.x Support** | ✅ Works with 21.x ([19c partial, 12.3+ with changes](docs/VERSION_COMPATIBILITY.md)) | ❌ Requires 23.x |
| **Batch Operations** | ✅ Start/stop multiple processes | ❌ One at a time |
| **Database Correlation** | ✅ Links DB performance to lag | ❌ GG metrics only |
| **AI-Layer Audit Trail** | ✅ Every tool call logged with args & results. Documents what the AI touches. | ❌ Predates AI tooling |

## Key Features

### AI-Powered Intelligence
- **Root cause analysis** - Diagnoses why lag is high, not just that it is
- **Predictive baselines** - Knows what's normal for your environment
- **Smart recommendations** - Actionable fixes, not generic advice

### Operational Efficiency
- **Batch operations** - Start/stop multiple processes at once
- **Trail management** - Monitor and manage trail files
- **Unified health checks** - All processes in one call
- **Multi-deployment** - Manage on-prem and cloud from one place

### Enterprise Ready
- **Read-only by default** - Safe for production
- **Comprehensive audit logs** - Full compliance trail
- **Secure credentials** - Environment-based configuration
- **GG 21.x & 23.x** - Works with your existing infrastructure

## Quick Start

```bash
# 1. Install
pip install -e .

# 2. Configure (copy .env.example to .env and edit)
cp .env.example .env

# 3. Run
python -m goldengate_mcp_server
```

### Configuration (.env file)

```bash
# Security & logging (recommended)
GG_READ_ONLY=true                          # Default: true (read-only mode)
GG_REQUEST_TIMEOUT=30                      # HTTP timeout in seconds
GG_AUDIT_LOG_PATH=./logs/audit.log         # Audit trail (credentials redacted)
GG_AUDIT_LOG_MAX_BYTES=10485760            # Rotate audit log at 10 MB (0 = never rotate)
GG_AUDIT_LOG_BACKUP_COUNT=5                # Rotated files kept (audit.log.1 ... .5)

# Metrics (optional — off by default: pure REST pass-through with no local storage)
GG_ENABLE_METRICS=false                     # Default: false. Set true to enable SQLite,
                                           # background collection, and baseline tools.
GG_METRICS_DB_PATH=./data/metrics.db       # SQLite store for baselines (30-day retention,
                                           # ignored when GG_ENABLE_METRICS=false)

# Performance tuning
GG_CACHE_TTL_SECONDS=30                    # Response cache TTL (reduces GG API load)
GG_MAX_CONCURRENT_REQUESTS=20              # Max parallel requests
GG_REQUESTS_PER_SECOND=50                  # Rate limit

# Deployment 1 (GoldenGate 21)
# Use NAME for a single deployment, or NAMES for multiple deployments sharing
# the same Service Manager URL and credentials (comma-separated).
# Each name is used in the REST path (/services/<name>/adminsrvr/v2/...), so it must
# be the real GoldenGate deployment name. To use a friendly alias instead, set
# NAME=<alias> plus GG_DEPLOYMENT_<N>_GG_DEPLOYMENT=<real name> (single NAME only;
# in GG_DEPLOYMENTS JSON use "gg_deployment").
# GG_DEPLOYMENT_1_NAME=gg21_prod
GG_DEPLOYMENT_1_NAMES=gg21_prod,gg21_test   # shorthand when URL/creds are shared
GG_DEPLOYMENT_1_URL=https://gg21-server
GG_DEPLOYMENT_1_USERNAME=oggadmin
GG_DEPLOYMENT_1_PASSWORD=your-secure-password
GG_DEPLOYMENT_1_VERIFY_SSL=true
# Optional: path to a PEM CA bundle if GG uses an internal/corporate CA
# Combine root + issuing CAs into one file: cat RootCA.pem IssuingCA.pem > bundle.pem
# GG_DEPLOYMENT_1_CA_BUNDLE=/path/to/ca-bundle.pem

# Deployment 2 (GoldenGate 23)
GG_DEPLOYMENT_2_NAME=gg23_test
GG_DEPLOYMENT_2_URL=https://gg23-server:9100
GG_DEPLOYMENT_2_USERNAME=oggadmin
GG_DEPLOYMENT_2_PASSWORD=your-secure-password
GG_DEPLOYMENT_2_VERIFY_SSL=true
```

### Running the Server

```bash
# Run with environment variables
python -m goldengate_mcp_server

# Or use the installed command
goldengate-mcp-server
```

### MCP Client Configuration

Add to your MCP client configuration (e.g., `claude_desktop_config.json`):

```json
{
  "mcpServers": {
    "goldengate": {
      "command": "python",
      "args": ["-m", "goldengate_mcp_server"],
      "env": {
        "GG_READ_ONLY": "true",
        "GG_DEPLOYMENT_1_NAME": "gg21_prod",
        "GG_DEPLOYMENT_1_URL": "https://gg21-server",
        "GG_DEPLOYMENT_1_USERNAME": "oggadmin",
        "GG_DEPLOYMENT_1_PASSWORD": "your-password",
        "GG_DEPLOYMENT_1_VERIFY_SSL": "true"
        // Optional: "GG_DEPLOYMENT_1_CA_BUNDLE": "/path/to/ca-bundle.pem"
      }
    }
  }
}
```

## Available Tools

**Full response-shape reference:** see [TOOL_REFERENCE.md](TOOL_REFERENCE.md) (JSON fields per tool).

The MCP server provides the following tools for AI assistants:

### Deployment Management
- `list_deployments` — names, connectivity probe, API version hint
- `get_deployment_info` — deployment JSON from `/services/v2/deployments`
- `list_services` — services list

### Process Monitoring
- `list_extracts` / `list_replicats`
- `get_extract_status` / `get_replicat_status`
- `get_process_statistics` — throughput (inserts/updates/deletes), discards, operations/sec

### Performance & Lag (Structured, AI-Friendly)
- `get_extract_lag` / `get_replicat_lag` — **typed fields**: `lag_seconds`, `baseline_mean_seconds`, `baseline_p95_seconds`, `deviation_sigma`, `severity` enum, `raw_lag`, `collected_at_utc`
- `diagnose_lag_issue` — numeric summary + root cause analysis + `contributing_factors` + recommendations ⚠️ *requires metrics enabled*
- `get_performance_baseline` — 7-day statistics (mean, std dev, p95) + hourly pattern for capacity planning ⚠️ *requires metrics enabled*
- `get_lag_trend` — 24-hour history with min/max/mean and direction indicator ⚠️ *requires metrics enabled*
- `get_deployment_health` — all processes + health summary in one call

### Trail & Configuration Management
- `list_trails` — List all trail files in deployment
- `get_trail_info` — Trail metadata (size, sequence, age)
- `backup_deployment_config` — Snapshot all Extract/Replicat configs for comparison
- `get_process_config` — Full config + parameter file content for a single process
- `compare_deployment_configs` — Diff current vs backup (useful for drift detection)

### Diagnostic Intelligence
- `get_troubleshooting_guide` — Structured steps based on symptoms (high_lag, abended, stopped, etc.)
- `check_database_correlation` — Link GoldenGate lag to source/target DB performance (requires oracledb monitor)

### Error Management
- `check_process_errors` — Check for process errors and recent messages

### Process Control (Requires Write Mode)
- `start_extract` / `stop_extract` — Start/stop an Extract process
- `start_replicat` / `stop_replicat` — Start/stop a Replicat process
- `batch_start_processes` — Start multiple Extracts or Replicats in parallel
- `batch_stop_processes` — Stop multiple Extracts or Replicats in parallel

## Security Best Practices

### 1. Read-Only Mode (Default)

The server runs in read-only mode by default, preventing any write operations:

```bash
GG_READ_ONLY=true  # Default, safest option
```

To enable write operations (use with caution):

```bash
GG_READ_ONLY=false  # Allows start/stop operations
```

In read-only mode the start/stop/batch tools are not listed at all (and are still rejected
if called directly). Every tool carries MCP annotations: read tools have `readOnlyHint: true`;
`stop_*` / `batch_stop_processes` have `destructiveHint: true`.

### Optional REST wrapper

`goldengate-rest-server` exposes the same tools over HTTP. It binds to `127.0.0.1` by default.
Set `GG_REST_API_TOKEN` to require `Authorization: Bearer <token>` on `/api/tools*`; binding to
any non-loopback host without a token is refused unless you pass `--allow-unauthenticated`
(only do that behind a gateway that enforces auth).

```bash
GG_REST_API_TOKEN=$(openssl rand -hex 32) goldengate-rest-server --host 0.0.0.0 --port 8000
```

### 2. SSL/TLS Verification

**Always** verify SSL certificates in production:

```bash
GG_DEPLOYMENT_1_VERIFY_SSL=true  # Always use in production
```

Only disable for testing in isolated environments:

```bash
GG_DEPLOYMENT_1_VERIFY_SSL=false  # Testing only!
```

### 3. Credential Management

**Never commit credentials to version control!**

Best practices:
- Use environment variables
- Use secret management systems (HashiCorp Vault, AWS Secrets Manager, etc.)
- Restrict file permissions: `chmod 600 .env`
- Use dedicated service accounts with minimal privileges
- Rotate passwords regularly

### 4. Network Security

- Place the MCP server where it can reach the GoldenGate REST port over HTTPS (not necessarily on the GG node)
- Use firewall rules to restrict access
- Consider VPN for remote access
- Monitor network traffic

### 5. Audit Logging

All operations are logged to the audit log:

```bash
# View recent audit entries
tail -f ./logs/audit.log

# Search for specific actions
grep "start_extract" ./logs/audit.log
```

Audit logs include:
- Timestamp
- Action performed
- Arguments (with sensitive data redacted)
- Success/failure status
- Error details
- Hash chain (`seq`, `prev_hash`, `hash`) so edited, deleted or reordered entries are detectable

Check the chain (covers rotated files too):

```bash
python -c "from goldengate_mcp_server.audit import verify_audit_log; print(verify_audit_log('./logs/audit.log'))"
```

The chain is tamper-evident, not tamper-proof; see [docs/SECURITY.md](docs/SECURITY.md) for how to pin the head hash externally.

### 6. Minimal Permissions

Create a dedicated GoldenGate user with minimal required permissions:

```sql
-- Example: Create read-only monitoring user
CREATE USER gg_monitor IDENTIFIED BY secure_password;
GRANT CONNECT TO gg_monitor;
-- Grant only necessary privileges
```

## Architecture

```
           ┌─────────────────┐
           │   AI Assistant  │
           │                 │
           └────────┬────────┘
                    │ MCP Protocol
                    │
    ┌───────────────▼─────────────────┐
    │   GoldenGate MCP Server         │
    │                                 │
    │  ┌──────────────────────────┐   │
    │  │  Security Layer          │   │
    │  │  - Read-only mode        │   │
    │  │  - Input validation      │   │
    │  │  - Audit logging         │   │
    │  └──────────────────────────┘   │
    │                                 │
    │  ┌──────────────────────────┐   │
    │  │  GoldenGate API Client   │   │
    │  │  - REST API calls        │   │
    │  │  - Error handling        │   │
    │  │  - Retry logic           │   │
    │  └──────────────────────────┘   │
    └────────────────┬────────────────┘
                     │   HTTPS/REST
                     │
            ┌────────▼────────┐
            │   GoldenGate    │
            │   Deployment    │
            │   (21.x/23.x)   │
            └─────────────────┘
```

## Example Usage with AI Assistants

Once configured, you can interact with your GoldenGate deployments naturally:

**User**: "What's the status of all Extract processes in gg21_prod?"

**AI** (using MCP): *Calls list_extracts tool*

**User**: "Check if any Replicats have high lag"

**AI** (using MCP): *Calls get_deployment_health and analyzes lag metrics*

**User**: "Show me any processes with errors"

**AI** (using MCP): *Calls check_process_errors for each process*

## Troubleshooting

### Connection Issues

```bash
# Test GoldenGate API connectivity
curl -u oggadmin:password https://gg-server:9000/services/v2/deployments

# Check SSL certificate
openssl s_client -connect gg-server:9000 -showcerts
```

### Authentication Failures

- Verify credentials are correct
- Check user has necessary GoldenGate permissions
- Review audit logs: `tail -f ./logs/audit.log`

### SSL Certificate Errors

For development/testing only:
```bash
GG_DEPLOYMENT_1_VERIFY_SSL=false
```

For production, install proper certificates or add CA to trust store.

### Process Not Found Errors

- Verify process name spelling
- Check process exists: `list_extracts` or `list_replicats`
- Ensure you're querying the correct deployment

## Development

### Running Tests

```bash
# Install dev dependencies
pip install -e ".[dev]"

# Run tests
pytest

# Run with coverage
pytest --cov=goldengate_mcp_server --cov-report=html
```

### Code Quality

```bash
# Lint code (ruff)
ruff check src/

# Fix lint issues automatically
ruff check src/ --fix

# Type checking
mypy src/
```

### Contributing

1. Fork the repository
2. Create a feature branch
3. Make your changes
4. Add tests
5. Run quality checks
6. Submit a pull request

## Performance Features

- **Caching**: Responses cached for `GG_CACHE_TTL_SECONDS` (default 30s) to reduce GG API load
- **Rate Limiting**: Configurable via `GG_REQUESTS_PER_SECOND` (default 50) and `GG_MAX_CONCURRENT_REQUESTS` (default 20)
- **Async Operations**: Built on async/await — background metrics collection runs up to 50 deployments concurrently
- **Metrics Store** *(optional, off by default)*: SQLite database (`GG_METRICS_DB_PATH`) maintains 30-day baselines for lag analysis. Enable with `GG_ENABLE_METRICS=true`; left at `false` the server runs in zero-storage, pure REST mode.
- **Timeout Settings**: Adjust `GG_REQUEST_TIMEOUT` (default 30s) based on your network latency

## Limitations

- **Requires Microservices Architecture**: GoldenGate must be deployed in Microservices mode (REST API available since GG 12.3+, recommended 21c+)
- **Trail endpoints**: `list_trails` and `get_trail_info` may not be available on GG Free edition (Enterprise only)
- **Database correlation**: `check_database_correlation` requires optional oracledb monitoring config (`GG_ORACLEDB_*` env vars)
- **Write operations**: `start_*`, `stop_*`, `batch_*` require `GG_READ_ONLY=false` and should be tested thoroughly before production use
- **Metrics mode vs pass-through mode**: Pass-through is the default (`GG_ENABLE_METRICS=false`): no SQLite store or background collection — no disk usage, no baseline tools (`diagnose_lag_issue`, `get_performance_baseline`, `get_lag_trend`). Useful for large-scale deployments (1000s of instances) where you only need live REST monitoring. Set `GG_ENABLE_METRICS=true` for metrics mode, where the background collection loop runs up to **50 deployments concurrently** — practical ceiling is network/disk throughput, not deployment count.
- **SQLite storage at scale**: With metrics enabled, steady-state DB size grows with deployment count (~50 GB at 5000 deployments with 30-day retention). Size appropriately or keep `GG_ENABLE_METRICS=false` (the default) if local storage is a constraint.

## Database Correlation (Optional)

The `check_database_correlation` tool connects directly to Oracle Database to check DB-side metrics (CPU, wait time ratio, active sessions) and correlates them with GG lag — answering *"is this lag caused by the database being slow, or is it a GG problem?"*

It is **disabled by default** and returns a "not configured" message unless you set it up.

### Setup

**1. Install the Oracle driver:**
```bash
pip install oracledb
```

**2. Add env vars for each database you want to monitor:**
```bash
GG_DB_MONITOR_1_NAME=prod_source        # Name you'll use in tool calls
GG_DB_MONITOR_1_TYPE=oracle             # Only "oracle" supported currently
GG_DB_MONITOR_1_DSN=gg-db-host:1521/FREEPDB1
GG_DB_MONITOR_1_USERNAME=system
GG_DB_MONITOR_1_PASSWORD=your-password

# Optional second DB (e.g. target)
GG_DB_MONITOR_2_NAME=prod_target
GG_DB_MONITOR_2_DSN=gg-target-host:1521/ORCLPDB1
GG_DB_MONITOR_2_USERNAME=system
GG_DB_MONITOR_2_PASSWORD=your-password
```

**3. Use it:**
> "Is GoldenGate lag on EXT01 caused by the source database?"

The AI will call `check_database_correlation` with `database_name=prod_source` and compare GG lag against Oracle `v$sysmetric` (CPU, wait time ratio, session counts).

> **Note:** The database user needs `SELECT` privilege on `v$sysmetric` and `v$session`. A read-only monitoring account is sufficient.

## Using with GG Free vs Enterprise

| Tool / Feature | GG Free 23.x | GG Enterprise 21c+ |
|---|---|---|
| List / status / lag (extracts & replicats) | ✅ | ✅ |
| Start / stop processes | ✅ | ✅ |
| Batch start / stop | ✅ | ✅ |
| Deployment health & diagnostics | ✅ | ✅ |
| Process statistics (throughput) | ✅ partial (falls back to detail endpoint) | ✅ full `/statistics` endpoint |
| `list_trails` / `get_trail_info` | ❌ 404 | ✅ |
| Multiple deployments per Service Manager | ❌ single deployment (oracle limitation) | ✅ |
| `check_database_correlation` | ❌ requires separate oracledb config | ❌ requires separate oracledb config |
| Credential store (USERIDALIAS) | ✅ | ✅ |
| Classic Architecture (non-Microservices) | ❌ not supported since no API | ❌ not supported since no API |

**In practice**: all read and write tools work on GG Free. The two tools that return 404 on Free (`list_trails`, `get_trail_info`) degrade gracefully with an error message rather than crashing. Enterprise 21c+ unlocks trail management and richer statistics.

## Support and Resources

- **GoldenGate Documentation**: https://docs.oracle.com/goldengate/

## License

MIT License

## Security Features

- **Credential Redaction**: All sensitive data (passwords, tokens, secrets) automatically scrubbed from responses, audit logs, and error messages
- **Audit Logging**: Complete, hash-chained (tamper-evident) trail of all operations with timestamps, arguments (redacted), and results
- **Read-Only Default**: Safe by default — requires explicit `GG_READ_ONLY=false` for write operations
- **Input Validation**: Process names and parameters validated to prevent injection attacks
- **SSL/TLS Support**: Configurable certificate verification with strong defaults for production

## Disclaimer

This software is provided as-is, without warranty. Always test thoroughly in non-production environments before using in production. The author is not responsible for any data loss or system issues. This is a free and open source project.

**Important**: This server provides programmatic access to your GoldenGate infrastructure. Treat credentials and audit logs as sensitive. Use `GG_READ_ONLY=true` by default, store credentials in secret management systems, and regularly review audit logs for unexpected activity.

---

**Security note:** This server provides programmatic access to your GoldenGate infrastructure. Prefer read‑only mode, protect credentials (env / secret store), and review audit logs. `Config.to_file(..., include_secrets=False)` avoids writing plaintext passwords by default.