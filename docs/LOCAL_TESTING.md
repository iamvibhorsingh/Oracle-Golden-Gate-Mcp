# Local Testing with Docker

The included `docker-compose.yml` spins up a full Oracle DB + GoldenGate Free stack locally so you can test the MCP server without needing a real GoldenGate environment.

## Prerequisites

- [Docker Desktop](https://www.docker.com/products/docker-desktop/) running
- Both images are **public** — no Oracle container registry login required

## Start the stack

```bash
docker compose up -d
```

This starts two containers:

| Container | Purpose | Port |
|-----------|---------|------|
| `ggmcp-oracle-db` | Oracle Database Free (source/target DB) | 1521 |
| `ggmcp-goldengate` | Oracle GoldenGate Free (Microservices REST API) | 9100 → 8443 |

First startup takes a few minutes while Oracle initializes. GoldenGate waits for the DB to be healthy before starting.

## Default credentials

| Service | Username | Password |
|---------|----------|----------|
| Oracle DB | `sys` / `system` | `GGMCP_Admin123` |
| GoldenGate admin | `oggadmin` | `GGMCP_Admin123` |

To use different passwords, set them in a `.env.local` file before starting:

```bash
# .env.local
ORACLE_PWD=MyPassword123#
OGG_ADMIN_PWD=MyPassword123#
```

Finally, run:
```bash
docker compose --env-file .env.local up -d
```

## Point the MCP server at it

Create a `.env` file (or copy `.env.example`) with the local GoldenGate endpoint:

```bash
GG_DEPLOYMENT_1_NAME=LocalTest
GG_DEPLOYMENT_1_URL=https://localhost:9100
GG_DEPLOYMENT_1_USERNAME=oggadmin
GG_DEPLOYMENT_1_PASSWORD=GGMCP_Admin123
GG_DEPLOYMENT_1_VERIFY_SSL=false   # GG Free uses a self-signed cert, ssl will fail locally
GG_READ_ONLY=false                 # set to true if you only need monitoring, not start/stop
```

Then run the MCP server as usual:

```bash
python -m goldengate_mcp_server
```

### MCP client config (Claude Code and similar tools)

If running via an MCP client rather than a `.env` file, pass the environment variables directly in the client config. On some systems the server may fail to write its audit log or metrics database if the default relative paths (`./logs/audit.log`, `./data/metrics.db`) can't be resolved from the client's working directory. Use absolute paths in that case:

```json
{
  "goldengate": {
    "command": "python",
    "args": ["-m", "goldengate_mcp_server"],
    "env": {
      "GG_READ_ONLY": "true",
      "GG_DEPLOYMENT_1_NAME": "LocalTest",
      "GG_DEPLOYMENT_1_URL": "https://localhost:9100",
      "GG_DEPLOYMENT_1_USERNAME": "oggadmin",
      "GG_DEPLOYMENT_1_PASSWORD": "GGMCP_Admin123",
      "GG_DEPLOYMENT_1_VERIFY_SSL": "false",
      "GG_AUDIT_LOG_PATH": "/absolute/path/to/project/logs/audit.log",
      "GG_METRICS_DB_PATH": "/absolute/path/to/project/data/metrics.db",
      "GG_ENABLE_METRICS": "true"
    }
  }
}
```

On Windows use double-backslash paths: `"C:\\something\\your\\project\\logs\\audit.log"`.

## Verify GoldenGate is up

```bash
curl -sk -u oggadmin:GGMCP_Admin123 https://localhost:9100/services/v2/deployments
```

## Set up replication processes

Once the stack is up, use the scripts in `scripts/` to configure GoldenGate:

### Minimal setup (1 Extract + 1 Replicat)

```bash
pip install httpx python-dotenv
python scripts/setup_local_replication.py
```

Creates a single Extract (`EXT1`) and Replicat (`REP1`) — good for basic MCP server testing.

### Stress-test setup (10 Extracts + 10 Replicats)

```bash
python scripts/setup_gg_processes.py
```

Configures 10 Extract/Replicat pairs across multiple tables. The script handles everything in order:

1. Creates a credential store with `ggadmin_src` and `ggadmin_tgt` aliases (both point to the same `ggadmin` DB user)
2. Writes parameter files for all 10 Extracts and Replicats
3. Creates and registers the 10 Extract processes (with LogMiner registration)
4. Creates the checkpoint table (`ggadmin.chkptab`) required by all Replicats
5. Creates the 10 Replicat processes bound to that checkpoint table
6. Starts all processes

> **Important:** The checkpoint table (`ggadmin.chkptab`) must exist before any Replicat can start. The script creates it automatically via `ADD CHECKPOINTTABLE ggadmin.chkptab`. If you ever recreate Replicats manually (e.g. after `DELETE REPLICAT`), you must re-specify `CHECKPOINTTABLE ggadmin.chkptab` in the `ADD REPLICAT` command — not in the `.prm` parameter file.

> **Note on table specs:** GoldenGate Free connects directly to `FREEPDB1` via the credential alias. Table names in `TABLE` and `MAP` parameters must **not** include the PDB prefix — use `GG_SRC.CUSTOMERS`, not `FREEPDB1.GG_SRC.CUSTOMERS`.

### Generate load

```bash
pip install oracledb
python scripts/generate_load.py
```

Runs continuous DML (inserts/updates/deletes) against the source tables so the Extract processes have redo traffic to capture. Options: `--rate 50` (ops/sec), `--duration 300` (seconds).

### Verify replication end-to-end

```bash
python scripts/test_replication.py
```

Inserts a row into the source schema and confirms it appears on the target, then checks Extract/Replicat status via the REST API.

## Stop and clean up

```bash
# Stop containers (keeps data volumes)
docker compose down

# Stop and remove all data
docker compose down -v
```

## Notes

- The GoldenGate REST API is available at `https://localhost:9100/services/v2`
- GoldenGate Microservices console: `https://localhost:9100`
- Oracle Enterprise Manager Express: `https://localhost:5500/em`
- `list_trails` and `get_trail_info` return 404 on GG Free — this is expected (Enterprise only)
- **Lag metrics are null on GG Free** — the REST API does not populate `lag_at_chkpt` or `time_since_chkpt`. The MCP tools (`get_extract_lag`, `get_replicat_lag`) will return `null` lag values. To see real lag and record counts, check the process report files directly:
  ```bash
  docker exec ggmcp-goldengate tail -30 /u02/Deployment/var/lib/report/EXT01.rpt
  docker exec ggmcp-goldengate tail -30 /u02/Deployment/var/lib/report/REP01.rpt
  ```
- **SSL verification must be disabled** for local Docker (`GG_DEPLOYMENT_1_VERIFY_SSL=false`) — GG Free uses a self-signed certificate
- The MCP's sigma-based severity classification requires ~7 days of baseline data to produce meaningful lag alerts; on a fresh deployment all severity levels will show as `normal`
- **Metrics disabled mode**: Set `GG_ENABLE_METRICS=false` to skip SQLite entirely — useful if you only want live status/lag checks without storing history locally. The three baseline tools (`diagnose_lag_issue`, `get_performance_baseline`, `get_lag_trend`) will not appear when metrics are disabled.

## Troubleshooting

| Symptom | Cause | Fix |
|---------|-------|-----|
| Extract starts then immediately stops, no errors in API | `TABLE` spec includes PDB prefix (`FREEPDB1.GG_SRC.TABLE`) | Use `GG_SRC.TABLE` — no catalog prefix |
| Replicat abends: `OGG-02603 Checkpoint table does not exist` | Checkpoint table not created, or `ADD REPLICAT` used wrong schema | Run `ADD CHECKPOINTTABLE ggadmin.chkptab` via AdminClient; use `ggadmin.chkptab` (not `ggadmin_tgt.chkptab`) |
| Replicat abends: `OGG-10144 CHECKPOINTTABLE not valid` | `CHECKPOINTTABLE` was put in the `.prm` file | Remove it from the `.prm` — it belongs only in the `ADD REPLICAT` command |
| SSL connection error on MCP startup | Self-signed cert | Set `GG_DEPLOYMENT_1_VERIFY_SSL=false` |
| MCP shows deployment as `error` with SSL failure | Same as above | Same fix |
