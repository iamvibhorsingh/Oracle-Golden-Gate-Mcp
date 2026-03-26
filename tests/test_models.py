"""Deterministic severity and response helpers."""

import pytest

from goldengate_mcp_server.models import classify_severity, deviation_sigma


def test_deviation_sigma():
    assert deviation_sigma(12.0, 2.0, 2.0) == 5.0
    assert deviation_sigma(12.0, 2.0, 0.0) is None


@pytest.mark.parametrize(
    ("current", "mean", "std", "p95", "status", "expected"),
    [
        (10.0, 5.0, 1.0, 8.0, "running", "high"),
        (10.0, 5.0, 1.0, 8.0, "stopped", "critical"),
        (6.0, 5.0, 1.0, 8.0, "running", "normal"),
        (None, 5.0, 1.0, 8.0, "running", "normal"),
        (6.0, None, 1.0, 8.0, "running", "normal"),
    ],
)
def test_classify_severity(current, mean, std, p95, status, expected):
    assert classify_severity(current, mean, std, p95, status) == expected


def test_classify_high_sigma():
    assert classify_severity(12.0, 5.0, 1.0, 8.0, "running") == "critical"
