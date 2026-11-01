"""Governance, fleet and config-drift tests against a fake GoldenGate REST API.

The fake answers in the documented shape: `api:standardResponse` envelopes, collection items
with names and links only, and parameter files as the `config` lines of a process.
"""

import asyncio
import inspect
import json
import logging
import sys

import httpx
import pytest

from goldengate_mcp_server import server as server_module
from goldengate_mcp_server.audit import verify_audit_log
from goldengate_mcp_server.config import Config, DeploymentConfig, env_bool
from goldengate_mcp_server.goldengate_client import GoldenGateClient
from goldengate_mcp_server.policy import Principal, Role, current_principal
from goldengate_mcp_server.redaction import redact_parameter_file_text, redact_sensitive_data
from goldengate_mcp_server.server import GoldenGateMCPServer
from goldengate_mcp_server.telemetry import JsonFormatter, Telemetry
from goldengate_mcp_server.tools import (
    ADMIN_TOOLS,
    LOCAL_WRITE_TOOLS,
    WRITE_TOOL_NAMES,
    all_tools,
)
from goldengate_mcp_server.utils import parse_lag_duration

PASSWORD_LINE = "USERID gg PASSWORD hunter2"


class FakeGG:
    """Just enough of one deployment's Administration Server."""

    def __init__(self, deployment="Source"):
        self.prefix = f"/services/{deployment}/adminsrvr/v2/"
        self.processes = {
            "extracts/EXT1": {"status": "running", "lag": 5, "config": ["EXTRACT EXT1"]},
            "replicats/REP1": {
                "status": "abended",
                "config": ["REPLICAT REP1", PASSWORD_LINE, "MAP a.*, TARGET b.*;"],
            },
        }
        self.writes = []
        self.ignore_stop = False

    @staticmethod
    def _reply(body):
        return httpx.Response(200, json={"$schema": "api:standardResponse", "response": body})

    def __call__(self, request):
        path = request.url.path
        if path == "/services/v2/deployments":
            return self._reply({"items": []})
        assert path.startswith(self.prefix)
        path = path[len(self.prefix):]

        if request.method == "PATCH":
            wanted = json.loads(request.content)["status"]
            self.writes.append((path, wanted))
            if not (self.ignore_stop and wanted == "stopped"):
                self.processes[path]["status"] = wanted
            return self._reply({})

        if path in ("extracts", "replicats"):
            names = [k.split("/")[1] for k in self.processes if k.startswith(path)]
            return self._reply({
                "$schema": "ogg:collection",
                "items": [{"$schema": "ogg:collectionItem", "name": n, "links": []} for n in names],
            })
        if path in self.processes:
            return self._reply(self.processes[path])
        if path.endswith("info/reports/REP1.rpt"):
            return self._reply({"lines": [
                "banner",
                PASSWORD_LINE,
                "2024-04-03 22:34:36  ERROR   OGG-01296  Error mapping from A to B.",
                "2024-04-03 22:34:37  WARNING OGG-30426  Key columns missing.",
            ]})
        if path.endswith("info/checkpoints"):
            return self._reply({"current": {"sequence": 7}})
        if path == "messages":
            return self._reply({"lines": ["one", "two", "three"]})
        return httpx.Response(404, json={"error": "not found"})


@pytest.fixture
def fresh_audit(tmp_path):
    audit = logging.getLogger("audit")
    saved = audit.handlers[:]
    audit.handlers.clear()
    yield tmp_path / "audit.log"
    for handler in audit.handlers:
        handler.close()
    audit.handlers[:] = saved


def make_server(tmp_path, audit_path, fakes=None, **settings):
    fakes = fakes or {"prod": FakeGG(), "test": FakeGG()}
    settings.setdefault("read_only", False)
    config = Config(
        audit_log_path=str(audit_path),
        backup_dir=str(tmp_path / "backups"),
        cache_ttl_seconds=0,
        requests_per_second=100,
        deployments=[
            DeploymentConfig(
                name=name,
                base_url="https://gg.example",
                username="u",
                password="p",
                gg_deployment="Source",
            )
            for name in fakes
        ],
        **settings,
    )
    server = GoldenGateMCPServer(config)
    for name, fake in fakes.items():
        client = server.clients[name]
        client.client = httpx.AsyncClient(transport=httpx.MockTransport(fake))
    server.fakes = fakes
    return server


