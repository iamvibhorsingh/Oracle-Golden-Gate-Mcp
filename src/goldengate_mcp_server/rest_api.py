"""
Optional FastAPI REST wrapper for the GoldenGate MCP Server.

Delegates to the same async handlers as MCP (`GoldenGateMCPServer._tool_handlers`).
Expose this only behind your own auth (API gateway, Azure Functions auth, etc.)—there
is no authentication on these routes by default.

Install: pip install goldengate-mcp-server[azure]
Run: goldengate-rest-server --host 0.0.0.0 --port 8000
"""

import logging
import sys
from datetime import datetime

try:
    import uvicorn
    from fastapi import FastAPI, HTTPException
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

class ToolRequest(BaseModel):
    name: str
    arguments: dict = {}

@app.get("/api/health")
async def health_check():
    return {"status": "healthy"}

@app.get("/api/tools")
async def get_tools():
    from .tools import all_tools
    tools = all_tools()
    return {"tools": [t.model_dump() for t in tools]}

@app.post("/api/tools/execute")
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


def main():
    import argparse
    parser = argparse.ArgumentParser(description="Run the GoldenGate REST API Wrapper")
    parser.add_argument("--host", default="0.0.0.0", help="Host interface to bind to")
    parser.add_argument("--port", type=int, default=8000, help="Port to bind to")
    args = parser.parse_args()

    uvicorn.run("goldengate_mcp_server.rest_api:app", host=args.host, port=args.port, reload=False)

if __name__ == "__main__":
    main()
