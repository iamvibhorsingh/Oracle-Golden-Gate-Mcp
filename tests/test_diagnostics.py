"""
Tests for DiagnosticsEngine functionality.
"""

from datetime import datetime
from unittest.mock import AsyncMock, MagicMock

import pytest

from goldengate_mcp_server.diagnostics import DiagnosticsEngine, TroubleshootingGuide
from goldengate_mcp_server.metrics_store import MetricsStore


@pytest.fixture
def mock_metrics_store():
    """Create a mock MetricsStore."""
    store = MagicMock(spec=MetricsStore)

    # Mock baseline calculation
    store.calculate_baseline.return_value = {
        "mean": 5.0,
        "median": 4.0,
        "std_dev": 2.0,
        "min": 1.0,
        "max": 10.0,
        "p95": 9.0,
        "p99": 10.0,
        "data_points": 100,
        "time_range_days": 7
    }

    # Mock lag history
    store.get_lag_history.return_value = [
        {"timestamp": datetime.utcnow().isoformat(), "lag_seconds": 5.0}
        for _ in range(10)
    ]

    # Mock hourly pattern
    store.get_hourly_pattern.return_value = {
        hour: {"mean": 5.0, "median": 4.0, "max": 10.0, "sample_count": 10}
        for hour in range(24)
    }

    return store


@pytest.fixture
def mock_clients():
    """Create mock GoldenGate clients."""
    client = AsyncMock()

    # Mock lag data
    client.get_extract_lag.return_value = {
        "lag": "00:00:05",
        "lag_at_chkpt": "00:00:03",
        "time_since_chkpt": "00:00:10",
        "status": "running"
    }

    client.get_replicat_lag.return_value = {
        "lag": "00:00:05",
        "lag_at_chkpt": "00:00:03",
        "time_since_chkpt": "00:00:10",
        "status": "running"
    }

    # Mock status data
    client.get_extract_status.return_value = {
        "name": "TEST_EXT",
        "status": "running",
        "type": "INTEGRATED"
    }

    client.get_replicat_status.return_value = {
        "name": "TEST_REP",
        "status": "running",
        "type": "PARALLEL"
    }

    # Mock statistics
    client.get_process_statistics.return_value = {
        "inserts": 1000,
        "updates": 500,
        "deletes": 100,
        "discards": 10,
        "operations_per_sec": 25.5
    }

    # Mock error check
    client.check_process_errors.return_value = {
        "has_errors": False,
        "messages": []
    }

    return {"test_deployment": client}


@pytest.fixture
def diagnostics_engine(mock_metrics_store, mock_clients):
    """Create a DiagnosticsEngine instance for testing."""
    return DiagnosticsEngine(mock_metrics_store, mock_clients)


