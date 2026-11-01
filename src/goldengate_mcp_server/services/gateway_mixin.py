"""The one path every tool call takes: policy, dry run, execution, audit, metrics."""

from __future__ import annotations

import asyncio
import logging
import time
import uuid
from dataclasses import dataclass
from typing import Any, Dict, Optional

from ..policy import Principal, current_principal
from ..process_safety import blocked_names, check_start
from ..redaction import redact_error_text
from ..tools import START_TOOLS, WRITE_TOOL_NAMES

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class ToolOutcome:
    """``status`` is ok, failed (the tool reported an error), denied, blocked (a pre-start
    check refused), invalid, unknown_tool or error (the tool raised; ``result`` is then the
    redacted message)."""

    status: str
    result: Any
    correlation_id: str

    @property
    def ok(self) -> bool:
        return self.status == "ok"


def _reports_failure(result: Any) -> bool:
    if not isinstance(result, dict):
        return False
    return "error" in result or bool(result.get("failed"))


class GatewayMixin:
    """Requires: audit_logger, policy, telemetry, default_principal, _tool_handlers,
    _tool_schemas, _preview_write."""

    audit_logger: Any
    config: Any
    policy: Any
    telemetry: Any
    default_principal: Principal
    _tool_handlers: Dict[str, Any]
    _tool_schemas: Dict[str, Any]

    async def execute_tool(
        self,
        name: str,
        arguments: Any,
        *,
        principal: Optional[Principal] = None,
        channel: str = "mcp",
    ) -> ToolOutcome:
        principal = principal or self.default_principal
        args = arguments if isinstance(arguments, dict) else {}
        correlation_id = uuid.uuid4().hex
        context = {"correlation_id": correlation_id, "channel": channel}
        if isinstance(args.get("deployment"), str):
            context["deployment"] = args["deployment"]

        self.audit_logger.log_action(
            action=name, arguments=arguments, user=principal.name, context=context
        )
        started = time.perf_counter()

        token = current_principal.set(principal)
        try:
            status, result = await self._dispatch(name, args, principal, context)
        except Exception as e:
            logger.error("Error executing tool %s: %s", name, e, exc_info=True)
            status, result = "error", redact_error_text(str(e))
        finally:
            current_principal.reset(token)

        self.audit_logger.log_result(
            action=name,
            success=status == "ok",
            error=result if status == "error" else None,
            user=principal.name,
            context={**context, "status": status},
        )
        # Unregistered names come from the client: don't let them mint metric labels.
        self.telemetry.record_call(
            name if name in self._tool_handlers else "unknown",
            status,
            time.perf_counter() - started,
        )

        if status == "ok" and name in WRITE_TOOL_NAMES and isinstance(result, dict):
            result = {**result, "correlation_id": correlation_id}
        return ToolOutcome(status, result, correlation_id)

    async def _dispatch(
        self,
        name: str,
        args: Dict[str, Any],
        principal: Principal,
        context: Dict[str, Any],
    ) -> tuple[str, Any]:
        handler = self._tool_handlers.get(name)
        if handler is None:
            return "unknown_tool", {"error": f"Unknown tool: {name}"}

        denial = self.policy.check(name, args, principal)
        if denial is not None:
            self._record_refusal(name, denial.code, denial.reason, principal, context)
            return "denied", {
                "error": "Denied by policy",
                "code": denial.code,
                "reason": denial.reason,
                "tool": name,
            }

        required = self._tool_schemas[name].inputSchema.get("required", [])
        missing = [key for key in required if key not in args]
        if missing:
            return "invalid", {"error": f"Missing required arguments: {', '.join(missing)}"}

        dry_run = name in WRITE_TOOL_NAMES and bool(args.get("dry_run"))

        prestart = None
        if name in START_TOOLS and self.config.prestart_checks != "off":
            prestart = await self._prestart_report(name, args)
            if prestart["blocked"] and prestart["enforced"] and not dry_run:
                reason = f"Pre-start checks refused: {', '.join(prestart['blocked'])}"
                self._record_refusal(name, "prestart_check_failed", reason, principal, context)
                return "blocked", {
                    "error": "Start blocked by pre-start checks",
                    "code": "prestart_check_failed",
                    "tool": name,
                    "prestart": prestart,
                }

        result = await (self._preview_write(name, args) if dry_run else handler(args))
        if prestart is not None and isinstance(result, dict):
            result = {**result, "prestart": prestart}
        return ("failed" if _reports_failure(result) else "ok"), result

    async def _prestart_report(self, name: str, args: Dict[str, Any]) -> Dict[str, Any]:
        client = self._get_client(args["deployment"])
        process_type, names = self._write_targets(name, args)
        reports = list(await asyncio.gather(*(check_start(client, process_type, n) for n in names)))
        return {
            "enforced": self.config.prestart_checks == "enforce",
            "blocked": blocked_names(reports),
            "processes": reports,
        }

    def _record_refusal(
        self,
        name: str,
        code: str,
        reason: str,
        principal: Principal,
        context: Dict[str, Any],
    ) -> None:
        self.telemetry.record_denial(code)
        self.audit_logger.log_security_event(
            event_type="tool_denied",
            details={
                **context,
                "tool": name,
                "user": principal.name,
                "code": code,
                "reason": reason,
            },
            severity="warning",
        )
