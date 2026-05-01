"""Trails and batch process operations."""

from __future__ import annotations

from typing import Any, Dict, List

from ..config import Config


class OperationalMixin:
    """Requires: config, _get_client."""

    config: Config

    async def _list_trails(self, deployment: str) -> Dict[str, Any]:
        client = self._get_client(deployment)
        return await client.list_trails()

    async def _get_trail_info(
        self, deployment: str, trail_name: str, trail_path: str = None
    ) -> Dict[str, Any]:
        client = self._get_client(deployment)
        return await client.get_trail_info(trail_name, trail_path=trail_path)

    async def _batch_start_processes(
        self,
        deployment: str,
        process_names: List[str],
        process_type: str,
    ) -> Dict[str, Any]:
        if self.config.read_only:
            return {
                "error": "Batch start blocked",
                "reason": "Server is running in read-only mode",
            }

        client = self._get_client(deployment)
        return await client.batch_start_processes(process_names, process_type)

    async def _batch_stop_processes(
        self,
        deployment: str,
        process_names: List[str],
        process_type: str,
    ) -> Dict[str, Any]:
        if self.config.read_only:
            return {
                "error": "Batch stop blocked",
                "reason": "Server is running in read-only mode",
            }

        client = self._get_client(deployment)
        return await client.batch_stop_processes(process_names, process_type)
