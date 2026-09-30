"""Tests for the audit log's hash chain: writing, recovery, rotation and tamper detection."""

import hashlib
import json
import logging
import threading
from itertools import pairwise

import pytest

from goldengate_mcp_server.audit import GENESIS_HASH, AuditLogger, verify_audit_log


@pytest.fixture(autouse=True)
def fresh_audit_logger():
    """AuditLogger reuses the process-wide "audit" logger's handler; give each test its own."""
    audit = logging.getLogger("audit")
    saved = audit.handlers[:]
    audit.handlers.clear()
    yield
    for h in audit.handlers:
        h.close()
    audit.handlers[:] = saved


def _restart(path, **kwargs):
    """Simulate a server restart: drop the old handler, build a new AuditLogger on the file."""
    audit = logging.getLogger("audit")
    for h in audit.handlers[:]:
        h.close()
        audit.removeHandler(h)
    return AuditLogger(str(path), **kwargs)


def _entry(line):
    return json.loads(line.split(" - ", 1)[1])


def _entries(path):
    return [_entry(line) for line in path.read_text().splitlines()]


def _rewrite(path, lines):
    path.write_text("\n".join(lines) + "\n")


def _log_some(audit, n):
    for i in range(n):
        audit.log_action(action=f"list_extracts_{i}", arguments={"deployment": "d"})


def _expected_hash(entry):
    """The documented hash: SHA-256 of the canonical JSON of the entry minus its hash."""
    body = {k: v for k, v in entry.items() if k != "hash"}
    canonical = json.dumps(body, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode()).hexdigest()


@pytest.fixture
def log5(tmp_path):
    path = tmp_path / "audit.log"
    _log_some(AuditLogger(str(path)), 5)
    return path


# ---- Writing ------------------------------------------------------------------

def test_every_event_type_is_chained(tmp_path):
    path = tmp_path / "audit.log"
    audit = AuditLogger(str(path))
    audit.log_action(action="list_extracts", arguments={"deployment": "d"})
    audit.log_result(action="list_extracts", success=True)
    audit.log_security_event("write_operation_blocked", {"operation": "start_extract"})
    audit.log_authentication("d", success=False, username="oggadmin", error="401")

    entries = _entries(path)
    assert [e["event_type"] for e in entries] == [
        "action", "result", "security", "authentication",
    ]
    assert [e["seq"] for e in entries] == [1, 2, 3, 4]
    assert entries[0]["prev_hash"] == GENESIS_HASH
    for prev, cur in pairwise(entries):
        assert cur["prev_hash"] == prev["hash"]
    for e in entries:
        assert e["hash"] == _expected_hash(e)
    assert verify_audit_log(path).ok


def test_existing_readers_and_redaction_are_unaffected(tmp_path):
    path = tmp_path / "audit.log"
    audit = AuditLogger(str(path))
    audit.log_action(action="list_extracts", arguments={"deployment": "d", "password": "hunter2"})
    audit.log_result(action="list_extracts", success=True)

    recent = audit.get_recent_entries()
    assert [e["event_type"] for e in recent] == ["action", "result"]
    assert recent[0]["arguments"]["password"] == "***REDACTED***"
    assert "hunter2" not in path.read_text()
    assert [e["seq"] for e in audit.search_logs(action="list_extracts")] == [1, 2]
    assert verify_audit_log(path).ok


def test_hash_survives_a_json_round_trip(tmp_path):
    """Non-ASCII text, floats, big ints, nesting and tuples must hash the same once re-read."""
    path = tmp_path / "audit.log"
    AuditLogger(str(path)).log_action(
        action="tëst",
        arguments={
            "name": "Zoë ☃ 日本",
            "lag": 0.1 + 0.2,
            "big": 2**70,
            "nested": {"a": [1, (2, 3), None, True]},
        },
    )

    result = verify_audit_log(path)
    assert result.ok, result.error


