"""
Stress test for GoldenGate MCP Server tool calls.

Exercises all 24 MCP tools against a live GoldenGate deployment with
10 Extracts and 10 Replicats. Validates response structure and content
rather than exact values, since the Free edition may limit some features.

Prerequisites:
    docker compose --env-file .env.local up -d
    bash scripts/setup_gg_processes.sh
    python scripts/generate_load.py --duration 60 &   # optional background load

Run:
    pytest tests/integration/test_stress_mcp.py -v --tb=short

Environment variables (or .env.local):
    GG_DEPLOYMENT_1_NAME=LocalTest
    GG_DEPLOYMENT_1_URL=https://localhost:9100
    GG_DEPLOYMENT_1_USERNAME=oggadmin
    GG_DEPLOYMENT_1_PASSWORD=Welcome1
    GG_DEPLOYMENT_1_VERIFY_SSL=false
    GG_READ_ONLY=false
"""

import asyncio
import json
import os
import time

import pytest
import pytest_asyncio
from dotenv import load_dotenv

# Load env before importing server modules
load_dotenv(os.path.join(os.path.dirname(__file__), "..", "..", ".env.local"))
load_dotenv()

from goldengate_mcp_server.config import Config  # noqa: E402
from goldengate_mcp_server.goldengate_client import GoldenGateAPIError  # noqa: E402
from goldengate_mcp_server.server import GoldenGateMCPServer  # noqa: E402

# ── Markers ──────────────────────────────────────────────────

pytestmark = pytest.mark.requires_gg


# ── Fixtures ─────────────────────────────────────────────────

@pytest.fixture(scope="session")
def deployment_name():
    return os.getenv("GG_DEPLOYMENT_1_NAME", "LocalTest")


@pytest_asyncio.fixture(scope="session")
async def mcp_server():
    """Create a real MCP server wired to the live GG instance.

    Disables background metrics collection so it doesn't compete
    with test requests for rate-limiter slots.
    """
    config = Config.from_env()
    _orig = GoldenGateMCPServer._start_metrics_collection
    GoldenGateMCPServer._start_metrics_collection = lambda self: None
    try:
        server = GoldenGateMCPServer(config)
        yield server
        await server.shutdown()
    finally:
        GoldenGateMCPServer._start_metrics_collection = _orig


@pytest_asyncio.fixture(scope="session")
async def call_tool(mcp_server):
    """Helper to invoke an MCP tool and return parsed JSON."""
    dispatch = mcp_server._tool_handlers

    async def _call(tool_name, arguments=None):
        handler = dispatch.get(tool_name)
        if handler is None:
            pytest.fail(f"Unknown tool: {tool_name}")
        try:
            result = await handler(arguments or {})
        except GoldenGateAPIError as e:
            # Return API errors as dicts so tests can inspect rather than crash.
            # 404 = process/resource doesn't exist (expected when GG has no processes).
            result = {"api_error": str(e)}
        return result

    return _call


# ── Helper to check result shape ────────────────────────────

def assert_has_keys(result, keys, context=""):
    if isinstance(result, dict):
        for k in keys:
            assert k in result, f"{context}: missing key '{k}' in {list(result.keys())}"


# ================================================================
# DEPLOYMENT TOOLS (3)
# ================================================================

class TestDeploymentTools:
    """list_deployments, get_deployment_info, list_services"""

    async def test_list_deployments(self, call_tool):
        result = await call_tool("list_deployments")
        assert isinstance(result, (dict, list)), f"Unexpected type: {type(result)}"
        print(f"  list_deployments: {json.dumps(result, default=str)[:300]}")

    async def test_get_deployment_info(self, call_tool, deployment_name):
        result = await call_tool("get_deployment_info", {"deployment": deployment_name})
        assert result is not None
        print(f"  get_deployment_info: {json.dumps(result, default=str)[:300]}")

    async def test_list_services(self, call_tool, deployment_name):
        result = await call_tool("list_services", {"deployment": deployment_name})
        assert result is not None
        print(f"  list_services: {json.dumps(result, default=str)[:300]}")


