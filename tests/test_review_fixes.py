"""Regression tests for cache invalidation, REST auth, batch concurrency, tool
visibility/annotations, deployment paths, blocking DB calls and audit rotation."""

import asyncio
import logging
import sys
import threading
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from goldengate_mcp_server.audit import AuditLogger
from goldengate_mcp_server.config import Config, DeploymentConfig
from goldengate_mcp_server.database_monitor import DatabaseMonitor
from goldengate_mcp_server.goldengate_client import GoldenGateClient
from goldengate_mcp_server.tools import WRITE_TOOL_NAMES, all_tools


def _response(payload):
    resp = MagicMock()
    resp.status_code = 200
    resp.text = "x"
    resp.json = MagicMock(return_value=payload)
    return resp


@pytest.fixture
def client():
    return GoldenGateClient(
        base_url="https://gg.example:9000",
        username="u",
        password="p",
        cache_ttl=30,
        deployment_name="Source",
    )


# ---- 1. Cache invalidation --------------------------------------------------

@pytest.mark.asyncio
async def test_start_extract_invalidates_cached_status(client):
    client.client.request = AsyncMock(side_effect=[
        _response({"status": "stopped"}),
        _response({}),
        _response({"status": "running"}),
    ])

    assert (await client.get_extract_status("EXT1"))["status"] == "stopped"
    await client.start_extract("EXT1")
    assert (await client.get_extract_status("EXT1"))["status"] == "running"
    assert client.client.request.await_count == 3


@pytest.mark.asyncio
async def test_failed_write_still_invalidates_cache(client):
    client.cache.set("x", {"stale": True})
    client.client.request = AsyncMock(side_effect=RuntimeError("boom"))
    with pytest.raises(Exception, match="boom"):
        await client.stop_extract("EXT1")
    assert client.cache.get("x") is None


# ---- 3. Batch runs concurrently ---------------------------------------------

@pytest.mark.asyncio
async def test_batch_start_runs_in_parallel(client):
    in_flight = 0
    peak = 0

    async def fake_start(name):
        nonlocal in_flight, peak
        in_flight += 1
        peak = max(peak, in_flight)
        await asyncio.sleep(0.05)
        in_flight -= 1
        if name == "BAD":
            raise RuntimeError("nope")
        return {"ok": name}

    client.start_extract = fake_start
    out = await client.batch_start_processes(["A", "B", "BAD"], "extract")

    assert peak == 3
    assert out["successful"] == 2
    assert out["failed"] == 1
    assert out["results"]["BAD"]["success"] is False
    assert list(out["results"]) == ["A", "B", "BAD"]


# ---- 5. Tool visibility and annotations -------------------------------------

def test_read_only_hides_write_tools():
    names = {t.name for t in all_tools(read_only=True)}
    assert names.isdisjoint(WRITE_TOOL_NAMES)
    assert "list_extracts" in names


def test_write_tools_listed_when_writable():
    names = {t.name for t in all_tools(read_only=False)}
    assert WRITE_TOOL_NAMES <= names


def test_tool_annotations():
    tools = {t.name: t for t in all_tools(read_only=False)}
    assert tools["list_extracts"].annotations.readOnlyHint is True
    assert tools["start_extract"].annotations.readOnlyHint is False
    assert tools["start_extract"].annotations.destructiveHint is False
    assert tools["stop_replicat"].annotations.destructiveHint is True
    assert tools["batch_stop_processes"].annotations.destructiveHint is True


# ---- 6. Deployment paths ----------------------------------------------------

def test_admin_path_uses_deployment_name(client):
    assert client._admin_path("extracts/E1") == "/services/Source/adminsrvr/v2/extracts/E1"


def test_admin_path_requires_deployment_name():
    c = GoldenGateClient(base_url="https://x", username="u", password="p")
    with pytest.raises(ValueError, match="deployment_name"):
        c._admin_path("extracts")


def test_gg_deployment_alias_from_json(monkeypatch):
    monkeypatch.setenv(
        "GG_DEPLOYMENTS",
        '[{"name": "prod", "gg_deployment": "Source", "base_url": "https://x",'
        ' "username": "u", "password": "p"}]',
    )
    cfg = Config.from_env()
    assert cfg.deployments[0].name == "prod"
    assert cfg.deployments[0].service_name == "Source"


def test_gg_deployment_rejected_with_multiple_names(monkeypatch):
    monkeypatch.delenv("GG_DEPLOYMENTS", raising=False)
    monkeypatch.setenv("GG_DEPLOYMENT_1_NAMES", "a,b")
    monkeypatch.setenv("GG_DEPLOYMENT_1_URL", "https://x")
    monkeypatch.setenv("GG_DEPLOYMENT_1_USERNAME", "u")
    monkeypatch.setenv("GG_DEPLOYMENT_1_PASSWORD", "p")
    monkeypatch.setenv("GG_DEPLOYMENT_1_GG_DEPLOYMENT", "Source")
    with pytest.raises(ValueError, match="GG_DEPLOYMENT"):
        Config.from_env()