def test_concurrent_writers_keep_one_chain(tmp_path):
    path = tmp_path / "audit.log"
    audit = AuditLogger(str(path))

    def work():
        for i in range(25):
            audit.log_action(action=f"a{i}", arguments={})

    threads = [threading.Thread(target=work) for _ in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    result = verify_audit_log(path)
    assert result.ok, result.error
    assert result.entries == 200


# ---- Recovery and rotation ----------------------------------------------------

def test_chain_continues_after_restart(tmp_path):
    path = tmp_path / "audit.log"
    _log_some(AuditLogger(str(path)), 3)
    _log_some(_restart(path), 2)

    assert [e["seq"] for e in _entries(path)] == [1, 2, 3, 4, 5]
    result = verify_audit_log(path)
    assert result.ok, result.error
    assert result.entries == 5


def test_entries_written_before_chaining_are_tolerated(tmp_path):
    path = tmp_path / "audit.log"
    legacy = (
        '2026-03-27 00:32:49 - {"event_type": "action", '
        '"timestamp": "2026-03-27T05:32:49", "action": "list_extracts", "arguments": {}}'
    )
    _rewrite(path, [legacy, legacy])

    _log_some(AuditLogger(str(path)), 2)

    result = verify_audit_log(path)
    assert result.ok, result.error
    assert (result.legacy_entries, result.entries, result.first_seq) == (2, 2, 1)


def test_chain_spans_rotated_files(tmp_path):
    path = tmp_path / "audit.log"
    _log_some(AuditLogger(str(path), max_bytes=800, backup_count=50), 40)

    assert (tmp_path / "audit.log.1").exists()
    result = verify_audit_log(path)
    assert result.ok, result.error
    assert (result.entries, result.first_seq, result.last_seq) == (40, 1, 40)


def test_dropped_rotated_files_leave_a_verifiable_tail(tmp_path):
    path = tmp_path / "audit.log"
    _log_some(AuditLogger(str(path), max_bytes=800, backup_count=1), 40)

    result = verify_audit_log(path)
    assert result.ok, result.error
    assert result.first_seq > 1
    assert result.last_seq == 40
    assert result.entries == result.last_seq - result.first_seq + 1


def test_restart_recovers_head_from_rotated_file_when_current_is_empty(tmp_path):
    path = tmp_path / "audit.log"
    _log_some(AuditLogger(str(path), max_bytes=800, backup_count=50), 10)
    path.write_text("")

    _log_some(_restart(path, max_bytes=800, backup_count=50), 1)

    rotated_tail = _entries(tmp_path / "audit.log.1")[-1]
    (new,) = _entries(path)
    assert (new["seq"], new["prev_hash"]) == (rotated_tail["seq"] + 1, rotated_tail["hash"])
    assert verify_audit_log(path).ok


def test_restart_skips_an_unparseable_trailing_line(log5):
    fifth = _entries(log5)[-1]
    with log5.open("a") as f:
        f.write("garbage from a torn write\n")

    _log_some(_restart(log5), 1)

    new = _entry(log5.read_text().splitlines()[-1])
    assert (new["seq"], new["prev_hash"]) == (6, fifth["hash"])
    # ...but verification still flags the garbage rather than papering over it.
    assert verify_audit_log(log5).error == "audit.log:6: not a valid audit entry"


# ---- Verification -------------------------------------------------------------

def test_verify_reports_the_head(log5):
    result = verify_audit_log(log5)

    assert result.ok, result.error
    assert (result.entries, result.first_seq, result.last_seq) == (5, 1, 5)
    assert result.head_hash == _entries(log5)[-1]["hash"]


def test_empty_log_verifies(tmp_path):
    path = tmp_path / "audit.log"
    AuditLogger(str(path))

    result = verify_audit_log(path)
    assert result.ok
    assert result.entries == 0
    assert result.head_hash is None


def test_missing_log_does_not_verify(tmp_path):
    result = verify_audit_log(tmp_path / "nope.log")

    assert not result.ok
    assert "no audit log found" in result.error


def test_detects_altered_entry(log5):
    lines = log5.read_text().splitlines()
    lines[2] = lines[2].replace("list_extracts_2", "list_extracts_X")
    _rewrite(log5, lines)

    result = verify_audit_log(log5)
    assert not result.ok
    assert result.error == "audit.log:3: hash mismatch, entry 3 was altered"
    assert result.entries == 2


def test_detects_deleted_entry(log5):
    lines = log5.read_text().splitlines()
    del lines[2]
    _rewrite(log5, lines)

    result = verify_audit_log(log5)
    assert not result.ok
    assert result.error.startswith("audit.log:3: expected seq 3, found 4")


def test_detects_reordered_entries(log5):
    lines = log5.read_text().splitlines()
    lines[1], lines[2] = lines[2], lines[1]
    _rewrite(log5, lines)

    result = verify_audit_log(log5)
    assert not result.ok
    assert result.error.startswith("audit.log:2: expected seq 2, found 3")


def test_detects_altered_entry_even_when_its_own_hash_is_fixed_up(log5):
    lines = log5.read_text().splitlines()
    forged = _entry(lines[1])
    forged["action"] = "forged"
    forged["hash"] = _expected_hash(forged)
    lines[1] = lines[1].split(" - ", 1)[0] + " - " + json.dumps(forged)
    _rewrite(log5, lines)

    result = verify_audit_log(log5)
    assert not result.ok
    assert result.error == "audit.log:3: prev_hash does not match the preceding entry"


def test_detects_unchained_entry_after_the_chain_started(log5):
    with log5.open("a") as f:
        f.write('2026-03-27 00:32:49 - {"event_type": "action", "action": "forged"}\n')

    result = verify_audit_log(log5)
    assert not result.ok
    assert result.error == "audit.log:6: unchained entry after the chain started"


def test_detects_malformed_chain_fields(log5):
    lines = log5.read_text().splitlines()
    broken = _entry(lines[1])
    broken["seq"] = "2"
    lines[1] = lines[1].split(" - ", 1)[0] + " - " + json.dumps(broken)
    _rewrite(log5, lines)

    result = verify_audit_log(log5)
    assert not result.ok
    assert result.error == "audit.log:2: malformed chain fields"


def test_detects_a_chain_that_restarts_at_genesis_mid_log(tmp_path):
    path = tmp_path / "audit.log"
    _log_some(AuditLogger(str(path)), 3)
    stale = path.read_text().splitlines()[0]  # seq 1 again, valid on its own
    with path.open("a") as f:
        f.write(stale + "\n")

    result = verify_audit_log(path)
    assert not result.ok
    assert result.error.startswith("audit.log:4: expected seq 4, found 1")