@pytest.fixture
def server(tmp_path, fresh_audit):
    return make_server(tmp_path, fresh_audit)


VIEWER = Principal("vera", Role.VIEWER)
OPERATOR = Principal("omar", Role.OPERATOR)


# ---- Config fails closed ----------------------------------------------------

@pytest.mark.parametrize("value", ["1", "yes", "TRUE", "on"])
def test_truthy_read_only_stays_read_only(monkeypatch, value):
    monkeypatch.setenv("GG_READ_ONLY", value)
    assert env_bool("GG_READ_ONLY", True) is True


def test_misspelled_boolean_is_an_error(monkeypatch):
    monkeypatch.setenv("GG_READ_ONLY", "ture")
    with pytest.raises(ValueError, match="GG_READ_ONLY"):
        env_bool("GG_READ_ONLY", True)


def test_password_file_and_repr(monkeypatch, tmp_path):
    secret = tmp_path / "pw"
    secret.write_text("s3cret\n")
    monkeypatch.delenv("GG_DEPLOYMENTS", raising=False)
    monkeypatch.setenv("GG_DEPLOYMENT_1_NAME", "a")
    monkeypatch.setenv("GG_DEPLOYMENT_1_URL", "https://x")
    monkeypatch.setenv("GG_DEPLOYMENT_1_USERNAME", "u")
    monkeypatch.setenv("GG_DEPLOYMENT_1_PASSWORD_FILE", str(secret))
    dep = Config.from_env().deployments[0]
    assert dep.password == "s3cret"
    assert "s3cret" not in repr(dep)


def _dep(name):
    return DeploymentConfig(name=name, base_url="https://x", username="u", password="p")


def test_duplicate_deployment_names_rejected():
    with pytest.raises(ValueError, match="Duplicate"):
        Config(deployments=[_dep("a"), _dep("a")])


def test_typo_in_protected_deployments_rejected():
    with pytest.raises(ValueError, match="protected_deployments"):
        Config(deployments=[_dep("prod")], protected_deployments=["prdo"])


# ---- Redaction --------------------------------------------------------------

@pytest.mark.parametrize(
    "line",
    [
        "SOURCEDB db USERID u PASSWORD hunter2",
        "TARGETDB db, USERID u, PASSWORD hunter2",
        "-- USERID u, PASSWORD hunter2",
    ],
)
def test_password_lines_redacted(line):
    assert "hunter2" not in redact_parameter_file_text(line)


def test_config_lines_redacted_in_process_detail():
    out = redact_sensitive_data({"response": {"config": ["EXTRACT E", PASSWORD_LINE]}})
    assert "hunter2" not in json.dumps(out)
    assert out["response"]["config"][0] == "EXTRACT E"


# ---- Client against documented response shapes ------------------------------

def test_name_with_trailing_newline_rejected():
    with pytest.raises(ValueError, match="Invalid process name"):
        GoldenGateClient._sanitize_name("EXT1\n")


def test_numeric_lag_parsed():
    assert parse_lag_duration(12) == 12.0
    assert parse_lag_duration("00:00:05") == 5.0


async def test_health_sees_enveloped_processes_without_list_status(server):
    health = await server._get_deployment_health("prod")
    assert [e["status"] for e in health["extracts"]] == ["running"]
    assert health["overall_status"] == "degraded"
    assert health["issues"] == ["Replicat REP1 is abended"]


async def test_replicat_error_does_not_mask_extract_listing_failure(server):
    async def broken():
        raise RuntimeError("down")

    server.clients["prod"].list_extracts = broken
    health = await server._get_deployment_health("prod")
    assert health["overall_status"] == "error"


