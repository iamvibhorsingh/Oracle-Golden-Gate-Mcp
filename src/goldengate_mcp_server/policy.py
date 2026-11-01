"""Who may call which tool, on which deployment.

Every tool call, MCP or REST, passes :meth:`Policy.check` before its handler runs. Checks
fail closed: a tool this module knows nothing about needs only ``viewer`` because every tool
that changes anything is listed in ``tools.registry`` and tested to be.
"""

from __future__ import annotations

import re
from contextvars import ContextVar
from dataclasses import dataclass
from enum import Enum
from typing import Any, Dict, FrozenSet, Iterable, List, Optional, TypeVar

from .config import Config
from .tools.registry import ADMIN_TOOLS, LOCAL_WRITE_TOOLS, WRITE_TOOL_NAMES

T = TypeVar("T")


class Role(str, Enum):
    VIEWER = "viewer"
    OPERATOR = "operator"
    ADMIN = "admin"


_ROLE_RANK = {Role.VIEWER: 0, Role.OPERATOR: 1, Role.ADMIN: 2}


@dataclass(frozen=True)
class Principal:
    """The caller. ``deployments=None`` means every deployment."""

    name: str
    role: Role = Role.ADMIN
    deployments: Optional[FrozenSet[str]] = None

    def has_role(self, required: Role) -> bool:
        return _ROLE_RANK[self.role] >= _ROLE_RANK[required]

    def can_access(self, deployment: str) -> bool:
        return self.deployments is None or deployment in self.deployments


# Set for the duration of a tool call so fleet-wide tools can scope themselves.
current_principal: ContextVar[Optional[Principal]] = ContextVar("current_principal", default=None)


@dataclass(frozen=True)
class Denial:
    """Why a call was refused; ``code`` is stable, ``reason`` is for humans and assistants."""

    code: str
    reason: str


def required_role(tool: str) -> Role:
    if tool in ADMIN_TOOLS:
        return Role.ADMIN
    if tool in WRITE_TOOL_NAMES or tool in LOCAL_WRITE_TOOLS:
        return Role.OPERATOR
    return Role.VIEWER


class Policy:
    def __init__(self, config: Config):
        self.read_only = config.read_only
        self.write_deployments = frozenset(config.write_deployments)
        self.protected_deployments = frozenset(config.protected_deployments)
        self.disabled_tools = frozenset(config.disabled_tools)
        self.max_batch_size = config.max_batch_size
        self.change_ticket_pattern = config.change_ticket_pattern

    def visible(self, tools: Iterable[T], principal: Principal) -> List[T]:
        """The tools (objects with ``.name``) ``principal`` is allowed to call at all."""
        return [
            t
            for t in tools
            if t.name not in self.disabled_tools
            and principal.has_role(required_role(t.name))
        ]

    def check(
        self, tool: str, arguments: Dict[str, Any], principal: Principal
    ) -> Optional[Denial]:
        """The reason ``principal`` may not make this call, or None if it may."""
        if tool in self.disabled_tools:
            return Denial("tool_disabled", f"{tool} is disabled on this server")

        role = required_role(tool)
        if not principal.has_role(role):
            return Denial(
                "insufficient_role",
                f"{tool} requires the {role.value} role; {principal.name} is "
                f"{principal.role.value}",
            )

        deployment = arguments.get("deployment")
        requested = [deployment] if isinstance(deployment, str) else []
        if isinstance(arguments.get("deployments"), list):
            requested += [str(d) for d in arguments["deployments"]]
        for name in requested:
            if not principal.can_access(name):
                return Denial(
                    "deployment_forbidden", f"{principal.name} has no access to {name}"
                )

        if tool in WRITE_TOOL_NAMES:
            return self._check_write(arguments, deployment)
        return None

    def _check_write(self, arguments: Dict[str, Any], deployment: Any) -> Optional[Denial]:
        if self.read_only:
            return Denial("read_only", "Server is running in read-only mode")

        if self.write_deployments and deployment not in self.write_deployments:
            return Denial(
                "deployment_not_writable",
                f"{deployment} is not in GG_WRITE_DEPLOYMENTS",
            )

        names = arguments.get("process_names")
        if isinstance(names, list) and len(names) > self.max_batch_size:
            return Denial(
                "batch_too_large",
                f"{len(names)} processes exceeds the limit of {self.max_batch_size}",
            )

        # A preview changes nothing, so it can run before a ticket exists.
        if deployment in self.protected_deployments and not arguments.get("dry_run"):
            ticket = arguments.get("change_ticket")
            if not ticket:
                return Denial(
                    "change_ticket_required",
                    f"{deployment} is protected: pass change_ticket",
                )
            if not isinstance(ticket, str) or not re.fullmatch(
                self.change_ticket_pattern, ticket
            ):
                return Denial(
                    "change_ticket_invalid",
                    f"change_ticket does not match {self.change_ticket_pattern}",
                )
        return None