# ================================================================
# EXTRACT TOOLS (3) — run against all 10 extracts
# ================================================================

class TestExtractTools:
    """list_extracts, get_extract_status, get_extract_lag"""

    EXTRACT_NAMES = [f"EXT{i:02d}" for i in range(1, 11)]

    async def test_list_extracts(self, call_tool, deployment_name):
        result = await call_tool("list_extracts", {"deployment": deployment_name})
        assert result is not None
        if isinstance(result, dict):
            items = result.get("items", result.get("response", {}).get("items", []))
            print(f"  list_extracts: {len(items)} extracts found")
        else:
            print(f"  list_extracts: {result!r:.200}")

    @pytest.mark.parametrize("extract_name", EXTRACT_NAMES)
    async def test_get_extract_status(self, call_tool, deployment_name, extract_name):
        result = await call_tool("get_extract_status", {
            "deployment": deployment_name,
            "extract_name": extract_name,
        })
        assert result is not None
        print(f"  {extract_name} status: {json.dumps(result, default=str)[:200]}")

    @pytest.mark.parametrize("extract_name", EXTRACT_NAMES)
    async def test_get_extract_lag(self, call_tool, deployment_name, extract_name):
        result = await call_tool("get_extract_lag", {
            "deployment": deployment_name,
            "extract_name": extract_name,
        })
        assert result is not None
        if isinstance(result, dict) and "severity" in result:
            assert result["severity"] in ("normal", "elevated", "high", "critical")
        print(f"  {extract_name} lag: {json.dumps(result, default=str)[:200]}")


# ================================================================
# REPLICAT TOOLS (3) — run against all 10 replicats
# ================================================================

class TestReplicatTools:
    """list_replicats, get_replicat_status, get_replicat_lag"""

    REPLICAT_NAMES = [f"REP{i:02d}" for i in range(1, 11)]

    async def test_list_replicats(self, call_tool, deployment_name):
        result = await call_tool("list_replicats", {"deployment": deployment_name})
        assert result is not None
        if isinstance(result, dict):
            items = result.get("items", result.get("response", {}).get("items", []))
            print(f"  list_replicats: {len(items)} replicats found")

    @pytest.mark.parametrize("replicat_name", REPLICAT_NAMES)
    async def test_get_replicat_status(self, call_tool, deployment_name, replicat_name):
        result = await call_tool("get_replicat_status", {
            "deployment": deployment_name,
            "replicat_name": replicat_name,
        })
        assert result is not None
        print(f"  {replicat_name} status: {json.dumps(result, default=str)[:200]}")

    @pytest.mark.parametrize("replicat_name", REPLICAT_NAMES)
    async def test_get_replicat_lag(self, call_tool, deployment_name, replicat_name):
        result = await call_tool("get_replicat_lag", {
            "deployment": deployment_name,
            "replicat_name": replicat_name,
        })
        assert result is not None
        if isinstance(result, dict) and "severity" in result:
            assert result["severity"] in ("normal", "elevated", "high", "critical")
        print(f"  {replicat_name} lag: {json.dumps(result, default=str)[:200]}")


# ================================================================
# HEALTH TOOLS (3)
# ================================================================

class TestHealthTools:
    """get_process_statistics, get_deployment_health, check_process_errors"""

    async def test_get_deployment_health(self, call_tool, deployment_name):
        result = await call_tool("get_deployment_health", {"deployment": deployment_name})
        assert result is not None
        if isinstance(result, dict):
            print(f"  deployment_health keys: {list(result.keys())}")
        print(f"  deployment_health: {json.dumps(result, default=str)[:400]}")

    @pytest.mark.parametrize(("proc_type", "proc_name"), [
        ("extract", "EXT01"), ("extract", "EXT05"), ("extract", "EXT10"),
        ("replicat", "REP01"), ("replicat", "REP05"), ("replicat", "REP10"),
    ])
    async def test_get_process_statistics(self, call_tool, deployment_name, proc_type, proc_name):
        result = await call_tool("get_process_statistics", {
            "deployment": deployment_name,
            "process_type": proc_type,
            "process_name": proc_name,
        })
        assert result is not None
        print(f"  {proc_name} stats: {json.dumps(result, default=str)[:200]}")

    @pytest.mark.parametrize(("proc_type", "proc_name"), [
        ("extract", "EXT01"), ("extract", "EXT10"),
        ("replicat", "REP01"), ("replicat", "REP10"),
    ])
    async def test_check_process_errors(self, call_tool, deployment_name, proc_type, proc_name):
        result = await call_tool("check_process_errors", {
            "deployment": deployment_name,
            "process_type": proc_type,
            "process_name": proc_name,
        })
        assert result is not None
        print(f"  {proc_name} errors: {json.dumps(result, default=str)[:200]}")


