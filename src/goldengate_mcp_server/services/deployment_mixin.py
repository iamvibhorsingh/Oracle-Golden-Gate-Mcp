"""Deployment discovery and metadata."""

from __future__ import annotations

import asyncio
import logging
from typing import Any, Dict

from ..redaction import redact_error_text

logger = logging.getLogger(__name__)


class DeploymentMixin:
    """Requires: clients (from ServerCoreMixin)."""

    clients: Dict[str, Any]

    async def _list_deployments(self) -> Dict[str, Any]:
        async def probe(name: str) -> Dict[str, Any]:
            client = self.clients[name]
            try:
                info = await client.get_deployment_info()
                return {
                    "name": name,
                    "status": "connected",
                    "version": info.get("version", "unknown"),
                    "base_url": client.base_url,
                }
            except Exception as e:
                return {
                    "name": name,
                    "status": "error",
                    "error": redact_error_text(str(e)),
                    "base_url": client.base_url,
                }

        names = self._visible_deployments()
        return {"deployments": list(await asyncio.gather(*(probe(n) for n in names)))}

    async def _get_deployment_info(self, deployment: str) -> Dict[str, Any]:
        client = self._get_client(deployment)
        return await client.get_deployment_info()

    async def _list_services(self, deployment: str) -> Dict[str, Any]:
        client = self._get_client(deployment)
        return await client.list_services()
