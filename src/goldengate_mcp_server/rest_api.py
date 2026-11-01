"""
Optional FastAPI REST wrapper for the GoldenGate MCP Server.

Calls go through the same gateway as MCP (`GoldenGateMCPServer.execute_tool`), so policy,
audit and metrics apply equally.

Authentication: set GG_REST_API_KEYS (or GG_REST_API_KEYS_FILE) to a JSON list of callers:

    [{"name": "alice", "token_sha256": "<hex>", "role": "operator", "deployments": ["prod"]}]

`role` is viewer, operator or admin; leave out `deployments` for all of them. Each caller is
named in the audit log. GG_REST_API_TOKEN (or GG_REST_API_TOKEN_FILE) still works as a single
shared admin token. The server binds to 127.0.0.1 by default and refuses to bind to any other
interface without either, unless --allow-unauthenticated is passed (e.g. when an API gateway
in front already enforces auth).

Install: pip install goldengate-mcp-server[azure]
Run: GG_REST_API_TOKEN=... goldengate-rest-server --host 0.0.0.0 --port 8000
"""

import hashlib
import hmac
import ipaddress
import json
import logging
import os
import sys
from contextlib import asynccontextmanager
from functools import lru_cache
from typing import Annotated, Optional, Tuple

try:
    import uvicorn
    from fastapi import Depends, FastAPI, HTTPException, Response
    from fastapi.responses import PlainTextResponse
    from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
    from pydantic import BaseModel
except ImportError:
    print("FastAPI dependencies not found. Install with: pip install goldengate-mcp-server[azure]")
    sys.exit(1)

from dotenv import load_dotenv

from .config import Config, env_secret
from .policy import Principal, Role
from .redaction import redact_sensitive_data
from .server import GoldenGateMCPServer
from .telemetry import configure_logging

logger = logging.getLogger("rest_api")

# Global server instance
mcp_server = None


@asynccontextmanager
async def lifespan(_app: FastAPI):
    global mcp_server
    logger.info("Initializing MCP Server core logic...")
    load_dotenv()
    config = Config.from_env()
    configure_logging(config.log_level, config.log_format)
    _load_keys()  # A malformed key list should stop startup, not fail every request.
    mcp_server = GoldenGateMCPServer(config)
    yield
    await mcp_server.shutdown()


app = FastAPI(
    title="Oracle GoldenGate Enterprise REST API (MCP Wrapper)",
    description="REST wrapper exposing GoldenGate MCP tools for Azure Copilot and Semantic Kernel.",
    version="1.2.0",
    lifespan=lifespan,
)

_bearer = HTTPBearer(auto_error=False)


@lru_cache(maxsize=4)
def _parse_keys(raw: str) -> Tuple[Tuple[str, Principal], ...]:
    """``(token_sha256, principal)`` pairs from a GG_REST_API_KEYS JSON document."""
    keys = []
    for entry in json.loads(raw):
        digest = entry["token_sha256"].lower()
        if len(digest) != 64 or set(digest) - set("0123456789abcdef"):
            raise ValueError(f"token_sha256 of {entry['name']} is not a SHA-256 hex digest")
        deployments = entry.get("deployments")
        keys.append((
            digest,
            Principal(
                name=entry["name"],
                role=Role(entry.get("role", "viewer")),
                deployments=None if deployments is None else frozenset(deployments),
            ),
        ))
    return tuple(keys)


def _load_keys() -> Tuple[Tuple[str, Principal], ...]:
    raw = env_secret("GG_REST_API_KEYS")
    return _parse_keys(raw) if raw else ()


def _auth_configured() -> bool:
    return bool(env_secret("GG_REST_API_KEYS") or env_secret("GG_REST_API_TOKEN"))