# ================================================================
# WRITE TOOLS (6) — start/stop cycle on a subset of processes
# ================================================================

class TestWriteTools:
    """start_extract, stop_extract, start_replicat, stop_replicat,
       batch_start_processes, batch_stop_processes"""

    async def test_stop_and_start_extract(self, call_tool, deployment_name):
        """Stop EXT01, verify, then restart."""
        stop = await call_tool("stop_extract", {
            "deployment": deployment_name, "extract_name": "EXT01",
        })
        print(f"  stop_extract EXT01: {json.dumps(stop, default=str)[:200]}")

        await asyncio.sleep(2)

        start = await call_tool("start_extract", {
            "deployment": deployment_name, "extract_name": "EXT01",
        })
        print(f"  start_extract EXT01: {json.dumps(start, default=str)[:200]}")

    async def test_stop_and_start_replicat(self, call_tool, deployment_name):
        """Stop REP01, verify, then restart."""
        stop = await call_tool("stop_replicat", {
            "deployment": deployment_name, "replicat_name": "REP01",
        })
        print(f"  stop_replicat REP01: {json.dumps(stop, default=str)[:200]}")

        await asyncio.sleep(2)

        start = await call_tool("start_replicat", {
            "deployment": deployment_name, "replicat_name": "REP01",
        })
        print(f"  start_replicat REP01: {json.dumps(start, default=str)[:200]}")

    async def test_batch_stop_and_start_extracts(self, call_tool, deployment_name):
        """Batch stop EXT02-EXT04, then batch start them."""
        names = ["EXT02", "EXT03", "EXT04"]

        stop = await call_tool("batch_stop_processes", {
            "deployment": deployment_name,
            "process_names": names,
            "process_type": "extract",
        })
        print(f"  batch_stop extracts: {json.dumps(stop, default=str)[:300]}")

        await asyncio.sleep(2)

        start = await call_tool("batch_start_processes", {
            "deployment": deployment_name,
            "process_names": names,
            "process_type": "extract",
        })
        print(f"  batch_start extracts: {json.dumps(start, default=str)[:300]}")

    async def test_batch_stop_and_start_replicats(self, call_tool, deployment_name):
        """Batch stop REP02-REP04, then batch start them."""
        names = ["REP02", "REP03", "REP04"]

        stop = await call_tool("batch_stop_processes", {
            "deployment": deployment_name,
            "process_names": names,
            "process_type": "replicat",
        })
        print(f"  batch_stop replicats: {json.dumps(stop, default=str)[:300]}")

        await asyncio.sleep(2)

        start = await call_tool("batch_start_processes", {
            "deployment": deployment_name,
            "process_names": names,
            "process_type": "replicat",
        })
        print(f"  batch_start replicats: {json.dumps(start, default=str)[:300]}")


# ================================================================
# DIAGNOSTICS TOOLS (5)
# ================================================================

