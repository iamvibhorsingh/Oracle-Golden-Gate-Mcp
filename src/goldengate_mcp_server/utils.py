"""Shared helpers for the GoldenGate MCP server."""

from __future__ import annotations

import logging
import re
from typing import Any, Dict, List, Optional, Union

logger = logging.getLogger(__name__)

# "2024-04-03 22:34:36  WARNING OGG-30426  Error mapping update record..."
_OGG_MESSAGE = re.compile(r"\b(ERROR|WARNING)\s+(OGG-\d{5})\b\s*(.*)")


def unwrap_response(payload: Any) -> Any:
    """Body of a GoldenGate reply, without its ``api:standardResponse`` envelope if any."""
    if isinstance(payload, dict) and isinstance(payload.get("response"), dict):
        return payload["response"]
    return payload


def collection_items(payload: Any) -> List[Dict[str, Any]]:
    """``items`` of a GoldenGate collection reply, enveloped or not."""
    body = unwrap_response(payload)
    return body.get("items", []) if isinstance(body, dict) else []


def normalize_status(value: Any) -> Any:
    """Process status in lower case: /info/status may report RUNNING where detail says running."""
    return value.lower() if isinstance(value, str) else value


def parse_ogg_messages(lines: List[Any]) -> List[Dict[str, str]]:
    """``OGG-nnnnn`` errors and warnings found in report lines."""
    messages = []
    for line in lines:
        match = _OGG_MESSAGE.search(str(line))
        if match:
            messages.append({
                "level": match.group(1),
                "code": match.group(2),
                "text": match.group(3).strip(),
            })
    return messages


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
