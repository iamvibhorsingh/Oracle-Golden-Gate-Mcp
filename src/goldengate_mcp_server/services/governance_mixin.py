"""Audit search and verification, and the server's security posture."""

from __future__ import annotations

import asyncio
from dataclasses import asdict
from datetime import datetime, timedelta
from importlib.metadata import PackageNotFoundError, version
from typing import Any, Dict, List, Optional
from urllib.parse import urlsplit

from ..audit import verify_audit_log


def _server_version() -> str:
    try:
        return version("goldengate-mcp-server")
    except PackageNotFoundError:
        return "unknown"


class GovernanceMixin:
    """Requires: config, policy, audit_logger, _visible_deployments."""

    config: Any
    policy: Any
    audit_logger: Any

    async def _search_audit_log(
        self,
        action: Optional[str] = None,
        user: Optional[str] = None,
        deployment: Optional[str] = None,
        event_type: Optional[str] = None,
        success: Optional[bool] = None,
        since_hours: Optional[float] = None,
        limit: int = 100,
    ) -> Dict[str, Any]:
        start_time = None
        if since_hours is not None:
            start_time = datetime.utcnow() - timedelta(hours=since_hours)

        entries = await asyncio.to_thread(
            self.audit_logger.search_logs,
            start_time=start_time,
            action=action,
            user=user,
            deployment=deployment,
            event_type=event_type,
            success=success,
            limit=max(1, min(limit, 1000)),
        )
        return {"count": len(entries), "entries": entries}

    async def _verify_audit_log(self) -> Dict[str, Any]:
        return asdict(await asyncio.to_thread(verify_audit_log, self.audit_logger.log_path))

    def _get_server_posture(self) -> Dict[str, Any]:
        """Effective settings, with anything weaker than a production baseline in findings."""
        config = self.config
        visible = set(self._visible_deployments())
        findings: List[str] = []
        deployments = []

        for dep in config.deployments:
            if dep.name not in visible:
                continue
            scheme = urlsplit(dep.base_url).scheme
            tls_verified = scheme == "https" and bool(dep.verify_ssl or dep.ca_bundle)
            writable = not config.read_only and (
                not config.write_deployments or dep.name in config.write_deployments
            )
            protected = dep.name in config.protected_deployments
            deployments.append({
                "name": dep.name,
                "scheme": scheme,
                "tls_verified": tls_verified,
                "writable": writable,
                "protected": protected,
            })
            if scheme != "https":
                findings.append(f"{dep.name}: credentials are sent over {scheme}")
            elif not tls_verified:
                findings.append(f"{dep.name}: TLS certificate verification is off")
            if writable and not protected:
                findings.append(f"{dep.name}: writable without a change ticket requirement")

        if not config.read_only and not config.write_deployments:
            findings.append("Writes are enabled on every deployment (GG_WRITE_DEPLOYMENTS unset)")

        if config.prestart_checks != "enforce":
            findings.append(f"Pre-start checks are {config.prestart_checks}, not enforced")

        return {
            "version": _server_version(),
            "read_only": config.read_only,
            "metrics_enabled": config.enable_metrics,
            "policy": {
                "write_deployments": sorted(self.policy.write_deployments),
                "protected_deployments": sorted(self.policy.protected_deployments),
                "disabled_tools": sorted(self.policy.disabled_tools),
                "max_batch_size": self.policy.max_batch_size,
                "prestart_checks": config.prestart_checks,
                "change_ticket_pattern": self.policy.change_ticket_pattern,
            },
            "deployments": deployments,
            "findings": findings,
        }