def test_service_name_defaults_to_name():
    dep = DeploymentConfig(name="Source", base_url="https://x", username="u", password="p")
    assert dep.service_name == "Source"


# ---- 4. Oracle queries run off the event loop -------------------------------

@pytest.mark.asyncio
async def test_oracle_health_runs_in_worker_thread():
    loop_thread = threading.get_ident()
    seen = {}

    cursor = MagicMock()
    cursor.__iter__ = lambda self: iter([])
    cursor.fetchone.return_value = None

    def execute(*_a, **_k):
        seen["execute"] = threading.get_ident()

    cursor.execute.side_effect = execute
    conn = MagicMock()
    conn.cursor.return_value = cursor
    ora = MagicMock()
    ora.connect.return_value = conn

    mon = DatabaseMonitor({"db": {"type": "oracle", "username": "u", "password": "p", "dsn": "d"}})
    mon.oracle_available = True
    with patch.dict(sys.modules, {"oracledb": ora}):
        result = await mon.check_source_database_health("db")

    assert result["status"] == "healthy"
    assert seen["execute"] != loop_thread


# ---- 7. Audit log rotation --------------------------------------------------

@pytest.fixture
def fresh_audit_logger():
    audit = logging.getLogger("audit")
    saved = audit.handlers[:]
    audit.handlers.clear()
    yield
    for h in audit.handlers:
        h.close()
    audit.handlers[:] = saved


def test_audit_log_rotates(tmp_path, fresh_audit_logger):
    path = tmp_path / "audit.log"
    audit = AuditLogger(str(path), max_bytes=500, backup_count=2)
    for i in range(50):
        audit.log_action(action=f"list_extracts_{i}", arguments={"deployment": "d"})

    assert path.exists()
    assert (tmp_path / "audit.log.1").exists()
    assert not (tmp_path / "audit.log.3").exists()
    assert path.stat().st_size <= 500


# ---- 2. REST wrapper auth and tool listing ----------------------------------

fastapi = pytest.importorskip("fastapi")


@pytest.fixture
def rest(monkeypatch):
    from fastapi.testclient import TestClient

    from goldengate_mcp_server import rest_api

    fake = MagicMock()
    fake.list_tool_definitions.return_value = all_tools(enable_metrics=False, read_only=True)
    fake._tool_handlers = {"list_extracts": AsyncMock(return_value={"items": []})}
    monkeypatch.setattr(rest_api, "mcp_server", fake)
    # No context manager: skip the startup hook that builds a real server.
    return TestClient(rest_api.app)


def test_rest_requires_token_when_configured(rest, monkeypatch):
    monkeypatch.setenv("GG_REST_API_TOKEN", "s3cret")
    assert rest.get("/api/tools").status_code == 401
    assert rest.get("/api/tools", headers={"Authorization": "Bearer nope"}).status_code == 401
    ok = rest.post(
        "/api/tools/execute",
        json={"name": "list_extracts", "arguments": {"deployment": "d"}},
        headers={"Authorization": "Bearer s3cret"},
    )
    assert ok.status_code == 200
    assert rest.get("/api/health").status_code == 200


def test_rest_tools_follow_server_filtering(rest, monkeypatch):
    monkeypatch.delenv("GG_REST_API_TOKEN", raising=False)
    names = {t["name"] for t in rest.get("/api/tools").json()["tools"]}
    assert names.isdisjoint(WRITE_TOOL_NAMES)
    assert "diagnose_lag_issue" not in names


def test_rest_refuses_public_bind_without_token(monkeypatch):
    from goldengate_mcp_server import rest_api

    monkeypatch.delenv("GG_REST_API_TOKEN", raising=False)
    monkeypatch.setattr(sys, "argv", ["goldengate-rest-server", "--host", "0.0.0.0"])
    with patch.object(rest_api.uvicorn, "run") as run, pytest.raises(SystemExit):
        rest_api.main()
    run.assert_not_called()


@pytest.mark.parametrize(
    ("argv", "token"),
    [
        (["x"], None),
        (["x", "--host", "0.0.0.0"], "tok"),
        (["x", "--host", "0.0.0.0", "--allow-unauthenticated"], None),
    ],
)
def test_rest_allowed_binds(monkeypatch, argv, token):
    from goldengate_mcp_server import rest_api

    if token:
        monkeypatch.setenv("GG_REST_API_TOKEN", token)
    else:
        monkeypatch.delenv("GG_REST_API_TOKEN", raising=False)
    monkeypatch.setattr(sys, "argv", argv)
    with patch.object(rest_api.uvicorn, "run") as run:
        rest_api.main()
    assert run.call_args.kwargs["host"] == ("127.0.0.1" if argv == ["x"] else "0.0.0.0")
