"""Diagnostics engine, baselines, trends, DB correlation, troubleshooting."""

from __future__ import annotations

import asyncio
import statistics
from datetime import datetime
from typing import Any, Dict

from ..diagnostics import TroubleshootingGuide


class DiagnosticsMixin:
    """Requires: diagnostics, metrics_store, db_monitor, _get_deployment_health."""

    diagnostics: Any
    metrics_store: Any
    db_monitor: Any

    async def _diagnose_lag_issue(
        self,
        deployment: str,
        process_name: str,
        process_type: str,
    ) -> Dict[str, Any]:
        return await self.diagnostics.diagnose_lag_issue(
            deployment,
            process_name,
            process_type,
        )

    async def _get_performance_baseline(
        self,
        deployment: str,
        process_name: str,
    ) -> Dict[str, Any]:
        baseline = await asyncio.to_thread(
            self.metrics_store.calculate_baseline,
            deployment,
            process_name,
            days=7,
        )

        hourly_pattern = await asyncio.to_thread(
            self.metrics_store.get_hourly_pattern,
            deployment,
            process_name,
            days=7,
        )

        return {
            "deployment": deployment,
            "process": process_name,
            "baseline_stats": baseline,
            "hourly_pattern": hourly_pattern,
            "note": "Baseline calculated from last 7 days of data",
        }

    async def _get_lag_trend(
        self,
        deployment: str,
        process_name: str,
    ) -> Dict[str, Any]:
        history = await asyncio.to_thread(
            self.metrics_store.get_lag_history,
            deployment,
            process_name,
            hours=24,
        )

        if not history:
            return {
                "deployment": deployment,
                "process": process_name,
                "error": "No historical data available",
                "note": "Run the server for a while to build up historical data",
            }

        lag_values = [h["lag_seconds"] for h in history if h["lag_seconds"] is not None]

        trend_analysis: Dict[str, Any] = {
            "deployment": deployment,
            "process": process_name,
            "time_range": "last 24 hours",
            "data_points": len(lag_values),
            "trend": {},
        }

        if lag_values:
            trend_analysis["trend"] = {
                "current": lag_values[-1],
                "min": min(lag_values),
                "max": max(lag_values),
                "mean": statistics.mean(lag_values),
                "median": statistics.median(lag_values),
            }

            if len(lag_values) >= 10:
                first_half = lag_values[: len(lag_values) // 2]
                second_half = lag_values[len(lag_values) // 2 :]

                avg_first = statistics.mean(first_half)
                avg_second = statistics.mean(second_half)

                if avg_second > avg_first * 1.2:
                    trend_analysis["trend"]["direction"] = "increasing"
                    trend_analysis["trend"]["note"] = "Lag is trending upward"
                elif avg_second < avg_first * 0.8:
                    trend_analysis["trend"]["direction"] = "decreasing"
                    trend_analysis["trend"]["note"] = "Lag is trending downward"
                else:
                    trend_analysis["trend"]["direction"] = "stable"
                    trend_analysis["trend"]["note"] = "Lag is relatively stable"

        trend_analysis["history"] = history

        return trend_analysis

    async def _check_database_correlation(
        self,
        deployment: str,
        database_name: str,
    ) -> Dict[str, Any]:
        correlation: Dict[str, Any] = {
            "deployment": deployment,
            "database": database_name,
            "timestamp": datetime.utcnow().isoformat(),
        }

        db_health = await self.db_monitor.check_source_database_health(database_name)
        correlation["database_health"] = db_health

        gg_health = await self._get_deployment_health(deployment)
        correlation["goldengate_health"] = gg_health

        correlation["analysis"] = []

        if db_health.get("status") == "error":
            correlation["analysis"].append({
                "finding": "Cannot access database for monitoring",
                "impact": "Unable to correlate database performance with GoldenGate lag",
            })
        elif db_health.get("issues"):
            for issue in db_health["issues"]:
                if "CPU" in issue.get("issue", ""):
                    correlation["analysis"].append({
                        "finding": "High database CPU detected",
                        "evidence": issue,
                        "impact": "Database load may be causing Extract to slow down",
                        "recommendation": "Consider reducing database load or Extract filtering",
                    })
                elif "wait" in issue.get("issue", "").lower():
                    correlation["analysis"].append({
                        "finding": "High database wait time detected",
                        "evidence": issue,
                        "impact": "Database I/O issues may impact replication performance",
                    })
        else:
            correlation["analysis"].append({
                "finding": "Database performance appears normal",
                "note": "Database is not likely the cause of replication issues",
            })

        return correlation

    def _get_troubleshooting_guide(self, symptoms: Dict[str, Any]) -> Dict[str, Any]:
        return TroubleshootingGuide.get_troubleshooting_guide(symptoms)
