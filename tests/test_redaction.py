"""Tests for MCP response and audit redaction helpers."""

from goldengate_mcp_server.redaction import (
    redact_error_text,
    redact_parameter_file_text,
    redact_sensitive_data,
)


def test_redact_nested_keys():
    payload = {
        "x": 1,
        "nested": {"api_key": "secret", "ok": "visible"},
        "username": "u1",
    }
    out = redact_sensitive_data(payload)
    assert out["x"] == 1
    assert out["nested"]["api_key"] == "***REDACTED***"
    assert out["nested"]["ok"] == "visible"
    assert out["username"] == "u1"


def test_redact_parameter_blob_by_key():
    payload = {
        "parameter_file": "USERID ggs, PASSWORD secret\nTABLE x.*;",
    }
    out = redact_sensitive_data(payload)
    assert "secret" not in out["parameter_file"]
    assert "REDACTED" in out["parameter_file"]


def test_redact_parameter_file_direct():
    text = "USERID ggs, PASSWORD hunter2\nfoo bar"
    out = redact_parameter_file_text(text)
    assert "hunter2" not in out
    assert "foo bar" in out


def test_redact_error_text_basic_auth_url():
    msg = 'Failed: //user:pass456@host/db error'
    out = redact_error_text(msg)
    assert "pass456" not in out
    assert "REDACTED" in out or "***" in out