def authenticate(
    credentials: Annotated[Optional[HTTPAuthorizationCredentials], Depends(_bearer)],
) -> Principal:
    """The caller behind this request (read per request so tests can set the env)."""
    supplied = credentials.credentials if credentials else ""

    keys = _load_keys()
    shared = env_secret("GG_REST_API_TOKEN")
    if not keys and not shared:
        return Principal(name="anonymous")

    # Check every key so the time taken doesn't reveal which one nearly matched.
    digest = hashlib.sha256(supplied.encode()).hexdigest()
    matched = None
    for key_digest, principal in keys:
        if hmac.compare_digest(digest, key_digest):
            matched = principal
    if matched:
        return matched
    if shared and hmac.compare_digest(supplied.encode(), shared.encode()):
        return Principal(name="shared-token")

    raise HTTPException(
        status_code=401,
        detail="Invalid or missing bearer token",
        headers={"WWW-Authenticate": "Bearer"},
    )


CurrentPrincipal = Annotated[Principal, Depends(authenticate)]


class ToolRequest(BaseModel):
    name: str
    arguments: dict = {}


@app.get("/api/health")
async def health_check():
    return {"status": "healthy"}


@app.get("/api/ready")
async def readiness_check():
    """Ready once the server has started; GoldenGate reachability is per deployment."""
    if mcp_server is None:
        raise HTTPException(status_code=503, detail="Server is still starting")
    return {"status": "ready"}


@app.get("/metrics", response_class=PlainTextResponse)
async def metrics(_principal: CurrentPrincipal):
    if mcp_server is None:
        raise HTTPException(status_code=503, detail="Server is still starting")
    return mcp_server.render_metrics()


@app.get("/api/tools")
async def get_tools(principal: CurrentPrincipal):
    if mcp_server is None:
        raise HTTPException(status_code=503, detail="Server is still starting")
    # Same filtering as MCP list_tools (metrics setting, read-only mode), plus the caller's role.
    tools = mcp_server.list_tool_definitions(principal)
    return {"tools": [t.model_dump(exclude_none=True) for t in tools]}


_ERROR_STATUS = {"unknown_tool": 404, "denied": 403, "invalid": 400, "error": 500}


@app.post("/api/tools/execute")
async def execute_tool(request: ToolRequest, principal: CurrentPrincipal, response: Response):
    if mcp_server is None:
        raise HTTPException(status_code=503, detail="Server is still starting")

    outcome = await mcp_server.execute_tool(
        request.name, request.arguments, principal=principal, channel="rest"
    )
    headers = {"X-Correlation-ID": outcome.correlation_id}

    if outcome.status in _ERROR_STATUS:
        raise HTTPException(
            status_code=_ERROR_STATUS[outcome.status],
            detail=redact_sensitive_data(outcome.result),
            headers=headers,
        )

    response.headers.update(headers)
    # Match MCP path: same redaction before returning structured JSON
    return {
        "success": outcome.ok,
        "tool": request.name,
        "correlation_id": outcome.correlation_id,
        "result": redact_sensitive_data(outcome.result),
    }


def _is_loopback(host: str) -> bool:
    if host == "localhost":
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def main():
    import argparse
    parser = argparse.ArgumentParser(description="Run the GoldenGate REST API Wrapper")
    parser.add_argument(
        "--host",
        default="127.0.0.1",
        help="Host interface to bind to (default: 127.0.0.1; non-loopback needs a token)",
    )
    parser.add_argument("--port", type=int, default=8000, help="Port to bind to")
    parser.add_argument(
        "--allow-unauthenticated",
        action="store_true",
        help="Allow binding to a non-loopback interface without GG_REST_API_KEYS or "
        "GG_REST_API_TOKEN (only when something in front of this server enforces auth)",
    )
    args = parser.parse_args()
    load_dotenv()

    if (
        not _is_loopback(args.host)
        and not _auth_configured()
        and not args.allow_unauthenticated
    ):
        parser.error(
            f"Refusing to listen on {args.host} without authentication. "
            "Set GG_REST_API_KEYS or GG_REST_API_TOKEN, bind to 127.0.0.1, "
            "or pass --allow-unauthenticated."
        )

    uvicorn.run("goldengate_mcp_server.rest_api:app", host=args.host, port=args.port, reload=False)


if __name__ == "__main__":
    main()
