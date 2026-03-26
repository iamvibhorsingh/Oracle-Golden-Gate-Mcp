"""Deployment discovery and metadata."""

from __future__ import annotations

import logging
from typing import Any, Dict

logger = logging.getLogger(__name__)


class DeploymentMixin:
    """Requires: clients (from ServerCoreMixin)."""

    clients: Dict[str, Any]

    async def _list_deployments(self) -> Dict[str, Any]:
        deployments = []
        for name, client in self.clients.items():
            try:
                info = await client.get_deployment_info()
                deployments.append({
                    "name": name,
                    "status": "connected",
                    "version": info.get("version", "unknown"),
                    "base_url": client.base_url,
                })
            except Exception as e:
                deployments.append({
                    "name": name,
                    "status": "error",
                    "error": str(e),
                    "base_url": client.base_url,
                })

        return {"deployments": deployments}

    async def _get_deployment_info(self, deployment: str) -> Dict[str, Any]:
        client = self._get_client(deployment)
        return await client.get_deployment_info()

    async def _list_services(self, deployment: str) -> Dict[str, Any]:
        client = self._get_client(deployment)
        return await client.list_services()
