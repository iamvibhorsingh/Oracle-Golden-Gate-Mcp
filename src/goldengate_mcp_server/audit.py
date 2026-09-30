"""JSON audit log for MCP tool actions and results.

Every entry is hash-chained to the one before it: it carries a ``seq`` number, the
``prev_hash`` of its predecessor and its own ``hash`` (SHA-256 over the entry's canonical
JSON, ``seq`` and ``prev_hash`` included). Editing, deleting, inserting or reordering an
entry breaks the chain, and :func:`verify_audit_log` reports where.

That makes the log tamper-*evident*, not tamper-proof: whoever can rewrite the whole file
can recompute every hash, and removing entries from either end leaves a valid, shorter
chain. Record ``head_hash`` from a verification somewhere the writer cannot modify to catch
both.
"""

import hashlib
import json
import logging
from dataclasses import dataclass
from datetime import datetime
from logging.handlers import RotatingFileHandler
from pathlib import Path
from threading import Lock
from typing import Any, Dict, List, Optional, Tuple, Union

from .redaction import redact_error_text, redact_sensitive_data

# prev_hash of the first entry in a brand-new log.
GENESIS_HASH = "0" * 64


def _entry_hash(entry: Dict[str, Any]) -> str:
    """SHA-256 of ``entry``'s canonical JSON, leaving out its own ``hash`` field."""
    body = {k: v for k, v in entry.items() if k != "hash"}
    canonical = json.dumps(body, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _chain_fields(entry: Dict[str, Any]) -> Optional[Tuple[int, str, str]]:
    """``(seq, prev_hash, hash)`` if ``entry`` carries well-formed chain fields."""
    seq, prev_hash, digest = entry.get("seq"), entry.get("prev_hash"), entry.get("hash")
    if type(seq) is int and isinstance(prev_hash, str) and isinstance(digest, str):
        return seq, prev_hash, digest
    return None


def _parse_line(line: str) -> Optional[Dict[str, Any]]:
    """The JSON entry in a ``<asctime> - <json>`` log line, or None if it isn't one."""
    try:
        entry = json.loads(line.split(" - ", 1)[1])
    except (IndexError, ValueError, RecursionError):
        return None
    return entry if isinstance(entry, dict) else None


def _log_files(log_path: Path) -> List[Path]:
    """``log_path`` plus its rotated siblings (``.N`` ... ``.1``), oldest entries first."""
    prefix = log_path.name + "."
    rotated = []
    if log_path.parent.is_dir():
        for p in log_path.parent.iterdir():
            suffix = p.name[len(prefix):]
            if p.name.startswith(prefix) and suffix.isdecimal():
                rotated.append((int(suffix), p))
    files = [p for _, p in sorted(rotated, reverse=True)]
    if log_path.is_file():
        files.append(log_path)
    return files


def _read_chain_head(log_path: Path) -> Tuple[int, str]:
    """``(seq, hash)`` of the newest chained entry on disk, or the genesis pair if none."""
    for file in reversed(_log_files(log_path)):
        head = None
        with open(file, "r", encoding="utf-8", errors="replace") as f:
            for line in f:
                entry = _parse_line(line)
                fields = _chain_fields(entry) if entry else None
                if fields:
                    head = (fields[0], fields[2])
        if head:
            return head
    return 0, GENESIS_HASH


@dataclass(frozen=True)
class ChainVerification:
    """Result of :func:`verify_audit_log`.

    ``last_seq`` and ``head_hash`` identify the newest chained entry checked, and
    ``first_seq`` the oldest (above 1 once rotation has dropped older files). ``legacy_entries``
    counts unchained entries written before hash chaining existed. ``error`` is
    ``"<file>:<line>: <reason>"`` for the first fault when ``ok`` is False.
    """

    ok: bool
    entries: int = 0
    legacy_entries: int = 0
    first_seq: Optional[int] = None
    last_seq: Optional[int] = None
    head_hash: Optional[str] = None
    error: Optional[str] = None


def verify_audit_log(log_path: Union[str, Path]) -> ChainVerification:
    """
    Check the hash chain across ``log_path`` and its rotated files, stopping at the first fault.

    Unchained entries are tolerated only at the start of the log (written before hash
    chaining existed); an unchained or unparseable entry after that is a fault.
    """
    path = Path(log_path)
    files = _log_files(path)
    if not files:
        return ChainVerification(ok=False, error=f"{path}: no audit log found")

    entries = legacy = 0
    first_seq: Optional[int] = None
    prev: Optional[Tuple[int, str]] = None  # (seq, hash) of the last verified entry

    def fault(where: str, reason: str) -> ChainVerification:
        return ChainVerification(
            ok=False,
            entries=entries,
            legacy_entries=legacy,
            first_seq=first_seq,
            last_seq=prev[0] if prev else None,
            head_hash=prev[1] if prev else None,
            error=f"{where}: {reason}",
        )

    for file in files:
        with open(file, "r", encoding="utf-8", errors="replace") as f:
            for lineno, line in enumerate(f, 1):
                if not line.strip():
                    continue
                where = f"{file.name}:{lineno}"

                entry = _parse_line(line)
                if entry is None:
                    return fault(where, "not a valid audit entry")
                if "hash" not in entry:
                    if prev is not None:
                        return fault(where, "unchained entry after the chain started")
                    legacy += 1
                    continue

                fields = _chain_fields(entry)
                if fields is None:
                    return fault(where, "malformed chain fields")
                seq, prev_hash, digest = fields
                if _entry_hash(entry) != digest:
                    return fault(where, f"hash mismatch, entry {seq} was altered")

                if prev is None:
                    # A chain that starts at genesis must start at 1; otherwise older files
                    # were rotated away and this entry anchors what is left.
                    if prev_hash == GENESIS_HASH and seq != 1:
                        return fault(where, f"chain starts at genesis but has seq {seq}")
                    first_seq = seq
                else:
                    if seq != prev[0] + 1:
                        return fault(
                            where,
                            f"expected seq {prev[0] + 1}, found {seq} "
                            "(entries removed or reordered)",
                        )
                    if prev_hash != prev[1]:
                        return fault(where, "prev_hash does not match the preceding entry")

                prev = (seq, digest)
                entries += 1

    return ChainVerification(
        ok=True,
        entries=entries,
        legacy_entries=legacy,
        first_seq=first_seq,
        last_seq=prev[0] if prev else None,
        head_hash=prev[1] if prev else None,
    )


class AuditLogger:
    """
    Audit logger for tracking all MCP server operations.

    Logs include:
    - Timestamp
    - Action performed
    - Arguments
    - User/session info
    - Result (success/failure)
    - Error details if applicable
    - Hash chain (``seq``, ``prev_hash``, ``hash``); see the module docstring

    The chain head lives in memory and is recovered from the log files on startup, so it
    continues across restarts and rotation. Use one AuditLogger (one server process) per
    log file; concurrent writers would fork the chain.
    """

    def __init__(
        self,
        log_path: str,
        max_bytes: int = 10 * 1024 * 1024,
        backup_count: int = 5,
    ):
        self.log_path = Path(log_path)
        self.lock = Lock()

        self.log_path.parent.mkdir(parents=True, exist_ok=True)
        self._seq, self._head_hash = _read_chain_head(self.log_path)

        self.logger = logging.getLogger("audit")
        self.logger.setLevel(logging.INFO)

        if not self.logger.handlers:
            # Size-based rotation: audit.log -> audit.log.1 ... audit.log.<backup_count>.
            # max_bytes=0 disables rotation.
            handler = RotatingFileHandler(
                self.log_path,
                maxBytes=max_bytes,
                backupCount=backup_count,
                encoding="utf-8",
            )
            handler.setLevel(logging.INFO)
            handler.setFormatter(logging.Formatter(
                '%(asctime)s - %(message)s',
                datefmt='%Y-%m-%d %H:%M:%S'
            ))
            self.logger.addHandler(handler)

    def _write(self, entry: Dict[str, Any]) -> None:
        """Chain ``entry`` to its predecessor and append it; the caller holds ``self.lock``.

        The hash covers the JSON entry, not the ``asctime`` prefix the formatter adds
        (each entry has its own ``timestamp``).
        """
        entry["seq"] = self._seq + 1
        entry["prev_hash"] = self._head_hash
        entry["hash"] = _entry_hash(entry)
        self.logger.info(json.dumps(entry))
        self._seq, self._head_hash = entry["seq"], entry["hash"]

    def log_action(
        self,
        action: str,
        arguments: Any,
        timestamp: Optional[datetime] = None,
        user: Optional[str] = None,
        session_id: Optional[str] = None
    ):
        if timestamp is None:
            timestamp = datetime.utcnow()

        with self.lock:
            args_in = arguments if isinstance(arguments, dict) else {"value": arguments}
            log_entry = {
                "event_type": "action",
                "timestamp": timestamp.isoformat(),
                "action": action,
                "arguments": self._sanitize_arguments(args_in),
            }

            if user:
                log_entry["user"] = user
            if session_id:
                log_entry["session_id"] = session_id

            self._write(log_entry)

    def log_result(
        self,
        action: str,
        success: bool,
        timestamp: Optional[datetime] = None,
        error: Optional[str] = None,
        user: Optional[str] = None,
        session_id: Optional[str] = None
    ):
        if timestamp is None:
            timestamp = datetime.utcnow()

        with self.lock:
            log_entry = {
                "event_type": "result",
                "timestamp": timestamp.isoformat(),
                "action": action,
                "success": success,
            }

            if error:
                log_entry["error"] = redact_error_text(error)
            if user:
                log_entry["user"] = user
            if session_id:
                log_entry["session_id"] = session_id

            self._write(log_entry)

    def log_security_event(
        self,
        event_type: str,
        details: Dict[str, Any],
        severity: str = "info",
        timestamp: Optional[datetime] = None
    ):
        if timestamp is None:
            timestamp = datetime.utcnow()

        with self.lock:
            log_entry = {
                "event_type": "security",
                "timestamp": timestamp.isoformat(),
                "security_event": event_type,
                "severity": severity,
                "details": redact_sensitive_data(
                    dict(details) if isinstance(details, dict) else {"detail": details}
                ),
            }

            self._write(log_entry)

    def log_authentication(
        self,
        deployment: str,
        success: bool,
        username: Optional[str] = None,
        error: Optional[str] = None,
        timestamp: Optional[datetime] = None
    ):
        if timestamp is None:
            timestamp = datetime.utcnow()

        with self.lock:
            log_entry = {
                "event_type": "authentication",
                "timestamp": timestamp.isoformat(),
                "deployment": deployment,
                "success": success,
            }

            if username:
                log_entry["username"] = username
            if error:
                log_entry["error"] = redact_error_text(error)

            self._write(log_entry)

    @staticmethod
    def _sanitize_arguments(arguments: Any) -> Any:
        """
        Sanitize arguments to remove sensitive data from logs (nested dicts included).
        """
        if arguments is None:
            return None
        if isinstance(arguments, dict):
            return redact_sensitive_data(dict(arguments))
        return arguments

    def get_recent_entries(self, count: int = 100) -> list:
        entries = []

        try:
            with open(self.log_path, 'r') as f:
                # Read last N lines
                lines = f.readlines()
                for line in lines[-count:]:
                    try:
                        # Parse each line as JSON
                        entry = json.loads(line.split(' - ', 1)[1])
                        entries.append(entry)
                    except (json.JSONDecodeError, IndexError):
                        continue
        except FileNotFoundError:
            pass

        return entries

    def search_logs(
        self,
        start_time: Optional[datetime] = None,
        end_time: Optional[datetime] = None,
        action: Optional[str] = None,
        event_type: Optional[str] = None,
        success: Optional[bool] = None
    ) -> list:
        """
        Search audit logs with filters.

        Args:
            start_time: Filter entries after this time
            end_time: Filter entries before this time
            action: Filter by action name
            event_type: Filter by event type
            success: Filter by success status

        Returns:
            List of matching log entries
        """
        matching_entries = []

        try:
            with open(self.log_path, 'r') as f:
                for line in f:
                    try:
                        # Parse log entry
                        entry = json.loads(line.split(' - ', 1)[1])

                        # Apply filters
                        if start_time:
                            entry_time = datetime.fromisoformat(entry["timestamp"])
                            if entry_time < start_time:
                                continue

                        if end_time:
                            entry_time = datetime.fromisoformat(entry["timestamp"])
                            if entry_time > end_time:
                                continue

                        if action and entry.get("action") != action:
                            continue

                        if event_type and entry.get("event_type") != event_type:
                            continue

                        if success is not None and entry.get("success") != success:
                            continue

                        matching_entries.append(entry)

                    except (json.JSONDecodeError, IndexError, KeyError):
                        continue

        except FileNotFoundError:
            pass

        return matching_entries


# Example usage for testing
if __name__ == "__main__":
    # Create test logger
    logger = AuditLogger("./test_audit.log")

    # Log some test events
    logger.log_action(
        action="list_extracts",
        arguments={"deployment": "gg21_prod"}
    )

    logger.log_result(
        action="list_extracts",
        success=True
    )

    logger.log_security_event(
        event_type="write_operation_blocked",
        details={
            "operation": "start_extract",
            "reason": "read_only_mode"
        },
        severity="warning"
    )

    # Get recent entries
    recent = logger.get_recent_entries(count=10)
    print(f"Recent entries: {len(recent)}")
    for entry in recent:
        print(json.dumps(entry, indent=2))
