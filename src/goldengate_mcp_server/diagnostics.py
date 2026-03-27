"""Lag diagnostics using live API data plus MetricsStore history."""

import logging
import statistics
from datetime import datetime
from typing import Any, Dict, List, Optional

from .models import classify_severity, deviation_sigma, status_for_lag_severity
from .utils import parse_lag_duration

logger = logging.getLogger(__name__)

_SEVERITY_RANK = {
    "normal": 0,
    "elevated": 1,
    "high": 2,
    "critical": 3,
    "unknown": -1,
    "low": 0,
    "medium": 1,
}


def _max_severity(*labels: str) -> str:
    return max(labels, key=lambda s: _SEVERITY_RANK.get(s, -1))


class DiagnosticsEngine:
    """
    Advanced diagnostics engine for GoldenGate troubleshooting.

    Analyzes current state, historical patterns, and correlations
    to identify root causes and provide actionable recommendations.
    """

    def __init__(self, metrics_store, goldengate_clients: Dict):
        self.metrics_store = metrics_store
        self.clients = goldengate_clients

    async def diagnose_lag_issue(
        self,
        deployment: str,
        process_name: str,
        process_type: str
    ) -> Dict[str, Any]:
        logger.info(f"Diagnosing lag issue for {deployment}/{process_name}")

        diagnosis: Dict[str, Any] = {
            "deployment": deployment,
            "process": process_name,
            "process_type": process_type,
            "timestamp": datetime.utcnow().isoformat(),
            "severity": "unknown",
            "likely_causes": [],
            "contributing_factors": [],
            "recommendations": [],
            "evidence": {},
            "confidence_score": 0.0,
        }

        client = self.clients.get(deployment)
        if not client:
            diagnosis["error"] = f"Unknown deployment: {deployment}"
            return diagnosis

        try:
            # 1. Get current lag
            if process_type == "extract":
                lag_data = await client.get_extract_lag(process_name)
                status_data = await client.get_extract_status(process_name)
            else:
                lag_data = await client.get_replicat_lag(process_name)
                status_data = await client.get_replicat_status(process_name)

            current_lag = parse_lag_duration(lag_data.get("lag"))
            current_status = lag_data.get("status")
            running = (current_status or "").lower() == "running"

            diagnosis["evidence"]["current_lag_seconds"] = current_lag
            diagnosis["evidence"]["current_status"] = current_status

            if not running:
                return await self._diagnose_non_running_process(
                    diagnosis, status_data, current_status
                )

            # 2. Get historical baseline
            baseline = self.metrics_store.calculate_baseline(
                deployment, process_name, days=7
            )

            if baseline.get("data_points", 0) > 0:
                diagnosis["evidence"]["baseline"] = baseline

                cause = self._analyze_lag_vs_baseline(current_lag, baseline)
                if cause:
                    diagnosis["likely_causes"].append(cause)
                    cf = cause.get("contributing_factor")
                    if cf:
                        diagnosis["contributing_factors"].append(cf)
            else:
                diagnosis["evidence"]["baseline"] = "insufficient_data"
                if current_lag and current_lag > 60:
                    diagnosis["likely_causes"].append({
                        "cause": "High lag detected (no baseline for comparison)",
                        "evidence": f"Current lag: {current_lag}s",
                        "confidence": 0.5,
                    })
                    diagnosis["contributing_factors"].append({
                        "factor": "lag_without_baseline",
                        "current_lag_seconds": current_lag,
                        "unit": "seconds",
                        "threshold_note": "baseline_requires_24h_history",
                    })

            severity_bumps: List[str] = []

            # 3. Check for sudden spikes
            recent_history = self.metrics_store.get_lag_history(
                deployment, process_name, hours=2
            )

            if len(recent_history) > 5:
                spike_analysis = self._analyze_lag_spike(recent_history, current_lag)
                if spike_analysis:
                    diagnosis["likely_causes"].append(spike_analysis)
                    ne = spike_analysis.get("numeric_evidence")
                    if ne:
                        diagnosis["contributing_factors"].append(
                            {"factor": "lag_spike", **ne}
                        )
                    if spike_analysis.get("confidence", 0) > 0.7:
                        severity_bumps.append("high")

            # 4. Check hourly patterns
            hourly_pattern = self.metrics_store.get_hourly_pattern(
                deployment, process_name, days=7
            )

            if hourly_pattern:
                pattern_analysis = self._analyze_hourly_pattern(
                    current_lag, hourly_pattern
                )
                if pattern_analysis:
                    diagnosis["likely_causes"].append(pattern_analysis)

            # 5. Check process statistics for clues
            try:
                stats = await client.get_process_statistics(process_type, process_name)
                stats_analysis = self._analyze_process_statistics(stats)
                if stats_analysis:
                    diagnosis["likely_causes"].append(stats_analysis)
                    diagnosis["evidence"]["statistics"] = stats
                    sf = stats_analysis.get("structured_factor")
                    if sf:
                        diagnosis["contributing_factors"].append(sf)
            except Exception as e:
                logger.warning(f"Could not get process statistics: {e}")

            # 6. Check for errors in the process
            try:
                error_check = await client.check_process_errors(process_type, process_name)
                if error_check.get("has_errors"):
                    diagnosis["likely_causes"].append({
                        "cause": "Process has reported errors",
                        "evidence": error_check.get("messages", []),
                        "confidence": 0.9,
                    })
                    severity_bumps.append("high")
                    diagnosis["contributing_factors"].append({
                        "factor": "process_errors",
                        "has_errors": True,
                        "message_count": len(error_check.get("messages") or []),
                    })
            except Exception as e:
                logger.warning(f"Could not check process errors: {e}")

            self._apply_running_diagnosis_summary(
                diagnosis,
                current_lag,
                diagnosis["evidence"].get("baseline"),
                current_status,
                severity_bumps,
            )

            # 7. Generate recommendations based on findings
            diagnosis["recommendations"] = self._generate_recommendations(diagnosis)

            # 8. Calculate overall confidence
            diagnosis["confidence_score"] = self._calculate_confidence_score(diagnosis)

        except Exception as e:
            logger.error(f"Error during diagnosis: {e}", exc_info=True)
            diagnosis["error"] = str(e)

        return diagnosis

    def _apply_running_diagnosis_summary(
        self,
        diagnosis: Dict[str, Any],
        current_lag: Optional[float],
        baseline: Any,
        current_status: Optional[str],
        severity_bumps: List[str],
    ) -> None:
        baseline_dict = (
            baseline
            if isinstance(baseline, dict) and baseline.get("data_points", 0) > 0
            else {}
        )
        mean = baseline_dict.get("mean")
        std = baseline_dict.get("std_dev")
        p95 = baseline_dict.get("p95")

        diagnosis["current_lag_seconds"] = current_lag
        diagnosis["baseline_mean_seconds"] = mean
        diagnosis["baseline_std_dev_seconds"] = std
        diagnosis["baseline_p95_seconds"] = p95
        if (
            current_lag is not None
            and mean is not None
            and std is not None
            and std > 0
        ):
            diagnosis["deviation_sigma"] = deviation_sigma(current_lag, mean, std)
        else:
            diagnosis["deviation_sigma"] = None

        base = classify_severity(
            current_lag,
            mean,
            std,
            p95,
            status_for_lag_severity(current_status, current_lag),
        )
        diagnosis["severity"] = _max_severity(base, *severity_bumps)

    async def _diagnose_non_running_process(
        self,
        diagnosis: Dict[str, Any],
        status_data: Dict[str, Any],
        current_status: str
    ) -> Dict[str, Any]:
        """Diagnose a process that is not running."""

        if (current_status or "").lower() == "stopped":
            diagnosis["severity"] = "critical"
            diagnosis["likely_causes"].append({
                "cause": "Process is stopped",
                "evidence": "Process status is 'stopped'",
                "confidence": 1.0
            })
            diagnosis["recommendations"].append({
                "action": "Start the process if this is unintended",
                "command": f"START {diagnosis['process'].upper()}",
                "risk": "low"
            })

        elif (current_status or "").lower() == "abended":
            diagnosis["severity"] = "critical"
            diagnosis["likely_causes"].append({
                "cause": "Process has abended (crashed)",
                "evidence": status_data.get("messages", ["Check process logs"]),
                "confidence": 1.0
            })
            diagnosis["recommendations"].extend([
                {
                    "action": "Review error logs to identify root cause",
                    "priority": "immediate"
                },
                {
                    "action": "Resolve the underlying issue before restarting",
                    "priority": "immediate"
                },
                {
                    "action": "Restart process after fixing the issue",
                    "command": f"START {diagnosis['process'].upper()}",
                    "risk": "medium"
                }
            ])

        return diagnosis

    def _analyze_lag_vs_baseline(
        self,
        current_lag: Optional[float],
        baseline: Dict[str, Any],
    ) -> Optional[Dict[str, Any]]:
        """Compare current lag to baseline; return narrative cause and structured factor."""
        if current_lag is None or not baseline.get("mean"):
            return None

        mean = baseline["mean"]
        std_dev = baseline.get("std_dev", 0) or 0
        p95 = baseline.get("p95", mean)
        sigma = (current_lag - mean) / std_dev if std_dev > 0 else None

        contributing_factor: Dict[str, Any] = {
            "factor": "lag_vs_baseline",
            "current_lag_seconds": current_lag,
            "baseline_mean_seconds": mean,
            "baseline_std_dev_seconds": std_dev if std_dev else None,
            "baseline_p95_seconds": p95,
            "deviation_sigma": sigma,
        }

        if std_dev > 0 and sigma is not None and sigma > 3:
            return {
                "cause": "Lag is significantly above normal (>3σ)",
                "evidence": {
                    "current_lag": f"{current_lag:.1f}s",
                    "normal_avg": f"{mean:.1f}s",
                    "std_dev": f"{std_dev:.1f}s",
                    "deviation": f"{sigma:.1f}σ",
                },
                "confidence": 0.9,
                "contributing_factor": contributing_factor,
            }

        if current_lag > p95 * 1.5:
            return {
                "cause": "Lag is above 95th percentile",
                "evidence": {
                    "current_lag": f"{current_lag:.1f}s",
                    "p95": f"{p95:.1f}s",
                    "ratio": f"{current_lag / p95:.1f}x",
                },
                "confidence": 0.75,
                "contributing_factor": contributing_factor,
            }

        if current_lag > mean * 2:
            return {
                "cause": "Lag is more than 2x average",
                "evidence": {
                    "current_lag": f"{current_lag:.1f}s",
                    "normal_avg": f"{mean:.1f}s",
                    "multiplier": f"{current_lag / mean:.1f}x",
                },
                "confidence": 0.7,
                "contributing_factor": contributing_factor,
            }

        return None

    def _analyze_lag_spike(
        self,
        history: List[Dict[str, Any]],
        current_lag: Optional[float]
    ) -> Optional[Dict[str, Any]]:
        """Detect sudden lag spikes."""

        if not current_lag or len(history) < 5:
            return None

        # Get lag values from last 2 hours
        recent_lags = [
            h["lag_seconds"] for h in history
            if h["lag_seconds"] is not None
        ]

        if not recent_lags:
            return None

        avg_recent = statistics.mean(recent_lags)

        # Sudden spike detected
        if current_lag > avg_recent * 3:
            # Find when the spike started
            spike_start = None
            for i in range(len(history) - 1, 0, -1):
                if history[i]["lag_seconds"] and history[i]["lag_seconds"] < avg_recent * 1.5:
                    spike_start = datetime.fromisoformat(history[i]["timestamp"])
                    break

            duration = None
            if spike_start:
                duration = (datetime.utcnow() - spike_start).total_seconds() / 60

            return {
                "cause": "Sudden lag spike detected",
                "evidence": {
                    "current_lag": f"{current_lag:.1f}s",
                    "recent_avg": f"{avg_recent:.1f}s",
                    "spike_magnitude": f"{current_lag / avg_recent:.1f}x",
                    "spike_duration_minutes": f"{duration:.0f}" if duration else "unknown",
                },
                "numeric_evidence": {
                    "current_lag_seconds": current_lag,
                    "recent_mean_lag_seconds": avg_recent,
                    "spike_ratio": round(current_lag / avg_recent, 4) if avg_recent else None,
                    "unit": "seconds",
                },
                "confidence": 0.85,
                "note": "This is an unusual spike, not part of normal pattern",
            }

        return None

    def _analyze_hourly_pattern(
        self,
        current_lag: Optional[float],
        hourly_pattern: Dict[int, Dict[str, float]]
    ) -> Optional[Dict[str, Any]]:
        """Check if current lag matches expected hourly pattern."""

        if not current_lag:
            return None

        current_hour = datetime.utcnow().hour

        if current_hour not in hourly_pattern:
            return None

        expected = hourly_pattern[current_hour]
        expected_mean = expected.get("mean", 0)
        expected_max = expected.get("max", 0)

        if current_lag <= expected_mean * 1.5:
            return {
                "cause": "Current lag is normal for this time of day",
                "evidence": {
                    "current_hour": current_hour,
                    "current_lag": f"{current_lag:.1f}s",
                    "typical_for_hour": f"{expected_mean:.1f}s",
                    "max_seen_this_hour": f"{expected_max:.1f}s"
                },
                "confidence": 0.8,
                "note": "This appears to be expected behavior based on historical patterns"
            }

        return None

    def _analyze_process_statistics(
        self,
        stats: Dict[str, Any]
    ) -> Optional[Dict[str, Any]]:
        """Analyze process statistics for anomalies."""

        # Check for high discard rate
        total_ops = stats.get("inserts", 0) + stats.get("updates", 0) + stats.get("deletes", 0)
        discards = stats.get("discards", 0)

        if total_ops > 0 and discards / total_ops > 0.05:
            ratio = discards / total_ops
            return {
                "cause": "High discard rate detected",
                "evidence": {
                    "total_operations": total_ops,
                    "discards": discards,
                    "discard_rate": f"{ratio * 100:.1f}%",
                },
                "confidence": 0.7,
                "note": "High discard rate can indicate filtering or mapping issues",
                "structured_factor": {
                    "factor": "discard_rate",
                    "current_value": round(ratio, 6),
                    "unit": "ratio",
                    "threshold": 0.05,
                    "exceeded": True,
                },
            }

        # Check for low throughput
        ops_per_sec = stats.get("operations_per_sec", 0)
        if ops_per_sec < 1 and total_ops > 0:
            return {
                "cause": "Low throughput detected",
                "evidence": {"operations_per_second": ops_per_sec},
                "confidence": 0.6,
                "note": "Process is running slowly",
                "structured_factor": {
                    "factor": "operations_per_sec",
                    "current_value": ops_per_sec,
                    "unit": "operations_per_sec",
                    "threshold": 1.0,
                    "exceeded": True,
                },
            }

        return None

    def _generate_recommendations(
        self,
        diagnosis: Dict[str, Any]
    ) -> List[Dict[str, Any]]:
        """Generate actionable recommendations based on diagnosis."""

        recommendations = []
        severity = diagnosis.get("severity", "unknown")
        causes = diagnosis.get("likely_causes", [])

        # High severity recommendations
        if severity in ("high", "critical"):
            recommendations.append({
                "priority": "immediate",
                "action": "Investigate immediately - replication may be falling behind",
                "impact": "Data synchronization delay"
            })

        # Recommendations based on specific causes
        for cause in causes:
            cause_text = cause.get("cause", "").lower()

            if "spike" in cause_text:
                recommendations.append({
                    "action": "Check source database for sudden load increase",
                    "details": "Look for long-running queries, batch jobs, or application changes"
                })
                recommendations.append({
                    "action": "Review recent changes to the application or database",
                    "details": "Deployments, schema changes, or configuration updates"
                })

            if "abended" in cause_text:
                recommendations.append({
                    "action": "Review process error log",
                    "command": "VIEW REPORT in GoldenGate console"
                })

            if "discard" in cause_text:
                recommendations.append({
                    "action": "Review process parameter file for TABLE mappings",
                    "details": "Check for filtering rules that may be too aggressive"
                })

            if "throughput" in cause_text or "slow" in cause_text:
                recommendations.append({
                    "action": "Consider enabling parallel replication",
                    "details": "Use PARALLELISM parameter to increase throughput"
                })
                recommendations.append({
                    "action": "Check target database performance",
                    "details": "Slow target can bottleneck replication"
                })

        # General recommendations if no specific causes found
        if not recommendations:
            recommendations.append({
                "action": "Monitor for next 15-30 minutes to see if issue persists",
                "details": "Transient spikes may resolve on their own"
            })
            recommendations.append({
                "action": "Collect more data points for better analysis",
                "details": "Continue running MCP server to build historical baseline"
            })

        return recommendations

    def _calculate_confidence_score(self, diagnosis: Dict[str, Any]) -> float:
        """Calculate overall confidence in the diagnosis."""

        causes = diagnosis.get("likely_causes", [])
        if not causes:
            return 0.0

        # Weight by confidence and combine
        confidences = [c.get("confidence", 0.5) for c in causes]

        # Average of top 2 causes
        top_confidences = sorted(confidences, reverse=True)[:2]
        return sum(top_confidences) / len(top_confidences) if top_confidences else 0.0

