"""
Tests for DatabaseMonitor functionality.
"""

import sys
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from goldengate_mcp_server.database_monitor import DatabaseMonitor, create_db_config_from_env


@pytest.fixture
def mock_db_config():
    """Create mock database configuration."""
    return {
        "test_db": {
            "type": "oracle",
            "dsn": "localhost:1521/TESTDB",
            "username": "ggadmin",
            "password": "password"
        }
    }


@pytest.fixture
def db_monitor(mock_db_config):
    """Create a DatabaseMonitor instance for testing."""
    return DatabaseMonitor(mock_db_config)


class TestDatabaseMonitor:
    """Test suite for DatabaseMonitor."""

    def test_initialization(self, db_monitor):
        """Test that DatabaseMonitor initializes correctly."""
        assert db_monitor is not None
        assert db_monitor.db_connections is not None

    @pytest.mark.asyncio
    async def test_check_unknown_database(self, db_monitor):
        """Test checking health of unknown database."""
        result = await db_monitor.check_source_database_health("unknown_db")

        assert result["status"] == "unknown"
        assert "error" in result

    @pytest.mark.asyncio
    async def test_check_unsupported_database_type(self):
        """Test checking unsupported database type."""
        config = {
            "test_db": {
                "type": "mysql",
                "dsn": "localhost:3306",
                "username": "user",
                "password": "pass"
            }
        }
        monitor = DatabaseMonitor(config)

        result = await monitor.check_source_database_health("test_db")

        assert result["status"] == "unsupported"
        assert "error" in result

    @pytest.mark.asyncio
    async def test_check_oracle_health_without_library(self, db_monitor):
        """Test Oracle health check when oracledb is not available."""
        # Temporarily disable oracle_available
        db_monitor.oracle_available = False

        result = await db_monitor.check_source_database_health("test_db")

        assert result["status"] == "unavailable"
        assert "oracledb" in result["error"]

    @pytest.mark.asyncio
    async def test_check_oracle_health_success(self, db_monitor):
        """Test successful Oracle health check."""
        mock_connection = MagicMock()
        mock_cursor = MagicMock()

        mock_cursor.fetchall.return_value = []
        mock_cursor.fetchone.side_effect = [
            (5000000.0,),
            ("YES",),
        ]
        mock_cursor.__iter__ = lambda self: iter([
            ("CPU Usage Per Sec", 50.0),
            ("Host CPU Utilization (%)", 60.0),
        ])

        mock_connection.cursor.return_value = mock_cursor
        mock_ora = MagicMock()
        mock_ora.connect = MagicMock(return_value=mock_connection)
        db_monitor.oracle_available = True

        with patch.dict(sys.modules, {"oracledb": mock_ora}):
            with patch("asyncio.to_thread", new=AsyncMock(return_value=mock_connection)):
                result = await db_monitor.check_source_database_health("test_db")

        assert result["database_type"] == "oracle"
        assert "metrics" in result

    @pytest.mark.asyncio
    async def test_check_target_database_health(self, db_monitor):
        """Test checking target database health."""
        # Should reuse source health check logic
        db_monitor.oracle_available = False
        result = await db_monitor.check_target_database_health("test_db")

        # Should get same result as source check
        assert "status" in result


class TestCreateDbConfigFromEnv:
    """Test suite for create_db_config_from_env."""

    def test_create_config_no_env_vars(self):
        """Test creating config with no environment variables."""
        with patch.dict('os.environ', {}, clear=True):
            config = create_db_config_from_env()
            assert config == {}

    def test_create_config_single_database(self):
        """Test creating config with single database."""
        env_vars = {
            "GG_DB_MONITOR_1_NAME": "prod_db",
            "GG_DB_MONITOR_1_TYPE": "oracle",
            "GG_DB_MONITOR_1_DSN": "localhost:1521/PROD",
            "GG_DB_MONITOR_1_USERNAME": "ggadmin",
            "GG_DB_MONITOR_1_PASSWORD": "secret"
        }

        with patch.dict('os.environ', env_vars, clear=True):
            config = create_db_config_from_env()

            assert "prod_db" in config
            assert config["prod_db"]["type"] == "oracle"
            assert config["prod_db"]["dsn"] == "localhost:1521/PROD"
            assert config["prod_db"]["username"] == "ggadmin"
            assert config["prod_db"]["password"] == "secret"

    def test_create_config_multiple_databases(self):
        """Test creating config with multiple databases."""
        env_vars = {
            "GG_DB_MONITOR_1_NAME": "db1",
            "GG_DB_MONITOR_1_TYPE": "oracle",
            "GG_DB_MONITOR_1_DSN": "host1:1521/DB1",
            "GG_DB_MONITOR_1_USERNAME": "user1",
            "GG_DB_MONITOR_1_PASSWORD": "pass1",
            "GG_DB_MONITOR_2_NAME": "db2",
            "GG_DB_MONITOR_2_TYPE": "oracle",
            "GG_DB_MONITOR_2_DSN": "host2:1521/DB2",
            "GG_DB_MONITOR_2_USERNAME": "user2",
            "GG_DB_MONITOR_2_PASSWORD": "pass2"
        }

        with patch.dict('os.environ', env_vars, clear=True):
            config = create_db_config_from_env()

            assert len(config) == 2
            assert "db1" in config
            assert "db2" in config

    def test_create_config_default_type(self):
        """Test that default database type is oracle."""
        env_vars = {
            "GG_DB_MONITOR_1_NAME": "test_db",
            "GG_DB_MONITOR_1_DSN": "localhost:1521/TEST",
            "GG_DB_MONITOR_1_USERNAME": "user",
            "GG_DB_MONITOR_1_PASSWORD": "pass"
        }

        with patch.dict('os.environ', env_vars, clear=True):
            config = create_db_config_from_env()

            assert config["test_db"]["type"] == "oracle"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