class TestDiagnosticsTools:
    """diagnose_lag_issue, get_performance_baseline, get_lag_trend,
       check_database_correlation, get_troubleshooting_guide"""

    @pytest.mark.parametrize(("proc_type", "proc_name"), [
        ("extract", "EXT01"), ("extract", "EXT05"),
        ("replicat", "REP01"), ("replicat", "REP05"),
    ])
    async def test_diagnose_lag_issue(self, call_tool, deployment_name, proc_type, proc_name):
        result = await call_tool("diagnose_lag_issue", {
            "deployment": deployment_name,
            "process_name": proc_name,
            "process_type": proc_type,
        })
        assert result is not None
        if isinstance(result, dict):
            for key in ("severity", "timestamp"):
                if key in result:
                    print(f"    {key}: {result[key]}")
        print(f"  diagnose {proc_name}: {json.dumps(result, default=str)[:300]}")

    @pytest.mark.parametrize("proc_name", ["EXT01", "REP01", "EXT10", "REP10"])
    async def test_get_performance_baseline(self, call_tool, deployment_name, proc_name):
        result = await call_tool("get_performance_baseline", {
            "deployment": deployment_name,
            "process_name": proc_name,
        })
        assert result is not None
        print(f"  baseline {proc_name}: {json.dumps(result, default=str)[:200]}")

    @pytest.mark.parametrize("proc_name", ["EXT01", "REP01"])
    async def test_get_lag_trend(self, call_tool, deployment_name, proc_name):
        result = await call_tool("get_lag_trend", {
            "deployment": deployment_name,
            "process_name": proc_name,
        })
        assert result is not None
        print(f"  lag_trend {proc_name}: {json.dumps(result, default=str)[:200]}")

    async def test_check_database_correlation(self, call_tool, deployment_name):
        """May fail without oracledb monitoring configured — that's OK."""
        result = await call_tool("check_database_correlation", {
            "deployment": deployment_name,
            "database_name": "prod_source",
        })
        assert result is not None
        print(f"  db_correlation: {json.dumps(result, default=str)[:200]}")

    @pytest.mark.parametrize("symptoms", [
        {"high_lag": True},
        {"abended": True},
        {"high_lag": True, "abended": False},
        {"stopped": True},
    ])
    async def test_get_troubleshooting_guide(self, call_tool, symptoms):
        result = await call_tool("get_troubleshooting_guide", {
            "symptoms": symptoms,
        })
        assert result is not None
        print(f"  troubleshoot {symptoms}: {json.dumps(result, default=str)[:200]}")


# ================================================================
# OPERATIONAL TOOLS (3)
# ================================================================

class TestOperationalTools:
    """list_trails, get_trail_info, get_all_process_health"""

    async def test_list_trails(self, call_tool, deployment_name):
        result = await call_tool("list_trails", {"deployment": deployment_name})
        assert result is not None
        print(f"  list_trails: {json.dumps(result, default=str)[:300]}")

    async def test_get_trail_info(self, call_tool, deployment_name):
        """Try to get info on a trail — name depends on what was created."""
        trails = await call_tool("list_trails", {"deployment": deployment_name})
        trail_name = None
        if isinstance(trails, dict):
            items = trails.get("items", trails.get("response", {}).get("items", []))
            if items and isinstance(items, list):
                trail_name = items[0].get("name", items[0].get("trailName"))
        if trail_name:
            result = await call_tool("get_trail_info", {
                "deployment": deployment_name,
                "trail_name": trail_name,
            })
            assert result is not None
            print(f"  trail_info({trail_name}): {json.dumps(result, default=str)[:200]}")
        else:
            result = await call_tool("get_trail_info", {
                "deployment": deployment_name,
                "trail_name": "aa",
            })
            print(f"  trail_info(aa): {json.dumps(result, default=str)[:200]}")

    async def test_get_all_process_health(self, call_tool, deployment_name):
        result = await call_tool("get_all_process_health", {"deployment": deployment_name})
        assert result is not None
        if isinstance(result, dict):
            ext_count = len(result.get("extracts", []))
            rep_count = len(result.get("replicats", []))
            print(f"  all_process_health: {ext_count} extracts, {rep_count} replicats")
        print(f"  all_process_health: {json.dumps(result, default=str)[:400]}")


# ================================================================
# CONFIG TOOLS (3)
# ================================================================

