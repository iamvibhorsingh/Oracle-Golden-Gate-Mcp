"""
Database Performance Monitoring

Monitors source and target databases to correlate with GoldenGate performance.
Helps identify if lag is caused by database-side issues.
"""

import asyncio
import logging
from typing import Any, Dict, Optional

from .redaction import redact_error_text

logger = logging.getLogger(__name__)


class DatabaseMonitor:
    """
    Monitors database performance metrics to correlate with GoldenGate issues.

    Supports Oracle Database initially, with extensibility for other databases.
    """

    def __init__(self, db_connections: Optional[Dict[str, Any]] = None):
        """
        Initialize database monitor.

        Args:
            db_connections: Dictionary of database connection configs
        """
        self.db_connections = db_connections or {}
        self.oracle_available = False

        # Try to import Oracle database library
        try:
            import oracledb  # noqa: F401
            self.oracle_available = True
            logger.info("Oracle database monitoring enabled")
        except ImportError:
            logger.warning("oracledb not installed - database monitoring limited")

    async def check_source_database_health(
        self,
        db_name: str
    ) -> Dict[str, Any]:
        """
        Check source database health and performance.

        Args:
            db_name: Database identifier

        Returns:
            Health metrics
        """
        if db_name not in self.db_connections:
            return {
                "status": "unknown",
                "error": f"Database {db_name} not configured for monitoring"
            }

        db_config = self.db_connections[db_name]
        db_type = db_config.get("type", "oracle")

        if db_type == "oracle":
            return await self._check_oracle_health(db_config)
        else:
            return {
                "status": "unsupported",
                "error": f"Database type {db_type} monitoring not yet implemented"
            }

    async def _check_oracle_health(self, db_config: Dict[str, Any]) -> Dict[str, Any]:
        """Check Oracle database health."""

        if not self.oracle_available:
            return {
                "status": "unavailable",
                "error": "oracledb library not installed"
            }

        import oracledb

        health = {
            "database_type": "oracle",
            "timestamp": None,
            "metrics": {},
            "issues": []
        }

        try:
            # Create connection
            connection = await asyncio.to_thread(
                oracledb.connect,
                user=db_config["username"],
                password=db_config["password"],
                dsn=db_config["dsn"]
            )

            cursor = connection.cursor()

            # Get database load
            cursor.execute("""
                SELECT
                    metric_name,
                    value
                FROM v$sysmetric
                WHERE metric_name IN (
                    'CPU Usage Per Sec',
                    'Host CPU Utilization (%)',
                    'Current Logons Count',
                    'Active Serial Sessions',
                    'Database CPU Time Ratio',
                    'Database Wait Time Ratio'
                )
                AND group_id = 2
            """)

            for row in cursor:
                metric_name, value = row
                health["metrics"][metric_name] = float(value)

            # Check for high CPU
            cpu_pct = health["metrics"].get("Host CPU Utilization (%)", 0)
            if cpu_pct > 80:
                health["issues"].append({
                    "severity": "high",
                    "issue": "High CPU utilization",
                    "value": f"{cpu_pct:.1f}%",
                    "impact": "May slow down Extract capture"
                })

            # Check for high wait time
            wait_time_ratio = health["metrics"].get("Database Wait Time Ratio", 0)
            if wait_time_ratio > 50:
                health["issues"].append({
                    "severity": "medium",
                    "issue": "High database wait time",
                    "value": f"{wait_time_ratio:.1f}%",
                    "impact": "Database is spending significant time waiting"
                })

            # Get redo generation rate (important for GoldenGate)
            cursor.execute("""
                SELECT
                    value
                FROM v$sysmetric
                WHERE metric_name = 'Redo Generated Per Sec'
                AND group_id = 2
            """)

            row = cursor.fetchone()
            if row:
                redo_rate = float(row[0])
                health["metrics"]["Redo Generated Per Sec"] = redo_rate

                # High redo generation
                if redo_rate > 10000000:  # 10MB/sec
                    health["issues"].append({
                        "severity": "medium",
                        "issue": "High redo generation rate",
                        "value": f"{redo_rate / 1024 / 1024:.1f} MB/sec",
                        "impact": "Extract needs to process high volume of changes"
                    })

            # Check supplemental logging (critical for GoldenGate)
            cursor.execute("""
                SELECT supplemental_log_data_min
                FROM v$database
            """)

            row = cursor.fetchone()
            if row:
                supp_logging = row[0]
                health["metrics"]["Supplemental Logging"] = supp_logging

                if supp_logging != "YES":
                    health["issues"].append({
                        "severity": "critical",
                        "issue": "Supplemental logging not enabled",
                        "value": supp_logging,
                        "impact": "GoldenGate Extract may not capture all changes correctly"
                    })

            # Check tablespace usage
            cursor.execute("""
                SELECT
                    tablespace_name,
                    ROUND(used_percent, 1) as used_pct
                FROM dba_tablespace_usage_metrics
                WHERE used_percent > 85
                ORDER BY used_percent DESC
            """)

            for row in cursor:
                ts_name, used_pct = row
                health["issues"].append({
                    "severity": "medium",
                    "issue": f"Tablespace {ts_name} is {used_pct}% full",
                    "impact": "May cause database performance issues"
                })

            cursor.close()
            connection.close()

            health["status"] = "healthy" if not health["issues"] else "degraded"

        except Exception as e:
            logger.error(f"Error checking Oracle health: {e}")
            health["status"] = "error"
            health["error"] = redact_error_text(str(e))

        return health

    async def check_target_database_health(
        self,
        db_name: str
    ) -> Dict[str, Any]:
        """
        Check target database health and performance.

        Similar to source checks but focused on write performance.
        """
        # Reuse the same health check for now
        # In a full implementation, you'd add target-specific checks
        return await self.check_source_database_health(db_name)

    async def get_database_session_info(
        self,
        db_name: str,
        username: str
    ) -> Dict[str, Any]:
        """
        Get information about GoldenGate database sessions.

        Args:
            db_name: Database identifier
            username: GoldenGate database username

        Returns:
            Session information
        """
        if db_name not in self.db_connections:
            return {
                "error": f"Database {db_name} not configured"
            }

        db_config = self.db_connections[db_name]

        if db_config.get("type") == "oracle" and self.oracle_available:
            return await self._get_oracle_session_info(db_config, username)

        return {"error": "Not implemented for this database type"}

    async def _get_oracle_session_info(
        self,
        db_config: Dict[str, Any],
        gg_username: str
    ) -> Dict[str, Any]:
        """Get Oracle session info for GoldenGate user."""

        import oracledb

        session_info = {
            "sessions": [],
            "total_sessions": 0,
            "issues": []
        }

        try:
            connection = await asyncio.to_thread(
                oracledb.connect,
                user=db_config["username"],
                password=db_config["password"],
                dsn=db_config["dsn"]
            )

            cursor = connection.cursor()

            # Get GoldenGate sessions
            cursor.execute("""
                SELECT
                    sid,
                    serial#,
                    status,
                    program,
                    logon_time,
                    last_call_et
                FROM v$session
                WHERE username = :gg_user
                ORDER BY logon_time DESC
            """, {"gg_user": gg_username.upper()})

            for row in cursor:
                sid, serial, status, program, logon_time, last_call = row

                session_info["sessions"].append({
                    "sid": sid,
                    "serial": serial,
                    "status": status,
                    "program": program,
                    "logon_time": str(logon_time),
                    "seconds_since_last_call": last_call
                })

            session_info["total_sessions"] = len(session_info["sessions"])

            # Check for blocked sessions
            cursor.execute("""
                SELECT COUNT(*)
                FROM v$session
                WHERE username = :gg_user
                AND blocking_session IS NOT NULL
            """, {"gg_user": gg_username.upper()})

            blocked_count = cursor.fetchone()[0]
            if blocked_count > 0:
                session_info["issues"].append({
                    "severity": "high",
                    "issue": f"{blocked_count} GoldenGate sessions are blocked",
                    "impact": "Replication may be stalled"
                })

            cursor.close()
            connection.close()

        except Exception as e:
            logger.error(f"Error getting Oracle session info: {e}")
            session_info["error"] = redact_error_text(str(e))

        return session_info


def create_db_config_from_env() -> Dict[str, Any]:
    """
    Create database monitoring configuration from environment variables.

    Environment variables:
    - GG_DB_MONITOR_1_NAME: Database identifier
    - GG_DB_MONITOR_1_TYPE: Database type (oracle, mysql, etc.)
    - GG_DB_MONITOR_1_DSN: Connection string
    - GG_DB_MONITOR_1_USERNAME: Username
    - GG_DB_MONITOR_1_PASSWORD: Password
    """
    import os

    db_configs = {}
    i = 1

    while True:
        name = os.getenv(f"GG_DB_MONITOR_{i}_NAME")
        if not name:
            break

        db_configs[name] = {
            "type": os.getenv(f"GG_DB_MONITOR_{i}_TYPE", "oracle"),
            "dsn": os.getenv(f"GG_DB_MONITOR_{i}_DSN"),
            "username": os.getenv(f"GG_DB_MONITOR_{i}_USERNAME"),
            "password": os.getenv(f"GG_DB_MONITOR_{i}_PASSWORD")
        }

        i += 1

    return db_configs