async def test_cached_reply_is_not_mutated_by_callers():
    fake = FakeGG()
    client = GoldenGateClient(
        "https://gg.example", "u", "p", deployment_name="Source", cache_ttl=30
    )
    client.client = httpx.AsyncClient(transport=httpx.MockTransport(fake))
    first = await client.get_extract_config("EXT1")
    first["response"]["status"] = "tampered"
    assert (await client.get_extract_status("EXT1"))["response"]["status"] == "running"


# ---- Gateway: policy, dry run, audit ----------------------------------------

def test_every_mutating_tool_is_classified():
    tools = all_tools(enable_metrics=True, read_only=False)
    mutating = {t.name for t in tools if not t.annotations.readOnlyHint}
    assert mutating == WRITE_TOOL_NAMES | LOCAL_WRITE_TOOLS


def test_every_scoped_tool_uses_the_deployment_argument():
    for tool in all_tools(enable_metrics=True, read_only=False):
        properties = tool.inputSchema["properties"]
        unscoped = {
            "list_deployments",
            "get_fleet_health",
            "get_troubleshooting_guide",
        } | ADMIN_TOOLS
        assert "deployment" in properties or tool.name in unscoped, tool.name


async def test_read_only_write_is_denied_and_recorded(tmp_path, fresh_audit):
    server = make_server(tmp_path, fresh_audit, read_only=True)
    outcome = await server.execute_tool(
        "stop_extract", {"deployment": "prod", "extract_name": "EXT1"}
    )
    assert outcome.status == "denied"
    assert outcome.result["code"] == "read_only"
    assert server.fakes["prod"].writes == []
    events = server.audit_logger.search_logs(event_type="security")
    assert events[0]["details"]["code"] == "read_only"


async def test_write_allowlist(tmp_path, fresh_audit):
    server = make_server(tmp_path, fresh_audit, write_deployments=["test"])
    args = {"extract_name": "EXT1"}
    denied = await server.execute_tool("stop_extract", {"deployment": "prod", **args})
    allowed = await server.execute_tool("stop_extract", {"deployment": "test", **args})
    assert denied.result["code"] == "deployment_not_writable"
    assert allowed.ok


@pytest.mark.parametrize(
    ("ticket", "code"),
    [(None, "change_ticket_required"), ("please", "change_ticket_invalid")],
)
async def test_protected_deployment_needs_a_valid_ticket(tmp_path, fresh_audit, ticket, code):
    server = make_server(tmp_path, fresh_audit, protected_deployments=["prod"])
    args = {"deployment": "prod", "extract_name": "EXT1"}
    if ticket:
        args["change_ticket"] = ticket
    outcome = await server.execute_tool("stop_extract", args)
    assert outcome.result["code"] == code
    assert server.fakes["prod"].writes == []


@pytest.mark.parametrize("ticket", ["OPS-123", "CHG0012345"])
async def test_ticket_is_accepted_and_audited(tmp_path, fresh_audit, ticket):
    server = make_server(tmp_path, fresh_audit, protected_deployments=["prod"])
    outcome = await server.execute_tool(
        "stop_extract",
        {"deployment": "prod", "extract_name": "EXT1", "change_ticket": ticket},
    )
    assert outcome.ok
    action = server.audit_logger.search_logs(event_type="action", action="stop_extract")[-1]
    assert action["arguments"]["change_ticket"] == ticket


async def test_dry_run_changes_nothing(tmp_path, fresh_audit):
    server = make_server(tmp_path, fresh_audit, protected_deployments=["prod"])
    outcome = await server.execute_tool(
        "batch_stop_processes",
        {
            "deployment": "prod",
            "process_names": ["EXT1"],
            "process_type": "extract",
            "dry_run": True,
        },
    )
    assert outcome.ok
    assert outcome.result["targets"] == [
        {"name": "EXT1", "current_status": "running", "would_change": True}
    ]
    assert outcome.result["change_ticket_required"] is True
    assert server.fakes["prod"].writes == []


