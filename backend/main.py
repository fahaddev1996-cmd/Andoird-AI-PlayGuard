"""
PlayGuard AI — FastAPI Backend
Thin HTTP layer that triggers the autonomous ReAct agent and streams results.
"""

import os, tempfile
from pathlib import Path
from datetime import datetime

from fastapi import FastAPI, UploadFile, File, HTTPException, Form
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, FileResponse
from fastapi.staticfiles import StaticFiles
import uvicorn

from agent import run_agent

app = FastAPI(title="PlayGuard AI Agent", version="2.0.0")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])

FRONTEND_DIR = Path(__file__).parent.parent / "frontend"
if FRONTEND_DIR.exists():
    app.mount("/static", StaticFiles(directory=str(FRONTEND_DIR)), name="static")


@app.get("/", response_class=HTMLResponse)
async def serve_frontend():
    index = FRONTEND_DIR / "index.html"
    if index.exists():
        return FileResponse(str(index))
    return HTMLResponse("<h1>PlayGuard AI</h1>")


@app.get("/health")
async def health():
    return {"status": "ok", "version": "2.0.0", "architecture": "ReAct Agent + MCP Tools"}


@app.get("/api/mcp/tools")
async def mcp_tools():
    """List tools the MCP server exposes — proves agentic tool use."""
    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client
    import sys
    params = StdioServerParameters(
        command=sys.executable,
        args=[str(Path(__file__).parent / "mcp_server" / "server.py")]
    )
    try:
        async with stdio_client(params) as (r, w):
            async with ClientSession(r, w) as session:
                await session.initialize()
                tools = await session.list_tools()
                return {
                    "mcp_server": "playguard-agent-tools",
                    "tools": [{"name": t.name, "description": t.description} for t in tools.tools]
                }
    except Exception as e:
        raise HTTPException(500, str(e))


@app.post("/api/analyze")
async def analyze(
    file: UploadFile = File(...),
    app_features: str = Form(default=""),
    has_ads: bool = Form(default=False),
    has_payments: bool = Form(default=False),
    has_account: bool = Form(default=True),
):
    """
    Main endpoint — starts the autonomous ReAct agent.
    Agent calls MCP tools in a loop until it has a complete
    Play Store submission readiness report.
    """
    ext = Path(file.filename).suffix.lower()
    if ext not in [".apk", ".aab"]:
        raise HTTPException(400, f"Only .apk or .aab files accepted, got '{ext}'")

    with tempfile.NamedTemporaryFile(delete=False, suffix=ext) as tmp:
        content = await file.read()
        if len(content) > 200 * 1024 * 1024:
            raise HTTPException(413, "File too large — max 200MB")
        tmp.write(content)
        tmp_path = tmp.name

    try:
        result = await run_agent(
            filepath=tmp_path,
            app_features=app_features,
            has_ads=has_ads,
            has_payments=has_payments,
            has_account=has_account,
        )
        result["timestamp"] = datetime.now().isoformat()
        result["app_info"]["filename"] = file.filename
        result["app_info"]["file_size_mb"] = round(len(content) / 1024 / 1024, 2)
        return result
    finally:
        os.unlink(tmp_path)


if __name__ == "__main__":
    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=True)