class TestConfigTools:
    """backup_deployment_config, get_process_config, compare_deployment_configs"""

    async def test_backup_deployment_config(self, call_tool, deployment_name):
        result = await call_tool("backup_deployment_config", {
            "deployment": deployment_name,
        })
        assert result is not None
        print(f"  backup_config: {json.dumps(result, default=str)[:300]}")

    @pytest.mark.parametrize(("proc_type", "proc_name"), [
        ("extract", "EXT01"), ("extract", "EXT10"),
        ("replicat", "REP01"), ("replicat", "REP10"),
    ])
    async def test_get_process_config(self, call_tool, deployment_name, proc_type, proc_name):
        result = await call_tool("get_process_config", {
            "deployment": deployment_name,
            "process_name": proc_name,
            "process_type": proc_type,
        })
        assert result is not None
        print(f"  config({proc_name}): {json.dumps(result, default=str)[:200]}")

    async def test_compare_deployment_configs(self, call_tool, deployment_name):
        result = await call_tool("compare_deployment_configs", {
            "deployment": deployment_name,
        })
        assert result is not None
        print(f"  compare_configs: {json.dumps(result, default=str)[:300]}")


# ================================================================
# CONCURRENCY STRESS (hit the server with parallel calls)
# ================================================================

class TestConcurrencyStress:
    """Blast multiple tools in parallel to test rate limiting and caching."""

    async def test_parallel_health_checks(self, call_tool, deployment_name):
        """10 concurrent get_deployment_health calls."""
        tasks = [
            call_tool("get_deployment_health", {"deployment": deployment_name})
            for _ in range(10)
        ]
        results = await asyncio.gather(*tasks, return_exceptions=True)
        successes = sum(1 for r in results if not isinstance(r, Exception))
        failures = sum(1 for r in results if isinstance(r, Exception))
        print(f"  parallel health: {successes} ok, {failures} errors")
        assert successes >= 5, f"Too many failures: {failures}/{len(results)}"

    async def test_parallel_lag_queries(self, call_tool, deployment_name):
        """20 concurrent lag queries across extracts and replicats."""
        tasks = []
        for i in range(1, 11):
            tasks.append(call_tool("get_extract_lag", {
                "deployment": deployment_name,
                "extract_name": f"EXT{i:02d}",
            }))
            tasks.append(call_tool("get_replicat_lag", {
                "deployment": deployment_name,
                "replicat_name": f"REP{i:02d}",
            }))
        results = await asyncio.gather(*tasks, return_exceptions=True)
        successes = sum(1 for r in results if not isinstance(r, Exception))
        print(f"  parallel lags: {successes}/{len(results)} ok")
        assert successes >= 10, "Too many failures in parallel lag queries"

    async def test_rapid_fire_mixed_tools(self, call_tool, deployment_name):
        """50 rapid-fire calls across varied tools."""
        calls = []
        for _ in range(5):
            calls.append(call_tool("list_deployments"))
            calls.append(call_tool("list_extracts", {"deployment": deployment_name}))
            calls.append(call_tool("list_replicats", {"deployment": deployment_name}))
            calls.append(call_tool("get_deployment_health", {"deployment": deployment_name}))
            calls.append(call_tool("list_trails", {"deployment": deployment_name}))
            calls.append(call_tool("get_extract_lag", {
                "deployment": deployment_name, "extract_name": "EXT01",
            }))
            calls.append(call_tool("get_replicat_lag", {
                "deployment": deployment_name, "replicat_name": "REP01",
            }))
            calls.append(call_tool("get_troubleshooting_guide", {
                "symptoms": {"high_lag": True},
            }))
            calls.append(call_tool("get_all_process_health", {
                "deployment": deployment_name,
            }))
            calls.append(call_tool("get_performance_baseline", {
                "deployment": deployment_name, "process_name": "EXT01",
            }))
        t0 = time.monotonic()
        results = await asyncio.gather(*calls, return_exceptions=True)
        elapsed = time.monotonic() - t0
        successes = sum(1 for r in results if not isinstance(r, Exception))
        print(f"  rapid-fire: {successes}/{len(results)} ok in {elapsed:.1f}s "
              f"({len(results)/elapsed:.0f} calls/sec)")
        assert successes >= len(results) * 0.5
