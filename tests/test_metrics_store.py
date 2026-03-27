"""MetricsStore tests."""

from datetime import datetime, timedelta
from pathlib import Path

import pytest

from goldengate_mcp_server.metrics_store import MetricsStore


@pytest.fixture
def metrics_store(tmp_path):
    """Isolated file DB per test (WAL off for Windows-friendly teardown)."""
    return MetricsStore(db_path=str(tmp_path / "metrics.db"), use_wal=False)


class TestMetricsStore:
    """Test suite for MetricsStore."""

    def test_initialization(self, metrics_store):
        """Test that MetricsStore initializes correctly."""
        assert metrics_store is not None
        assert Path(metrics_store.db_path).exists()

    def test_record_lag_metric(self, metrics_store):
        """Test recording a lag metric."""
        lag_data = {
            "lag": "00:00:05",
            "lag_at_chkpt": "00:00:03",
            "time_since_chkpt": "00:00:10",
            "status": "running"
        }

        metrics_store.record_lag_metric(
            deployment="test_deployment",
            process_name="TEST_EXT",
            process_type="extract",
            lag_data=lag_data
        )

        # Verify data was recorded
        history = metrics_store.get_lag_history(
            deployment="test_deployment",
            process_name="TEST_EXT",
            hours=1
        )

        assert len(history) == 1
        assert history[0]["deployment"] == "test_deployment"
        assert history[0]["process_name"] == "TEST_EXT"
        assert history[0]["lag_seconds"] == 5.0

    def test_record_process_statistics(self, metrics_store):
        """Test recording process statistics."""
        stats_data = {
            "inserts": 1000,
            "updates": 500,
            "deletes": 100,
            "discards": 10,
            "operations_per_sec": 25.5
        }

        metrics_store.record_process_statistics(
            deployment="test_deployment",
            process_name="TEST_REP",
            process_type="replicat",
            stats_data=stats_data
        )

        import sqlite3

        with sqlite3.connect(
            str(metrics_store.db_path), **metrics_store._connect_kwargs
        ) as conn:
            cursor = conn.execute(
                "SELECT * FROM process_statistics WHERE process_name = ?",
                ("TEST_REP",)
            )
            row = cursor.fetchone()
            assert row is not None

    def test_calculate_baseline(self, metrics_store):
        """Test baseline calculation."""
        # Record multiple data points
        for i in range(10):
            lag_data = {
                "lag": f"00:00:{i:02d}",
                "status": "running"
            }
            timestamp = datetime.utcnow() - timedelta(hours=i)
            metrics_store.record_lag_metric(
                deployment="test_deployment",
                process_name="TEST_EXT",
                process_type="extract",
                lag_data=lag_data,
                timestamp=timestamp
            )

        baseline = metrics_store.calculate_baseline(
            deployment="test_deployment",
            process_name="TEST_EXT",
            days=1
        )

        assert baseline["data_points"] == 10
        assert "mean" in baseline
        assert "median" in baseline
        assert "std_dev" in baseline
        assert baseline["mean"] >= 0

    def test_get_hourly_pattern(self, metrics_store):
        """Test hourly pattern analysis."""
        # Record data points across different hours
        for hour in range(24):
            for i in range(3):
                lag_data = {
                    "lag": f"00:00:{(hour % 10):02d}",
                    "status": "running"
                }
                timestamp = datetime.utcnow().replace(hour=hour, minute=i*20)
                metrics_store.record_lag_metric(
                    deployment="test_deployment",
                    process_name="TEST_EXT",
                    process_type="extract",
                    lag_data=lag_data,
                    timestamp=timestamp
                )

        pattern = metrics_store.get_hourly_pattern(
            deployment="test_deployment",
            process_name="TEST_EXT",
            days=1
        )

        assert len(pattern) > 0
        # Check that we have statistics for at least some hours
        for _hour, stats in pattern.items():
            assert "mean" in stats
            assert "median" in stats
            assert "max" in stats

    def test_cleanup_old_data(self, metrics_store):
        """Test cleanup of old data."""
        # Record old data
        old_timestamp = datetime.utcnow() - timedelta(days=35)
        lag_data = {"lag": "00:00:05", "status": "running"}

        metrics_store.record_lag_metric(
            deployment="test_deployment",
            process_name="OLD_EXT",
            process_type="extract",
            lag_data=lag_data,
            timestamp=old_timestamp
        )

        # Record recent data
        metrics_store.record_lag_metric(
            deployment="test_deployment",
            process_name="NEW_EXT",
            process_type="extract",
            lag_data=lag_data
        )

        # Cleanup with 30-day retention
        metrics_store.cleanup_old_data(retention_days=30)

        # Old data should be gone
        old_history = metrics_store.get_lag_history(
            deployment="test_deployment",
            process_name="OLD_EXT",
            hours=24*40  # 40 days
        )
        assert len(old_history) == 0

        # Recent data should remain
        new_history = metrics_store.get_lag_history(
            deployment="test_deployment",
            process_name="NEW_EXT",
            hours=24
        )
        assert len(new_history) == 1

    def test_parse_lag_to_seconds(self):
        """Test lag parsing utility."""
        assert MetricsStore._parse_lag_to_seconds("00:00:05") == 5.0
        assert MetricsStore._parse_lag_to_seconds("00:01:30") == 90.0
        assert MetricsStore._parse_lag_to_seconds("01:00:00") == 3600.0
        assert MetricsStore._parse_lag_to_seconds("N/A") is None
        assert MetricsStore._parse_lag_to_seconds(None) is None

    def test_record_health_snapshot(self, metrics_store):
        """Test recording health snapshots."""
        health_data = {
            "deployment": "test_deployment",
            "extracts": [
                {"name": "EXT1", "status": "running", "lag": {"lag": "00:00:05"}},
                {"name": "EXT2", "status": "stopped"}
            ],
            "replicats": [
                {"name": "REP1", "status": "running", "lag": {"lag": "00:00:10"}}
            ],
            "issues": ["EXT2 is stopped"]
        }

        metrics_store.record_health_snapshot(
            deployment="test_deployment",
            health_data=health_data
        )

        # Verify by getting history
        history = metrics_store.get_health_history(
            deployment="test_deployment",
            hours=1
        )

        assert len(history) == 1
        assert history[0]["total_processes"] == 3
        assert history[0]["running_processes"] == 2
        assert history[0]["stopped_processes"] == 1


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