class TroubleshootingGuide:
    """
    Provides intelligent troubleshooting guides based on symptoms.
    """

    @staticmethod
    def get_troubleshooting_guide(symptoms: Dict[str, Any]) -> Dict[str, Any]:
        guide = {
            "symptom_analysis": symptoms,
            "steps": [],
            "common_causes": [],
            "prevention": []
        }

        # High lag troubleshooting
        if symptoms.get("high_lag"):
            guide["steps"].extend([
                {
                    "step": 1,
                    "action": "Check process status",
                    "command": "INFO <process_name>",
                    "look_for": "Status should be RUNNING"
                },
                {
                    "step": 2,
                    "action": "Check lag details",
                    "command": "LAG <process_name>",
                    "look_for": "Time since last checkpoint"
                },
                {
                    "step": 3,
                    "action": "Check source database load",
                    "details": "High CPU or I/O on source can slow Extract"
                },
                {
                    "step": 4,
                    "action": "Check network connectivity",
                    "details": "Network issues between source and target"
                },
                {
                    "step": 5,
                    "action": "Check target database performance",
                    "details": "Slow target can bottleneck Replicat"
                }
            ])

            guide["common_causes"].extend([
                "Source database under heavy load",
                "Network latency or packet loss",
                "Target database performance issues",
                "Large transactions causing delays",
                "Insufficient GoldenGate resources (CPU/memory)"
            ])

        # Abended process troubleshooting
        if symptoms.get("abended"):
            guide["steps"].extend([
                {
                    "step": 1,
                    "action": "Review error log",
                    "command": "VIEW REPORT <process_name>",
                    "look_for": "Error messages near the end of the report"
                },
                {
                    "step": 2,
                    "action": "Check database connectivity",
                    "details": "Verify database is accessible and credentials are valid"
                },
                {
                    "step": 3,
                    "action": "Check for data issues",
                    "details": "Invalid data or constraint violations"
                },
                {
                    "step": 4,
                    "action": "Review recent schema changes",
                    "details": "Column additions/removals may cause issues"
                }
            ])

            guide["common_causes"].extend([
                "Database connection lost",
                "Invalid credentials",
                "Data type mismatches",
                "Constraint violations on target",
                "Missing supplemental logging on source"
            ])

        # Prevention strategies
        guide["prevention"].extend([
            "Enable monitoring and alerting",
            "Set up regular health checks",
            "Maintain historical metrics for baselining",
            "Document process configurations",
            "Schedule regular maintenance windows"
        ])

        return guide
