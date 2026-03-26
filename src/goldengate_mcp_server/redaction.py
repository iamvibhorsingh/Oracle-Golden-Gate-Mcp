"""
Redact secrets from MCP responses, audit payloads, and error strings.
"""

from __future__ import annotations

import copy
import re
from typing import Any, Mapping, Set

# Substrings for dict keys whose values must not appear in MCP responses
# (avoid bare "auth" → "author").
_SENSITIVE_KEY_FRAGMENTS: tuple[str, ...] = (
    "password",
    "passwd",
    "pwd",
    "secret",
    "token",
    "api_key",
    "apikey",
    "credential",
    "private_key",
    "access_token",
    "refresh_token",
    "client_secret",
)

_EXPLICIT_SENSITIVE_KEYS: frozenset[str] = frozenset(
    {"authorization", "auth_token", "auth_header"}
)

_REDACT_PLACEHOLDER = "***REDACTED***"

_USERID_WITH_PASSWORD = re.compile(
    r"(?i)^\s*userid\s+.+,.*\bpassword\b",
)


def redact_parameter_file_text(text: str) -> str:
    """Redact obvious credential lines from a parameter file or similar blob."""
    if not text or not isinstance(text, str):
        return text
    out: list[str] = []
    for line in text.splitlines():
        stripped = line.lstrip()
        if stripped.startswith(("--", "#")):
            out.append(line)
            continue
        low = stripped.lower()
        if (
            low.startswith("password ")
            or low.startswith("password\t")
            or "encryptkey" in low
            or "decryptkey" in low
            or low.startswith("wallet ")
            or low.startswith("wallet\t")
            or _USERID_WITH_PASSWORD.search(line)
            or re.search(r"(?i)\bpassword\s*=", line)
        ):
            out.append(f"{_REDACT_PLACEHOLDER} (credential line removed)")
        else:
            out.append(line)
    return "\n".join(out)


def _key_suggests_param_blob(key: str) -> bool:
    k = key.lower().replace("-", "_")
    return any(
        frag in k
        for frag in ("parameter", "param_file", "file_content", "contents", "content", "prmf")
    )

# Match Basic auth and common URL/query credential patterns in free-text errors.
_ERROR_PATTERNS = (
    re.compile(
        r"(?i)(password|passwd|pwd|secret|token|api[_-]?key)\s*[=:]\s*\S+",
    ),
    re.compile(r"(?i)//[^:]+:[^@]+@"),
    re.compile(r"(?i)Bearer\s+\S+"),
)


def _key_is_sensitive(key: str) -> bool:
    k = key.lower().replace("-", "_")
    if k in {"username", "user_name", "deployment", "base_url"}:
        return False
    if k in _EXPLICIT_SENSITIVE_KEYS:
        return True
    return any(frag in k for frag in _SENSITIVE_KEY_FRAGMENTS)


def redact_sensitive_data(obj: Any, *, _seen: Set[int] | None = None) -> Any:
    """
    Walk JSON-like structures and return a new tree with sensitive dict keys redacted.
    """
    if _seen is None:
        _seen = set()

    if isinstance(obj, Mapping):
        oid = id(obj)
        if oid in _seen:
            return {"_redaction": "cycle_omitted"}
        _seen.add(oid)
        try:
            out: dict[str, Any] = {}
            for k, v in obj.items():
                ks = str(k)
                if _key_is_sensitive(ks):
                    out[ks] = _REDACT_PLACEHOLDER
                else:
                    cleaned = redact_sensitive_data(v, _seen=_seen)
                    if isinstance(cleaned, str) and _key_suggests_param_blob(ks):
                        cleaned = redact_parameter_file_text(cleaned)
                    out[ks] = cleaned
            return out
        finally:
            _seen.discard(oid)

    if isinstance(obj, (list, tuple)):
        return type(obj)(redact_sensitive_data(item, _seen=_seen) for item in obj)

    if isinstance(obj, (str, int, float, bool)) or obj is None:
        return obj

    # sets, bytes, etc. — stringify without leaking nested structures
    return str(obj)


def redact_sensitive_data_copy(obj: Any) -> Any:
    """Deep-copy then redact (caller retains original references)."""
    return redact_sensitive_data(copy.deepcopy(obj))


def redact_error_text(message: str, max_len: int = 2000) -> str:
    """Scrub common credential patterns from exception or HTTP error text."""
    if not message:
        return message
    text = message[:max_len]
    for pat in _ERROR_PATTERNS:
        text = pat.sub(lambda _m: _REDACT_PLACEHOLDER, text)
    return text
