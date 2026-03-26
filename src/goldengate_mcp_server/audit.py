"""
Audit logging for GoldenGate MCP Server.

Provides comprehensive audit trail for all operations, supporting
compliance and security requirements.
"""

import json
import logging
from datetime import datetime
from pathlib import Path
from threading import Lock
from typing import Any, Dict, Optional

from .redaction import redact_error_text, redact_sensitive_data


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
    """

    def __init__(self, log_path: str):
        """
        Initialize audit logger.

        Args:
            log_path: Path to audit log file
        """
        self.log_path = Path(log_path)
        self.lock = Lock()

        # Create log directory if it doesn't exist
        self.log_path.parent.mkdir(parents=True, exist_ok=True)

        # Set up file handler
        self.logger = logging.getLogger("audit")
        self.logger.setLevel(logging.INFO)

        # Create file handler
        handler = logging.FileHandler(self.log_path)
        handler.setLevel(logging.INFO)

        # Create formatter
        formatter = logging.Formatter(
            '%(asctime)s - %(message)s',
            datefmt='%Y-%m-%d %H:%M:%S'
        )
        handler.setFormatter(formatter)

        # Add handler if not already added
        if not self.logger.handlers:
            self.logger.addHandler(handler)

    def log_action(
        self,
        action: str,
        arguments: Any,
        timestamp: Optional[datetime] = None,
        user: Optional[str] = None,
        session_id: Optional[str] = None
    ):
        """
        Log an action being performed.

        Args:
            action: Name of the action
            arguments: Action arguments
            timestamp: Action timestamp (defaults to now)
            user: User performing the action
            session_id: Session identifier
        """
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

            self.logger.info(json.dumps(log_entry))

    def log_result(
        self,
        action: str,
        success: bool,
        timestamp: Optional[datetime] = None,
        error: Optional[str] = None,
        user: Optional[str] = None,
        session_id: Optional[str] = None
    ):
        """
        Log the result of an action.

        Args:
            action: Name of the action
            success: Whether the action succeeded
            timestamp: Result timestamp (defaults to now)
            error: Error message if action failed
            user: User who performed the action
            session_id: Session identifier
        """
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

            self.logger.info(json.dumps(log_entry))

    def log_security_event(
        self,
        event_type: str,
        details: Dict[str, Any],
        severity: str = "info",
        timestamp: Optional[datetime] = None
    ):
        """
        Log a security-related event.

        Args:
            event_type: Type of security event
            details: Event details
            severity: Event severity (info, warning, error, critical)
            timestamp: Event timestamp (defaults to now)
        """
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

            self.logger.info(json.dumps(log_entry))

    def log_authentication(
        self,
        deployment: str,
        success: bool,
        username: Optional[str] = None,
        error: Optional[str] = None,
        timestamp: Optional[datetime] = None
    ):
        """
        Log an authentication attempt.

        Args:
            deployment: Deployment being accessed
            success: Whether authentication succeeded
            username: Username used
            error: Error message if failed
            timestamp: Event timestamp (defaults to now)
        """
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

            self.logger.info(json.dumps(log_entry))

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
        """
        Get recent audit log entries.

        Args:
            count: Number of entries to retrieve

        Returns:
            List of log entries
        """
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
