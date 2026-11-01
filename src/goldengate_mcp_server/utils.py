"""Shared helpers for the GoldenGate MCP server."""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional, Union

logger = logging.getLogger(__name__)


def unwrap_response(payload: Any) -> Any:
    """Body of a GoldenGate reply, without its ``api:standardResponse`` envelope if any."""
    if isinstance(payload, dict) and isinstance(payload.get("response"), dict):
        return payload["response"]
    return payload


def collection_items(payload: Any) -> List[Dict[str, Any]]:
    """``items`` of a GoldenGate collection reply, enveloped or not."""
    body = unwrap_response(payload)
    return body.get("items", []) if isinstance(body, dict) else []


def parse_lag_duration(value: Optional[Union[str, int, float]]) -> Optional[float]:
    """
    Parse a GoldenGate lag duration (string, or plain seconds) to seconds.

    Examples:
        "00:00:05" -> 5.0
        "00:02:30" -> 150.0
        "01:30:00" -> 5400.0
        12 -> 12.0
    """
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return float(value)
    if not value or value == "N/A":
        return None

    try:
        if ":" in value:
            parts = value.split(":")
            if len(parts) == 3:
                hours, minutes, seconds = map(float, parts)
                return hours * 3600 + minutes * 60 + seconds
        return float(value)
    except (ValueError, AttributeError, TypeError):
        logger.warning("Could not parse lag value: %s", value)
        return None
