"""SQLite-backed storage for GoldenGate lag, stats, and health snapshots."""

import json
import logging
import sqlite3
import statistics
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional

from .utils import parse_lag_duration

logger = logging.getLogger(__name__)


class MetricsStore:
    """Historical GoldenGate metrics in SQLite."""

    def __init__(self, db_path: str = "./data/metrics.db", use_wal: bool = True):
        db_str = str(db_path)
        self.use_wal = use_wal
        self._connect_kwargs: dict = {}

        if db_str == ":memory:":
            self.db_path = "file::memory:?cache=shared"
            self._connect_kwargs = {"uri": True}
            self._memory_db = True
        elif db_str.startswith("file::memory:"):
            self.db_path = db_str
            self._connect_kwargs = {"uri": True}
            self._memory_db = True
        else:
            self.db_path = Path(db_path)
            self._memory_db = False
            self.db_path.parent.mkdir(parents=True, exist_ok=True)

        self._init_database()
        logger.info(f"Metrics store initialized at {db_path}")

    def _init_database(self):
        with sqlite3.connect(str(self.db_path), **self._connect_kwargs) as conn:
            if self.use_wal:
                conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("PRAGMA busy_timeout=5000")
            conn.execute("""
                CREATE TABLE IF NOT EXISTS lag_metrics (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    timestamp TEXT NOT NULL,
                    deployment TEXT NOT NULL,
                    process_name TEXT NOT NULL,
                    process_type TEXT NOT NULL,
                    lag_seconds REAL,
                    lag_at_chkpt TEXT,
                    time_since_chkpt TEXT,
                    status TEXT,
                    created_at TEXT DEFAULT CURRENT_TIMESTAMP
                )
            """)

            conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_lag_metrics_lookup
                ON lag_metrics(deployment, process_name, timestamp DESC)
            """)

            conn.execute("""
                CREATE TABLE IF NOT EXISTS process_statistics (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    timestamp TEXT NOT NULL,
                    deployment TEXT NOT NULL,
                    process_name TEXT NOT NULL,
                    process_type TEXT NOT NULL,
                    inserts INTEGER,
                    updates INTEGER,
                    deletes INTEGER,
                    discards INTEGER,
                    operations_per_sec REAL,
                    created_at TEXT DEFAULT CURRENT_TIMESTAMP
                )
            """)

            conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_process_stats_lookup
                ON process_statistics(deployment, process_name, timestamp DESC)
            """)

            conn.execute("""
                CREATE TABLE IF NOT EXISTS health_snapshots (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    timestamp TEXT NOT NULL,
                    deployment TEXT NOT NULL,
                    total_processes INTEGER,
                    running_processes INTEGER,
                    stopped_processes INTEGER,
                    abended_processes INTEGER,
                    avg_lag_seconds REAL,
                    max_lag_seconds REAL,
                    issues TEXT,
                    created_at TEXT DEFAULT CURRENT_TIMESTAMP
                )
            """)

            conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_health_snapshots_lookup
                ON health_snapshots(deployment, timestamp DESC)
            """)

    def record_lag_metric(
        self,
        deployment: str,
        process_name: str,
        process_type: str,
        lag_data: Dict[str, Any],
        timestamp: Optional[datetime] = None
    ):
        if timestamp is None:
            timestamp = datetime.utcnow()

        # Extract lag in seconds
        lag_seconds = parse_lag_duration(lag_data.get("lag"))

        with sqlite3.connect(str(self.db_path), **self._connect_kwargs) as conn:
            conn.execute("""
                INSERT INTO lag_metrics
                (timestamp, deployment, process_name, process_type,
                 lag_seconds, lag_at_chkpt, time_since_chkpt, status)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                timestamp.isoformat(),
                deployment,
                process_name,
                process_type,
                lag_seconds,
                lag_data.get("lag_at_chkpt"),
                lag_data.get("time_since_chkpt"),
                lag_data.get("status")
            ))

    def record_process_statistics(
        self,
        deployment: str,
        process_name: str,
        process_type: str,
        stats_data: Dict[str, Any],
        timestamp: Optional[datetime] = None
    ):
        if timestamp is None:
            timestamp = datetime.utcnow()

        with sqlite3.connect(str(self.db_path), **self._connect_kwargs) as conn:
            conn.execute("""
                INSERT INTO process_statistics
                (timestamp, deployment, process_name, process_type,
                 inserts, updates, deletes, discards, operations_per_sec)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                timestamp.isoformat(),
                deployment,
                process_name,
                process_type,
                stats_data.get("inserts", 0),
                stats_data.get("updates", 0),
                stats_data.get("deletes", 0),
                stats_data.get("discards", 0),
                stats_data.get("operations_per_sec", 0.0)
            ))

    def record_health_snapshot(
        self,
        deployment: str,
        health_data: Dict[str, Any],
        timestamp: Optional[datetime] = None
    ):
        if timestamp is None:
            timestamp = datetime.utcnow()

        extracts = health_data.get("extracts", [])
        replicats = health_data.get("replicats", [])
        all_processes = extracts + replicats

        total = len(all_processes)
        running = sum(1 for p in all_processes if p.get("status") == "running")
        stopped = sum(1 for p in all_processes if p.get("status") == "stopped")
        abended = sum(1 for p in all_processes if p.get("status") == "abended")

        lag_values = []
        for process in all_processes:
            if "lag" in process and process.get("status") == "running":
                lag = parse_lag_duration(process["lag"].get("lag"))
                if lag is not None:
                    lag_values.append(lag)

        avg_lag = statistics.mean(lag_values) if lag_values else None
        max_lag = max(lag_values) if lag_values else None

        with sqlite3.connect(str(self.db_path), **self._connect_kwargs) as conn:
            conn.execute("""
                INSERT INTO health_snapshots
                (timestamp, deployment, total_processes, running_processes,
                 stopped_processes, abended_processes, avg_lag_seconds, max_lag_seconds, issues)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                timestamp.isoformat(),
                deployment,
                total,
                running,
                stopped,
                abended,
                avg_lag,
                max_lag,
                json.dumps(health_data.get("issues", []))
            ))

    def get_lag_history(
        self,
        deployment: str,
        process_name: str,
        hours: int = 24
    ) -> List[Dict[str, Any]]:
        cutoff = datetime.utcnow() - timedelta(hours=hours)

        with sqlite3.connect(str(self.db_path), **self._connect_kwargs) as conn:
            conn.row_factory = sqlite3.Row
            cursor = conn.execute("""
                SELECT * FROM lag_metrics
                WHERE deployment = ? AND process_name = ? AND timestamp >= ?
                ORDER BY timestamp ASC
            """, (deployment, process_name, cutoff.isoformat()))

            return [dict(row) for row in cursor.fetchall()]

    def get_health_history(
        self,
        deployment: str,
        hours: int = 24
    ) -> List[Dict[str, Any]]:
        cutoff = datetime.utcnow() - timedelta(hours=hours)

        with sqlite3.connect(str(self.db_path), **self._connect_kwargs) as conn:
            conn.row_factory = sqlite3.Row
            cursor = conn.execute("""
                SELECT * FROM health_snapshots
                WHERE deployment = ? AND timestamp >= ?
                ORDER BY timestamp ASC
            """, (deployment, cutoff.isoformat()))

            return [dict(row) for row in cursor.fetchall()]

    def calculate_baseline(
        self,
        deployment: str,
        process_name: str,
        days: int = 7
    ) -> Dict[str, Any]:
        history = self.get_lag_history(deployment, process_name, hours=days * 24)

        if not history:
            return {
                "error": "Insufficient data for baseline",
                "data_points": 0
            }

        lag_values = [
            h["lag_seconds"] for h in history
            if h["lag_seconds"] is not None and h["status"] == "running"
        ]

        if not lag_values:
            return {
                "error": "No valid lag data points",
                "data_points": 0
            }

        return {
            "mean": statistics.mean(lag_values),
            "median": statistics.median(lag_values),
            "std_dev": statistics.stdev(lag_values) if len(lag_values) > 1 else 0,
            "min": min(lag_values),
            "max": max(lag_values),
            "p95": (
                statistics.quantiles(lag_values, n=20)[18]
                if len(lag_values) > 20
                else max(lag_values)
            ),
            "p99": (
                statistics.quantiles(lag_values, n=100)[98]
                if len(lag_values) > 100
                else max(lag_values)
            ),
            "data_points": len(lag_values),
            "time_range_days": days
        }

    def get_hourly_pattern(
        self,
        deployment: str,
        process_name: str,
        days: int = 7
    ) -> Dict[int, Dict[str, float]]:
        history = self.get_lag_history(deployment, process_name, hours=days * 24)

        hourly_data = {h: [] for h in range(24)}

        for record in history:
            if record["lag_seconds"] is not None and record["status"] == "running":
                timestamp = datetime.fromisoformat(record["timestamp"])
                hour = timestamp.hour
                hourly_data[hour].append(record["lag_seconds"])

        hourly_stats = {}
        for hour, values in hourly_data.items():
            if values:
                hourly_stats[hour] = {
                    "mean": statistics.mean(values),
                    "median": statistics.median(values),
                    "max": max(values),
                    "sample_count": len(values)
                }

        return hourly_stats

    def cleanup_old_data(self, retention_days: int = 30):
        cutoff = datetime.utcnow() - timedelta(days=retention_days)

        with sqlite3.connect(str(self.db_path), **self._connect_kwargs) as conn:
            tables = ["lag_metrics", "process_statistics", "health_snapshots"]
            for table in tables:
                result = conn.execute(f"""
                    DELETE FROM {table}
                    WHERE timestamp < ?
                """, (cutoff.isoformat(),))

                deleted = result.rowcount
                if deleted > 0:
                    logger.info(f"Cleaned up {deleted} old records from {table}")

    @staticmethod
    def _parse_lag_to_seconds(lag_str: Optional[str]) -> Optional[float]:
        return parse_lag_duration(lag_str)