async def test_batch_limit(tmp_path, fresh_audit):
    server = make_server(tmp_path, fresh_audit, max_batch_size=1)
    outcome = await server.execute_tool(
        "batch_stop_processes",
        {"deployment": "prod", "process_names": ["A", "B"], "process_type": "extract"},
    )
    assert outcome.result["code"] == "batch_too_large"


async def test_batch_with_failures_is_audited_as_failure(server):
    outcome = await server.execute_tool(
        "batch_start_processes",
        {"deployment": "prod", "process_names": ["bad name"], "process_type": "extract"},
    )
    assert outcome.status == "failed"
    assert server.audit_logger.search_logs(event_type="result")[-1]["success"] is False


async def test_viewer_cannot_write_and_does_not_see_write_tools(server):
    outcome = await server.execute_tool(
        "stop_extract", {"deployment": "prod", "extract_name": "EXT1"}, principal=VIEWER
    )
    assert outcome.result["code"] == "insufficient_role"
    names = {t.name for t in server.list_tool_definitions(VIEWER)}
    assert names.isdisjoint(WRITE_TOOL_NAMES | ADMIN_TOOLS)
    assert "list_extracts" in names


async def test_operator_cannot_read_audit_log(server):
    outcome = await server.execute_tool("search_audit_log", {}, principal=OPERATOR)
    assert outcome.result["code"] == "insufficient_role"


async def test_deployment_scope(server):
    scoped = Principal("sam", Role.VIEWER, frozenset({"test"}))
    denied = await server.execute_tool("list_extracts", {"deployment": "prod"}, principal=scoped)
    assert denied.result["code"] == "deployment_forbidden"

    listing = await server.execute_tool("list_deployments", {}, principal=scoped)
    assert [d["name"] for d in listing.result["deployments"]] == ["test"]

    explicit = await server.execute_tool(
        "get_fleet_health", {"deployments": ["prod"]}, principal=scoped
    )
    assert explicit.result["code"] == "deployment_forbidden"


async def test_disabled_tool_is_hidden_and_refused(tmp_path, fresh_audit):
    server = make_server(tmp_path, fresh_audit, disabled_tools=["get_process_config"])
    assert "get_process_config" not in {t.name for t in server.list_tool_definitions()}
    outcome = await server.execute_tool(
        "get_process_config",
        {"deployment": "prod", "process_name": "EXT1", "process_type": "extract"},
    )
    assert outcome.result["code"] == "tool_disabled"


async def test_missing_argument_and_unknown_tool(server):
    assert (await server.execute_tool("list_extracts", {})).status == "invalid"
    assert (await server.execute_tool("nope", {})).status == "unknown_tool"


async def test_exception_is_redacted_audited_and_chain_stays_valid(server, fresh_audit):
    outcome = await server.execute_tool("list_extracts", {"deployment": "ghost"})
    assert outcome.status == "error"
    assert "ghost" in outcome.result
    entries = server.audit_logger.search_logs(deployment="ghost")
    assert [e["event_type"] for e in entries] == ["action", "result"]
    assert {e["correlation_id"] for e in entries} == {outcome.correlation_id}
    assert verify_audit_log(fresh_audit).ok


async def test_audit_names_the_caller(server):
    await server.execute_tool("list_extracts", {"deployment": "prod"}, principal=VIEWER)
    assert server.audit_logger.search_logs(user="vera", event_type="result")[0]["success"]


async def test_principal_is_reset_after_a_call(server):
    await server.execute_tool("list_deployments", {}, principal=VIEWER)
    assert current_principal.get() is None


# ---- Restart ----------------------------------------------------------------

async def test_restart_stops_then_starts(server):
    outcome = await server.execute_tool(
        "restart_process",
        {"deployment": "prod", "process_name": "EXT1", "process_type": "extract"},
    )
    assert outcome.ok
    assert outcome.result["status"] == "running"
    assert [w[1] for w in server.fakes["prod"].writes] == ["stopped", "running"]


