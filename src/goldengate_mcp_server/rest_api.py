"""
Optional FastAPI REST wrapper for the GoldenGate MCP Server.

Delegates to the same async handlers as MCP (`GoldenGateMCPServer._tool_handlers`).

Authentication: set GG_REST_API_TOKEN and send `Authorization: Bearer <token>` on every
/api/tools* request. The server binds to 127.0.0.1 by default and refuses to bind to any
other interface without a token unless --allow-unauthenticated is passed (e.g. when an
API gateway in front already enforces auth).

Install: pip install goldengate-mcp-server[azure]
Run: GG_REST_API_TOKEN=... goldengate-rest-server --host 0.0.0.0 --port 8000
"""

import hmac
import ipaddress
import logging
import os
import sys
from datetime import datetime
from typing import Annotated, Optional

try:
    import uvicorn
    from fastapi import Depends, FastAPI, HTTPException
    from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
    from pydantic import BaseModel
except ImportError:
    print("FastAPI dependencies not found. Install with: pip install goldengate-mcp-server[azure]")
    sys.exit(1)

from .config import Config
from .redaction import redact_error_text, redact_sensitive_data
from .server import GoldenGateMCPServer

logger = logging.getLogger("rest_api")

app = FastAPI(
    title="Oracle GoldenGate Enterprise REST API (MCP Wrapper)",
    description="REST wrapper exposing GoldenGate MCP tools for Azure Copilot and Semantic Kernel.",
    version="1.0.0"
)

# Global server instance
mcp_server = None

@app.on_event("startup")
async def startup_event():
    global mcp_server
    logger.info("Initializing MCP Server core logic...")
    config = Config.from_env()
    mcp_server = GoldenGateMCPServer(config)

@app.on_event("shutdown")
async def shutdown_event():
    if mcp_server:
        await mcp_server.shutdown()

_bearer = HTTPBearer(auto_error=False)


def require_token(
    credentials: Annotated[Optional[HTTPAuthorizationCredentials], Depends(_bearer)],
) -> None:
    """Enforce GG_REST_API_TOKEN when it is set (read per request so tests can set it)."""
    expected = os.getenv("GG_REST_API_TOKEN")
    if not expected:
        return
    supplied = credentials.credentials if credentials else ""
    if not hmac.compare_digest(supplied.encode(), expected.encode()):
        raise HTTPException(
            status_code=401,
            detail="Invalid or missing bearer token",
            headers={"WWW-Authenticate": "Bearer"},
        )


class ToolRequest(BaseModel):
    name: str
    arguments: dict = {}

@app.get("/api/health")
async def health_check():
    return {"status": "healthy"}

@app.get("/api/tools", dependencies=[Depends(require_token)])
async def get_tools():
    if mcp_server is None:
        raise HTTPException(status_code=503, detail="Server is still starting")
    # Same filtering as MCP list_tools (metrics setting, read-only mode).
    tools = mcp_server.list_tool_definitions()
    return {"tools": [t.model_dump(exclude_none=True) for t in tools]}

@app.post("/api/tools/execute", dependencies=[Depends(require_token)])
async def execute_tool(request: ToolRequest):
    if mcp_server is None:
        raise HTTPException(status_code=503, detail="Server is still starting")

    handler = mcp_server._tool_handlers.get(request.name)
    if not handler:
        raise HTTPException(status_code=404, detail=f"Tool '{request.name}' not found")

    ts = datetime.utcnow()
    try:
        mcp_server.audit_logger.log_action(
            action=f"rest_api_{request.name}",
            arguments=request.arguments,
            timestamp=ts,
        )

        result = await handler(request.arguments)

        ok = not (isinstance(result, dict) and "error" in result)
        mcp_server.audit_logger.log_result(
            action=f"rest_api_{request.name}",
            success=ok,
            timestamp=datetime.utcnow(),
        )
        # Match MCP path: same redaction before returning structured JSON
        return {
            "success": ok,
            "tool": request.name,
            "result": redact_sensitive_data(result),
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.error("Error executing tool %s: %s", request.name, e)
        safe = redact_error_text(str(e))
        mcp_server.audit_logger.log_result(
            action=f"rest_api_{request.name}",
            success=False,
            error=safe,
            timestamp=datetime.utcnow(),
        )
        raise HTTPException(status_code=500, detail=safe) from e


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
        help="Allow binding to a non-loopback interface without GG_REST_API_TOKEN "
        "(only when something in front of this server enforces auth)",
    )
    args = parser.parse_args()

    if (
        not _is_loopback(args.host)
        and not os.getenv("GG_REST_API_TOKEN")
        and not args.allow_unauthenticated
    ):
        parser.error(
            f"Refusing to listen on {args.host} without authentication. "
            "Set GG_REST_API_TOKEN, bind to 127.0.0.1, or pass --allow-unauthenticated."
        )

    uvicorn.run("goldengate_mcp_server.rest_api:app", host=args.host, port=args.port, reload=False)

if __name__ == "__main__":
    main()
