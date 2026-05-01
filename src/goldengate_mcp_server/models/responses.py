"""Typed tool outputs and lag severity classification."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Literal, Optional

from pydantic import BaseModel, Field

from ..metrics_store import MetricsStore
from ..utils import parse_lag_duration

Severity = Literal["normal", "elevated", "high", "critical"]
ProcessType = Literal["extract", "replicat"]
ProcessStatus = Literal["running", "stopped", "abended", "unknown"]


def deviation_sigma(
    current: float,
    baseline_mean: float,
    baseline_std_dev: float,
) -> Optional[float]:
    if baseline_std_dev <= 0:
        return None
    return (current - baseline_mean) / baseline_std_dev


def classify_severity(
    current: Optional[float],
    baseline_mean: Optional[float],
    baseline_std_dev: Optional[float],
    baseline_p95: Optional[float],
    status: str,
) -> Severity:
    if status != "running":
        return "critical"

    if current is None or baseline_mean is None:
        return "normal"

    sigma: Optional[float] = None
    if baseline_std_dev is not None and baseline_std_dev > 0:
        sigma = deviation_sigma(current, baseline_mean, baseline_std_dev)

    if sigma is not None and sigma > 5:
        return "critical"

    if (sigma is not None and sigma > 3) or current > baseline_mean * 2:
        return "high"

    if (not baseline_std_dev or baseline_std_dev <= 0) and current > baseline_mean * 3:
        return "high"

    if sigma is not None and sigma > 1.5:
        return "elevated"

    if baseline_p95 is not None and current > baseline_p95:
        return "elevated"

    if current > baseline_mean * 1.5:
        return "elevated"

    return "normal"


class ProcessLag(BaseModel):
    deployment: str
    process_name: str
    process_type: ProcessType
    status: ProcessStatus
    lag_seconds: Optional[float] = None
    lag_at_checkpoint: Optional[str] = None
    time_since_checkpoint: Optional[str] = None
    baseline_mean_seconds: Optional[float] = None
    baseline_p95_seconds: Optional[float] = None
    baseline_std_dev_seconds: Optional[float] = None
    baseline_data_points: int = 0
    deviation_sigma: Optional[float] = None
    severity: Severity = "normal"
    raw_lag: Optional[dict[str, Any]] = Field(
        default=None,
        description="Original lag fields from the GoldenGate API",
    )
    collected_at_utc: str


def _normalize_status(raw: Optional[str]) -> ProcessStatus:
    if not raw:
        return "unknown"
    s = raw.lower()
    if s in ("running", "stopped", "abended"):
        return s  # type: ignore[return-value]
    return "unknown"


def status_for_lag_severity(raw: Optional[str], lag_seconds: Optional[float]) -> str:
    s = (raw or "").lower()
    if s == "running":
        return "running"
    if s in ("stopped", "abended"):
        return s
    if lag_seconds is not None:
        return "running"
    return "unknown"


def build_process_lag_payload(
    deployment: str,
    process_name: str,
    process_type: ProcessType,
    lag_api_data: dict[str, Any],
    metrics_store: MetricsStore,
) -> dict[str, Any]:
    status = _normalize_status(lag_api_data.get("status"))
    lag_seconds = parse_lag_duration(lag_api_data.get("lag"))

    baseline = (
        metrics_store.calculate_baseline(deployment, process_name, days=7)
        if metrics_store is not None
        else {}
    )
    baseline_mean: Optional[float] = None
    baseline_p95: Optional[float] = None
    baseline_std: Optional[float] = None
    baseline_points = 0

    if baseline.get("data_points", 0) > 0 and "error" not in baseline:
        baseline_mean = baseline.get("mean")
        baseline_p95 = baseline.get("p95")
        baseline_std = baseline.get("std_dev")
        baseline_points = int(baseline.get("data_points", 0))

    sigma: Optional[float] = None
    if (
        lag_seconds is not None
        and baseline_mean is not None
        and baseline_std is not None
        and baseline_std > 0
    ):
        sigma = deviation_sigma(lag_seconds, baseline_mean, baseline_std)

    _normalize_status(lag_api_data.get("status"))
    severity = classify_severity(
        lag_seconds,
        baseline_mean,
        baseline_std,
        baseline_p95,
        status_for_lag_severity(lag_api_data.get("status"), lag_seconds),
    )

    model = ProcessLag(
        deployment=deployment,
        process_name=process_name,
        process_type=process_type,
        status=status,
        lag_seconds=lag_seconds,
        lag_at_checkpoint=lag_api_data.get("lag_at_chkpt")
        or lag_api_data.get("lag_at_checkpoint"),
        time_since_checkpoint=lag_api_data.get("time_since_chkpt")
        or lag_api_data.get("time_since_checkpoint"),
        baseline_mean_seconds=baseline_mean,
        baseline_p95_seconds=baseline_p95,
        baseline_std_dev_seconds=baseline_std,
        baseline_data_points=baseline_points,
        deviation_sigma=sigma,
        severity=severity,
        raw_lag=dict(lag_api_data),
        collected_at_utc=datetime.now(timezone.utc).isoformat(),
    )
    return model.model_dump(mode="json")
