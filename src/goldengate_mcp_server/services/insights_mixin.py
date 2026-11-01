"""Fleet-wide health and process runtime detail (reports, checkpoints, messages)."""

from __future__ import annotations

import asyncio
from datetime import datetime
from typing import Any, Dict, List, Optional

from ..process_safety import check_start
from ..redaction import redact_error_text
from ..utils import parse_lag_duration, parse_ogg_messages

_FLEET_CONCURRENCY = 20
_MAX_REPORT_ERRORS = 20

_SEVERITY_ORDER = {"critical": 0, "high": 1, "elevated": 2}


def _attention_items(health: Dict[str, Any], lag_threshold: float) -> List[Dict[str, Any]]:
    """Processes in one deployment's health document that need a human's eye."""
    items = []
    for process_type, group in (("extract", "extracts"), ("replicat", "replicats")):
        for process in health.get(group, []):
            status = process.get("status")
            lag = parse_lag_duration((process.get("lag") or {}).get("lag"))
            if status == "running":
                if lag is None or lag < lag_threshold:
                    continue
                severity, reason = "high", f"lag {lag:g}s >= {lag_threshold:g}s"
            elif status == "stopped":
                severity, reason = "elevated", "stopped"
            elif status == "starting":
                continue
            else:
                severity, reason = "critical", f"status {status}"
            items.append({
                "deployment": health["deployment"],
                "process": process.get("name"),
                "process_type": process_type,
                "status": status,
                "lag_seconds": lag,
                "severity": severity,
                "reason": reason,
            })
    return items


class InsightsMixin:
    """Requires: config, _get_client, _get_deployment_health, _visible_deployments."""

    config: Any

    async def _get_fleet_health(
        self,
        deployments: Optional[List[str]] = None,
        lag_threshold_seconds: float = 300,
    ) -> Dict[str, Any]:
        visible = self._visible_deployments()
        if deployments:
            unknown = sorted(set(deployments) - set(visible))
            if unknown:
                return {"error": f"Unknown deployments: {', '.join(unknown)}"}
            visible = [d for d in visible if d in deployments]

        sem = asyncio.Semaphore(_FLEET_CONCURRENCY)
        # One dead host must not hold up the report for the others.
        timeout = self.config.request_timeout * 2

        async def one(name: str) -> Dict[str, Any]:
            async with sem:
                try:
                    return await asyncio.wait_for(self._get_deployment_health(name), timeout)
                except Exception as e:
                    return {
                        "deployment": name,
                        "overall_status": "error",
                        "extracts": [],
                        "replicats": [],
                        "issues": [redact_error_text(str(e) or type(e).__name__)],
                    }

        reports = await asyncio.gather(*(one(name) for name in visible))

        attention = [
            item for report in reports for item in _attention_items(report, lag_threshold_seconds)
        ]
        attention.sort(
            key=lambda i: (_SEVERITY_ORDER[i["severity"]], -(i["lag_seconds"] or 0))
        )

        summary = {
            "deployments": len(reports),
            "healthy": 0,
            "degraded": 0,
            "error": 0,
            "processes": 0,
            "running": 0,
            "needs_attention": len(attention),
        }
        rows = []
        for report in reports:
            summary[report["overall_status"]] += 1
            processes = report["extracts"] + report["replicats"]
            running = sum(1 for p in processes if p.get("status") == "running")
            summary["processes"] += len(processes)
            summary["running"] += running
            rows.append({
                "deployment": report["deployment"],
                "overall_status": report["overall_status"],
                "processes": len(processes),
                "running": running,
                "issues": report["issues"],
            })

        return {
            "timestamp": datetime.utcnow().isoformat(),
            "lag_threshold_seconds": lag_threshold_seconds,
            "summary": summary,
            "attention": attention,
            "deployments": rows,
        }

    async def _get_process_report(
        self,
        deployment: str,
        process_type: str,
        process_name: str,
        tail_lines: int = 200,
        report: Optional[str] = None,
    ) -> Dict[str, Any]:
        client = self._get_client(deployment)
        body = await client.get_process_report(process_type, process_name, report)
        lines = [str(line) for line in body.get("lines", [])]

        messages = parse_ogg_messages(lines)

        return {
            "deployment": deployment,
            "process": process_name,
            "process_type": process_type,
            "report": report or f"{process_name}.rpt",
            "total_lines": len(lines),
            "ogg_messages": messages[-_MAX_REPORT_ERRORS:],
            "lines": lines[-max(1, min(tail_lines, 1000)):],
        }

    async def _check_start_readiness(
        self, deployment: str, process_type: str, process_name: str
    ) -> Dict[str, Any]:
        return await check_start(self._get_client(deployment), process_type, process_name)

    async def _list_distribution_paths(self, deployment: str) -> Dict[str, Any]:
        return await self._get_client(deployment).list_distribution_paths()

    async def _get_distribution_path(self, deployment: str, path_name: str) -> Dict[str, Any]:
        return await self._get_client(deployment).get_distribution_path(path_name)

    async def _get_distribution_path_checkpoints(
        self, deployment: str, path_name: str
    ) -> Dict[str, Any]:
        return await self._get_client(deployment).get_distribution_path_checkpoints(path_name)

    async def _list_data_streams(self, deployment: str) -> Dict[str, Any]:
        return await self._get_client(deployment).list_data_streams()

    async def _get_data_stream(self, deployment: str, stream_name: str) -> Dict[str, Any]:
        return await self._get_client(deployment).get_data_stream(stream_name)

    async def _get_data_stream_definition(
        self, deployment: str, stream_name: str
    ) -> Dict[str, Any]:
        return await self._get_client(deployment).get_data_stream_definition(stream_name)

    async def _get_process_checkpoints(
        self,
        deployment: str,
        process_type: str,
        process_name: str,
        history: int = 0,
    ) -> Dict[str, Any]:
        client = self._get_client(deployment)
        return await client.get_process_checkpoints(process_type, process_name, history)

    async def _get_deployment_messages(
        self, deployment: str, limit: int = 50
    ) -> Dict[str, Any]:
        client = self._get_client(deployment)
        body = await client.get_messages()
        messages = body.get("lines") or body.get("items") or body.get("messages") or []
        return {
            "deployment": deployment,
            "total": len(messages),
            "messages": messages[-max(1, min(limit, 500)):],
        }