async def test_restart_does_not_start_a_process_that_will_not_stop(server):
    fake = server.fakes["prod"]
    fake.ignore_stop = True
    outcome = await server.execute_tool(
        "restart_process",
        {
            "deployment": "prod",
            "process_name": "EXT1",
            "process_type": "extract",
            "timeout_seconds": 0,
        },
    )
    assert outcome.status == "failed"
    assert [w[1] for w in fake.writes] == ["stopped"]


async def test_restart_of_abended_process_skips_stop(server):
    await server.execute_tool(
        "restart_process",
        {"deployment": "prod", "process_name": "REP1", "process_type": "replicat"},
    )
    assert [w[1] for w in server.fakes["prod"].writes] == ["running"]


# ---- Fleet and runtime detail -----------------------------------------------

async def test_fleet_ranks_problems_and_survives_a_dead_deployment(server):
    async def dead():
        raise RuntimeError("connection refused password=hunter2")

    server.clients["test"].list_extracts = dead
    server.clients["test"].list_replicats = dead

    outcome = await server.execute_tool("get_fleet_health", {"lag_threshold_seconds": 1})
    report = outcome.result

    assert report["summary"]["deployments"] == 2
    assert report["summary"]["degraded"] == 1
    assert report["summary"]["error"] == 1
    assert [(a["process"], a["severity"]) for a in report["attention"]] == [
        ("REP1", "critical"),
        ("EXT1", "high"),
    ]
    assert "hunter2" not in json.dumps(report)


async def test_process_report_parses_ogg_messages(server):
    outcome = await server.execute_tool(
        "get_process_report",
        {"deployment": "prod", "process_type": "replicat", "process_name": "REP1"},
    )
    assert [m["code"] for m in outcome.result["ogg_messages"]] == ["OGG-01296", "OGG-30426"]
    assert "hunter2" not in server._format_result(outcome.result)


async def test_report_name_is_validated(server):
    outcome = await server.execute_tool(
        "get_process_report",
        {
            "deployment": "prod",
            "process_type": "replicat",
            "process_name": "REP1",
            "report": "../../secrets",
        },
    )
    assert outcome.status == "error"


async def test_checkpoints_and_messages(server):
    checkpoints = await server.execute_tool(
        "get_process_checkpoints",
        {"deployment": "prod", "process_type": "extract", "process_name": "EXT1"},
    )
    assert checkpoints.result["current"]["sequence"] == 7
    messages = await server.execute_tool(
        "get_deployment_messages", {"deployment": "prod", "limit": 2}
    )
    assert messages.result == {"deployment": "prod", "total": 3, "messages": ["two", "three"]}


# ---- Config snapshots and drift ---------------------------------------------

async def test_snapshot_round_trip_detects_drift_without_leaking(server, tmp_path):
    saved = await server.execute_tool("save_config_snapshot", {"deployment": "prod"})
    assert saved.result["replicats"] == 1
    written = (tmp_path / "backups" / saved.result["backup_file"]).read_text()
    assert "hunter2" not in written

    clean = await server.execute_tool(
        "compare_deployment_configs",
        {"deployment": "prod", "backup_file": saved.result["backup_file"]},
    )
    assert clean.result["identical"] is True

    server.fakes["prod"].processes["replicats/REP1"]["config"][2] = "MAP a.*, TARGET c.*;"
    drifted = await server.execute_tool(
        "compare_deployment_configs",
        {"deployment": "prod", "backup_file": saved.result["backup_file"]},
    )
    assert drifted.result["identical"] is False
    assert drifted.result["summary"]["modified_processes"] == ["Replicat: REP1"]
    diff = drifted.result["differences"][0]["diff"]
    assert "+MAP a.*, TARGET c.*;" in diff

    listing = await server.execute_tool("list_config_backups", {"deployment": "prod"})
    assert [b["backup_file"] for b in listing.result["backups"]] == [saved.result["backup_file"]]


@pytest.mark.parametrize("backup_file", ["../outside.json", "/etc/passwd"])
async def test_compare_cannot_read_outside_backup_dir(server, tmp_path, backup_file):
    (tmp_path / "outside.json").write_text("{}")
    (tmp_path / "backups").mkdir()
    outcome = await server.execute_tool(
        "compare_deployment_configs", {"deployment": "prod", "backup_file": backup_file}
    )
    assert "must be inside" in outcome.result["error"]


