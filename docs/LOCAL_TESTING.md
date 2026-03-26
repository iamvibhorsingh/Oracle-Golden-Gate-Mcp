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
GG_DEPLOYMENT_1_NAME=local
GG_DEPLOYMENT_1_URL=https://localhost:9100
GG_DEPLOYMENT_1_USERNAME=oggadmin
GG_DEPLOYMENT_1_PASSWORD=GGMCP_Admin123
GG_DEPLOYMENT_1_VERIFY_SSL=false   # GG Free uses a self-signed cert, ssl will fail locally
```

Then run the MCP server as usual:

```bash
python -m goldengate_mcp_server
```

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
bash scripts/setup_gg_processes.sh
```

Configures 10 Extract/Replicat pairs across multiple tables. Useful for testing batch operations and lag diagnostics.

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