class TestDiagnosticsEngine:
    """Test suite for DiagnosticsEngine."""

    @pytest.mark.asyncio
    async def test_diagnose_running_process(self, diagnostics_engine):
        """Test diagnosing a running process with normal lag."""
        diagnosis = await diagnostics_engine.diagnose_lag_issue(
            deployment="test_deployment",
            process_name="TEST_EXT",
            process_type="extract"
        )

        assert diagnosis is not None
        assert diagnosis["deployment"] == "test_deployment"
        assert diagnosis["process"] == "TEST_EXT"
        assert diagnosis["process_type"] == "extract"
        assert "likely_causes" in diagnosis
        assert "recommendations" in diagnosis
        assert "confidence_score" in diagnosis

    @pytest.mark.asyncio
    async def test_diagnose_stopped_process(self, diagnostics_engine, mock_clients):
        """Test diagnosing a stopped process."""
        # Mock stopped status
        mock_clients["test_deployment"].get_extract_lag.return_value = {
            "lag": "00:00:00",
            "status": "stopped"
        }
        mock_clients["test_deployment"].get_extract_status.return_value = {
            "name": "TEST_EXT",
            "status": "stopped"
        }

        diagnosis = await diagnostics_engine.diagnose_lag_issue(
            deployment="test_deployment",
            process_name="TEST_EXT",
            process_type="extract"
        )

        assert diagnosis["severity"] == "critical"
        assert any("stopped" in str(cause).lower() for cause in diagnosis["likely_causes"])

    @pytest.mark.asyncio
    async def test_diagnose_abended_process(self, diagnostics_engine, mock_clients):
        """Test diagnosing an abended process."""
        # Mock abended status
        mock_clients["test_deployment"].get_extract_lag.return_value = {
            "lag": "00:00:00",
            "status": "abended"
        }
        mock_clients["test_deployment"].get_extract_status.return_value = {
            "name": "TEST_EXT",
            "status": "abended",
            "messages": ["ORA-01555: snapshot too old"]
        }

        diagnosis = await diagnostics_engine.diagnose_lag_issue(
            deployment="test_deployment",
            process_name="TEST_EXT",
            process_type="extract"
        )

        assert diagnosis["severity"] == "critical"
        assert any("abended" in str(cause).lower() for cause in diagnosis["likely_causes"])

    @pytest.mark.asyncio
    async def test_diagnose_high_lag(self, diagnostics_engine, mock_clients):
        """Test diagnosing high lag scenario."""
        # Mock high lag
        mock_clients["test_deployment"].get_extract_lag.return_value = {
            "lag": "00:01:00",  # 60 seconds
            "lag_at_chkpt": "00:00:55",
            "time_since_chkpt": "00:00:30",
            "status": "running"
        }

        diagnosis = await diagnostics_engine.diagnose_lag_issue(
            deployment="test_deployment",
            process_name="TEST_EXT",
            process_type="extract"
        )

        assert diagnosis["evidence"]["current_lag_seconds"] == 60.0
        # Should detect high lag vs baseline (baseline mean is 5.0)
        assert len(diagnosis["likely_causes"]) > 0

    @pytest.mark.asyncio
    async def test_diagnose_unknown_deployment(self, diagnostics_engine):
        """Test diagnosing with unknown deployment."""
        diagnosis = await diagnostics_engine.diagnose_lag_issue(
            deployment="unknown_deployment",
            process_name="TEST_EXT",
            process_type="extract"
        )

        assert "error" in diagnosis
        assert "unknown" in diagnosis["error"].lower()

    def test_analyze_lag_vs_baseline(self, diagnostics_engine):
        """Test lag vs baseline analysis."""
        baseline = {
            "mean": 5.0,
            "std_dev": 2.0,
            "p95": 9.0
        }

        assert diagnostics_engine._analyze_lag_vs_baseline(5.0, baseline) is None

        cause = diagnostics_engine._analyze_lag_vs_baseline(20.0, baseline)
        assert cause is not None
        assert "σ" in str(cause["evidence"])

    def test_parse_lag_duration_utils(self):
        """Lag duration parsing lives in utils.parse_lag_duration."""
        from goldengate_mcp_server.utils import parse_lag_duration

        assert parse_lag_duration("00:00:05") == 5.0
        assert parse_lag_duration("00:01:30") == 90.0
        assert parse_lag_duration(None) is None


class TestTroubleshootingGuide:
    """Test suite for TroubleshootingGuide."""

    def test_get_troubleshooting_guide_high_lag(self):
        """Test getting troubleshooting guide for high lag."""
        symptoms = {"high_lag": True}
        guide = TroubleshootingGuide.get_troubleshooting_guide(symptoms)

        assert guide is not None
        assert "steps" in guide
        assert "common_causes" in guide
        assert len(guide["steps"]) > 0
        assert any("lag" in str(step).lower() for step in guide["steps"])

    def test_get_troubleshooting_guide_abended(self):
        """Test getting troubleshooting guide for abended process."""
        symptoms = {"abended": True}
        guide = TroubleshootingGuide.get_troubleshooting_guide(symptoms)

        assert guide is not None
        assert len(guide["steps"]) > 0
        assert any("error" in str(step).lower() or "log" in str(step).lower()
                   for step in guide["steps"])

    def test_get_troubleshooting_guide_multiple_symptoms(self):
        """Test getting troubleshooting guide with multiple symptoms."""
        symptoms = {"high_lag": True, "abended": True}
        guide = TroubleshootingGuide.get_troubleshooting_guide(symptoms)

        assert guide is not None
        # Should have steps for both symptoms
        assert len(guide["steps"]) > 5
        assert len(guide["common_causes"]) > 0

    def test_get_troubleshooting_guide_prevention(self):
        """Test that prevention strategies are included."""
        symptoms = {"high_lag": True}
        guide = TroubleshootingGuide.get_troubleshooting_guide(symptoms)

        assert "prevention" in guide
        assert len(guide["prevention"]) > 0


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
