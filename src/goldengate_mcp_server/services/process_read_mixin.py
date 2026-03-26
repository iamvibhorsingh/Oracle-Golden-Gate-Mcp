"""Read-only Extract / Replicat calls."""

from __future__ import annotations

from typing import Any, Dict

from ..models import build_process_lag_payload


class ProcessReadMixin:
    """Requires: _get_client, metrics_store."""

    metrics_store: Any

    async def _list_extracts(self, deployment: str) -> Dict[str, Any]:
        client = self._get_client(deployment)
        return await client.list_extracts()

    async def _list_replicats(self, deployment: str) -> Dict[str, Any]:
        client = self._get_client(deployment)
        return await client.list_replicats()

    async def _get_extract_status(self, deployment: str, extract_name: str) -> Dict[str, Any]:
        client = self._get_client(deployment)
        return await client.get_extract_status(extract_name)

    async def _get_replicat_status(self, deployment: str, replicat_name: str) -> Dict[str, Any]:
        client = self._get_client(deployment)
        return await client.get_replicat_status(replicat_name)

    async def _get_extract_lag(self, deployment: str, extract_name: str) -> Dict[str, Any]:
        client = self._get_client(deployment)
        raw = await client.get_extract_lag(extract_name)
        return build_process_lag_payload(
            deployment, extract_name, "extract", raw, self.metrics_store
        )

    async def _get_replicat_lag(self, deployment: str, replicat_name: str) -> Dict[str, Any]:
        client = self._get_client(deployment)
        raw = await client.get_replicat_lag(replicat_name)
        return build_process_lag_payload(
            deployment, replicat_name, "replicat", raw, self.metrics_store
        )

    async def _get_process_statistics(
        self, deployment: str, process_type: str, process_name: str
    ) -> Dict[str, Any]:
        client = self._get_client(deployment)
        return await client.get_process_statistics(process_type, process_name)
