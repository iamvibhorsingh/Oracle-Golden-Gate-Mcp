"""Shared helpers for the GoldenGate MCP server."""

from __future__ import annotations

import logging
from typing import Optional

logger = logging.getLogger(__name__)


def parse_lag_duration(value: Optional[str]) -> Optional[float]:
    """
    Parse a GoldenGate lag duration string to seconds.

    Examples:
        "00:00:05" -> 5.0
        "00:02:30" -> 150.0
        "01:30:00" -> 5400.0
    """
    if not value or value == "N/A":
        return None

    try:
        if ":" in value:
            parts = value.split(":")
            if len(parts) == 3:
                hours, minutes, seconds = map(float, parts)
                return hours * 3600 + minutes * 60 + seconds
        return float(value)
    except (ValueError, AttributeError):
        logger.warning("Could not parse lag value: %s", value)
        return None
