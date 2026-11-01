"""In-process counters (Prometheus text format) and logging setup."""

from __future__ import annotations

import json
import logging
import sys
from collections import Counter
from datetime import datetime, timezone
from threading import Lock
from typing import Dict, Iterable, List, Tuple

from .redaction import redact_error_text


def _escape(value: str) -> str:
    return value.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n")


def _sample(metric: str, labels: Dict[str, str], value: float) -> str:
    rendered = ",".join(f'{k}="{_escape(v)}"' for k, v in sorted(labels.items()))
    return f"{metric}{{{rendered}}} {value:g}" if rendered else f"{metric} {value:g}"


class Telemetry:
    """Tool-call counters. Values reset on restart, which Prometheus counters tolerate."""

    def __init__(self) -> None:
        self._lock = Lock()
        self._calls: Counter = Counter()  # (tool, status) -> calls
        self._seconds: Counter = Counter()  # tool -> seconds
        self._counts: Counter = Counter()  # tool -> calls
        self._denials: Counter = Counter()  # code -> denials

    def record_call(self, tool: str, status: str, seconds: float) -> None:
        with self._lock:
            self._calls[(tool, status)] += 1
            self._seconds[tool] += seconds
            self._counts[tool] += 1

    def record_denial(self, code: str) -> None:
        with self._lock:
            self._denials[code] += 1

    def render(self, *, deployments: int, cache_stats: Iterable[Tuple[str, dict]]) -> str:
        """Prometheus text exposition of the counters plus per-deployment cache stats."""
        with self._lock:
            calls = sorted(self._calls.items())
            seconds = sorted(self._seconds.items())
            counts = sorted(self._counts.items())
            denials = sorted(self._denials.items())

        lines: List[str] = [
            "# TYPE ggmcp_tool_calls_total counter",
            *(
                _sample("ggmcp_tool_calls_total", {"tool": tool, "status": status}, n)
                for (tool, status), n in calls
            ),
            "# TYPE ggmcp_tool_duration_seconds summary",
            *(_sample("ggmcp_tool_duration_seconds_sum", {"tool": t}, s) for t, s in seconds),
            *(_sample("ggmcp_tool_duration_seconds_count", {"tool": t}, n) for t, n in counts),
            "# TYPE ggmcp_policy_denials_total counter",
            *(_sample("ggmcp_policy_denials_total", {"code": c}, n) for c, n in denials),
            "# TYPE ggmcp_deployments_configured gauge",
            _sample("ggmcp_deployments_configured", {}, deployments),
            "# TYPE ggmcp_cache_hits_total counter",
            "# TYPE ggmcp_cache_misses_total counter",
        ]
        for name, stats in cache_stats:
            lines.append(_sample("ggmcp_cache_hits_total", {"deployment": name}, stats["hits"]))
            lines.append(_sample("ggmcp_cache_misses_total", {"deployment": name}, stats["misses"]))
        return "\n".join(lines) + "\n"


class JsonFormatter(logging.Formatter):
    """One JSON object per line, for log shippers."""

    def format(self, record: logging.LogRecord) -> str:
        entry = {
            "timestamp": datetime.fromtimestamp(record.created, timezone.utc).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": redact_error_text(record.getMessage(), max_len=8000),
        }
        if record.exc_info:
            entry["exception"] = redact_error_text(self.formatException(record.exc_info))
        return json.dumps(entry)


def configure_logging(level: str = "INFO", fmt: str = "text") -> None:
    """Send logs to stderr; stdout carries the MCP protocol."""
    handler = logging.StreamHandler(sys.stderr)
    if fmt == "json":
        handler.setFormatter(JsonFormatter())
    else:
        handler.setFormatter(
            logging.Formatter("%(asctime)s - %(name)s - %(levelname)s - %(message)s")
        )
    logging.basicConfig(level=level, handlers=[handler], force=True)