async def test_compare_refuses_another_deployments_snapshot(server):
    saved = await server.execute_tool("save_config_snapshot", {"deployment": "test"})
    outcome = await server.execute_tool(
        "compare_deployment_configs",
        {"deployment": "prod", "backup_file": saved.result["backup_file"]},
    )
    assert "snapshot of test" in outcome.result["error"]


async def test_snapshot_available_in_read_only_mode(tmp_path, fresh_audit):
    server = make_server(tmp_path, fresh_audit, read_only=True)
    assert "save_config_snapshot" in {t.name for t in server.list_tool_definitions()}
    assert (await server.execute_tool("save_config_snapshot", {"deployment": "prod"})).ok


# ---- Audit tools and posture ------------------------------------------------

async def test_verify_tool_reports_the_chain(server):
    await server.execute_tool("list_deployments", {})
    outcome = await server.execute_tool("verify_audit_log", {})
    assert outcome.result["ok"] is True
    assert outcome.result["entries"] >= 3


async def test_search_includes_rotated_files(tmp_path, fresh_audit):
    logging.getLogger("audit").handlers.clear()
    server = make_server(tmp_path, fresh_audit)
    for handler in logging.getLogger("audit").handlers:
        handler.maxBytes = 400
    for _ in range(10):
        await server.execute_tool("list_deployments", {})
    assert (tmp_path / "audit.log.1").exists()
    # Each file holds one entry here, so anything past the first came from a rotated file.
    assert len(server.audit_logger.search_logs(event_type="action")) > 1


async def test_posture_findings(tmp_path, fresh_audit):
    server = make_server(tmp_path, fresh_audit, protected_deployments=["prod"])
    server.config.deployments[1].verify_ssl = False
    posture = (await server.execute_tool("get_server_posture", {})).result
    assert posture["deployments"][0] == {
        "name": "prod",
        "scheme": "https",
        "tls_verified": True,
        "writable": True,
        "protected": True,
    }
    assert "test: TLS certificate verification is off" in posture["findings"]
    assert "test: writable without a change ticket requirement" in posture["findings"]


def test_audit_cli_exit_codes(tmp_path, fresh_audit, monkeypatch, server):
    from goldengate_mcp_server import audit as audit_module

    server.audit_logger.log_action(action="x", arguments={})

    monkeypatch.setattr(sys, "argv", ["verify", str(fresh_audit)])
    with pytest.raises(SystemExit) as ok:
        audit_module.main()
    assert ok.value.code == 0

    monkeypatch.setattr(sys, "argv", ["verify", str(fresh_audit), "--contains", "f" * 64])
    with pytest.raises(SystemExit) as missing:
        audit_module.main()
    assert missing.value.code == 1


# ---- Telemetry --------------------------------------------------------------

def test_prometheus_output():
    telemetry = Telemetry()
    telemetry.record_call("list_extracts", "ok", 0.5)
    telemetry.record_denial("read_only")
    text = telemetry.render(deployments=2, cache_stats=[('a"b', {"hits": 1, "misses": 2})])
    assert 'ggmcp_tool_calls_total{status="ok",tool="list_extracts"} 1' in text
    assert 'ggmcp_tool_duration_seconds_sum{tool="list_extracts"} 0.5' in text
    assert 'ggmcp_policy_denials_total{code="read_only"} 1' in text
    assert "ggmcp_deployments_configured 2" in text
    assert 'ggmcp_cache_hits_total{deployment="a\\"b"} 1' in text


async def test_unknown_tool_names_do_not_become_metric_labels(server):
    await server.execute_tool("attacker_controlled", {})
    assert "attacker_controlled" not in server.render_metrics()


def test_json_logs_are_redacted():
    record = logging.LogRecord(
        "x", logging.INFO, __file__, 1, "login password=hunter2", None, None
    )
    entry = json.loads(JsonFormatter().format(record))
    assert entry["level"] == "INFO"
    assert "hunter2" not in entry["message"]


