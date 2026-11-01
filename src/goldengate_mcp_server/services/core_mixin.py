"""Client access and MCP-safe result formatting."""

from __future__ import annotations

import json
from typing import Any, Dict, List

from ..goldengate_client import GoldenGateClient
from ..policy import current_principal
from ..redaction import redact_sensitive_data


class ServerCoreMixin:
    """Requires: config, clients: Dict[str, GoldenGateClient]."""

    config: Any
    clients: Dict[str, GoldenGateClient]

    def _get_client(self, deployment: str) -> GoldenGateClient:
        if deployment not in self.clients:
            raise ValueError(f"Unknown deployment: {deployment}")
        return self.clients[deployment]

    def _visible_deployments(self) -> List[str]:
        """Deployments the caller of the current tool may see."""
        principal = current_principal.get()
        return [n for n in self.clients if principal is None or principal.can_access(n)]

    def _format_result(self, result: Any) -> str:
        safe = redact_sensitive_data(result)
        return json.dumps(safe, indent=2, default=str)
