"""Background metrics collection round (publish roadmap Phase 4)."""

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from goldengate_mcp_server.config import Config, DeploymentConfig
from goldengate_mcp_server.metrics_store import MetricsStore
from goldengate_mcp_server.server import GoldenGateMCPServer


@pytest.mark.asyncio
async def test_metrics_collection_round_records_lag(tmp_path, monkeypatch):
    db = str(tmp_path / "bg.db")
    store = MetricsStore(db_path=db, use_wal=False)

    mock_client = MagicMock()
    mock_client.list_extracts = AsyncMock(
        return_value={"items": [{"name": "EXT1", "status": "running"}]}
    )
    mock_client.get_extract_lag = AsyncMock(
        return_value={"lag": "00:00:07", "status": "running"}
    )
    mock_client.list_replicats = AsyncMock(return_value={"items": []})
    mock_client.close = AsyncMock()

    cfg = Config(
        read_only=True,
        enable_metrics=True,
        request_timeout=5,
        audit_log_path=str(tmp_path / "audit.log"),
        deployments=[
            DeploymentConfig(
                name="dep1",
                base_url="https://gg.example:9000",
                username="u",
                password="p",
            )
        ],
    )

    with patch(
        "goldengate_mcp_server.server.MetricsStore",
        return_value=store,
    ):
        with patch(
            "goldengate_mcp_server.server.GoldenGateClient",
            return_value=mock_client,
        ):
            server = GoldenGateMCPServer(cfg)

    if server._metrics_task:
        server._metrics_task.cancel()
        try:
            await server._metrics_task
        except asyncio.CancelledError:
            pass

    await server._metrics_collection_round()

    hist = store.get_lag_history("dep1", "EXT1", hours=24)
    assert len(hist) >= 1
    assert hist[0]["lag_seconds"] == 7.0

    await server.shutdown()