# ---- Entry point ------------------------------------------------------------

def test_console_script_target_is_synchronous():
    assert not inspect.iscoroutinefunction(server_module.main)
    assert asyncio.iscoroutinefunction(server_module.run_server)


# ---- REST -------------------------------------------------------------------

pytest.importorskip("fastapi")


@pytest.fixture
def rest(server, monkeypatch):
    import hashlib

    from fastapi.testclient import TestClient

    from goldengate_mcp_server import rest_api

    def key(name, token, role, deployments=None):
        entry = {
            "name": name,
            "role": role,
            "token_sha256": hashlib.sha256(token.encode()).hexdigest(),
        }
        return {**entry, "deployments": deployments} if deployments else entry

    monkeypatch.delenv("GG_REST_API_TOKEN", raising=False)
    monkeypatch.setenv("GG_REST_API_KEYS", json.dumps([
        key("vera", "viewer-token", "viewer"),
        key("omar", "operator-token", "operator", ["test"]),
    ]))
    monkeypatch.setattr(rest_api, "mcp_server", server)
    return TestClient(rest_api.app)


def _bearer(token):
    return {"Authorization": f"Bearer {token}"}


def test_rest_rejects_unknown_key(rest):
    assert rest.get("/api/tools", headers=_bearer("nope")).status_code == 401
    assert rest.get("/metrics").status_code == 401


def test_rest_viewer_gets_403_and_filtered_tools(rest):
    tools = rest.get("/api/tools", headers=_bearer("viewer-token")).json()["tools"]
    assert "stop_extract" not in {t["name"] for t in tools}

    denied = rest.post(
        "/api/tools/execute",
        json={"name": "stop_extract", "arguments": {"deployment": "prod", "extract_name": "E"}},
        headers=_bearer("viewer-token"),
    )
    assert denied.status_code == 403
    assert denied.json()["detail"]["code"] == "insufficient_role"
    assert denied.headers["X-Correlation-ID"]


def test_rest_operator_is_scoped_and_named_in_audit(rest, server):
    body = {"name": "start_replicat", "arguments": {"replicat_name": "REP1"}}

    denied = rest.post(
        "/api/tools/execute",
        json={**body, "arguments": {**body["arguments"], "deployment": "prod"}},
        headers=_bearer("operator-token"),
    )
    assert denied.status_code == 403

    allowed = rest.post(
        "/api/tools/execute",
        json={**body, "arguments": {**body["arguments"], "deployment": "test"}},
        headers=_bearer("operator-token"),
    )
    assert allowed.status_code == 200
    payload = allowed.json()
    assert payload["success"] is True
    assert allowed.headers["X-Correlation-ID"] == payload["correlation_id"]

    entry = server.audit_logger.search_logs(user="omar", action="start_replicat")[-1]
    assert entry["channel"] == "rest"
    assert entry["correlation_id"] == payload["correlation_id"]


def test_rest_status_mapping(rest):
    headers = _bearer("viewer-token")
    unknown = rest.post("/api/tools/execute", json={"name": "nope"}, headers=headers)
    invalid = rest.post("/api/tools/execute", json={"name": "list_extracts"}, headers=headers)
    assert unknown.status_code == 404
    assert invalid.status_code == 400


def test_rest_metrics_and_ready(rest):
    assert rest.get("/api/ready").json() == {"status": "ready"}
    rest.post(
        "/api/tools/execute",
        json={"name": "list_extracts", "arguments": {"deployment": "prod"}},
        headers=_bearer("viewer-token"),
    )
    text = rest.get("/metrics", headers=_bearer("viewer-token")).text
    assert 'ggmcp_tool_calls_total{status="ok",tool="list_extracts"} 1' in text


def test_malformed_key_digest_is_rejected():
    from goldengate_mcp_server import rest_api

    with pytest.raises(ValueError, match="SHA-256"):
        rest_api._parse_keys('[{"name": "x", "token_sha256": "plaintext"}]')
