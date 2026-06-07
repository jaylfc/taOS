"""
agent_bridge.py — TinyAgentOS agent-bridge daemon.

Runs inside Docker containers alongside streamed apps.
Exposes an HTTP API on port 9100 for host-side control.
"""

from __future__ import annotations

import asyncio
import base64
import os
import shlex
from pathlib import Path
from typing import Any

from fastapi import FastAPI
from pydantic import BaseModel


# ---------------------------------------------------------------------------
# Request / response models
# ---------------------------------------------------------------------------

class ExecRequest(BaseModel):
    command: str
    timeout: int = 30


class FileListRequest(BaseModel):
    path: str


class FileReadRequest(BaseModel):
    path: str


class FileWriteRequest(BaseModel):
    path: str
    content: str


class FileBatchRequest(BaseModel):
    command: str
    timeout: int = 30


class KeyboardRequest(BaseModel):
    keys: str


class MouseRequest(BaseModel):
    x: int
    y: int
    button: int = 1


class TypeRequest(BaseModel):
    text: str


class ComputerUseRequest(BaseModel):
    enabled: bool


class AgentSwapRequest(BaseModel):
    agent_name: str
    agent_type: str


class McpToolRequest(BaseModel):
    tool: str
    args: dict[str, Any] = {}


# ---------------------------------------------------------------------------
# Factory
# ---------------------------------------------------------------------------

def create_bridge_app(
    app_id: str | None = None,
    mcp_server: str | None = None,
) -> FastAPI:
    """Return a configured FastAPI bridge app."""

    resolved_app_id = app_id or os.environ.get("TAOS_APP_ID", "unknown")
    resolved_mcp = mcp_server or os.environ.get("TAOS_MCP_SERVER", "")
    resolved_agent_name = os.environ.get("TAOS_AGENT_NAME", "default")
    resolved_agent_type = os.environ.get("TAOS_AGENT_TYPE", "general")

    state: dict[str, Any] = {
        "app_id": resolved_app_id,
        "mcp_server": resolved_mcp,
        "agent_name": resolved_agent_name,
        "agent_type": resolved_agent_type,
        "computer_use": False,
    }

    bridge = FastAPI(title="TinyAgentOS Agent Bridge", version="1.0.0")

    # -----------------------------------------------------------------------
    # Health
    # -----------------------------------------------------------------------

    @bridge.get("/health")
    async def health():
        return {
            "status": "ok",
            "app_id": state["app_id"],
            "mcp_server": state["mcp_server"],
            "computer_use": state["computer_use"],
        }

    # -----------------------------------------------------------------------
    # MCP stubs
    # -----------------------------------------------------------------------

    @bridge.get("/mcp/capabilities")
    async def mcp_capabilities():
        return {
            "mcp_server": state["mcp_server"],
            "capabilities": [],
        }

    @bridge.post("/mcp/tool")
    async def mcp_tool(req: McpToolRequest):
        return {
            "status": "not_connected",
            "tool": req.tool,
            "args": req.args,
        }

    # -----------------------------------------------------------------------
    # Exec
    # -----------------------------------------------------------------------

    @bridge.post("/exec")
    async def exec_command(req: ExecRequest):
        # SECURITY: shlex.split prevents shell-metachar injection (;, |, $()) by running
        # the command without a shell. Single commands with arguments work as before.
        # Shell features (pipes, redirects) are intentionally NOT supported.
        # NOTE: This endpoint must be auth-gated — it runs arbitrary commands inside
        # the container. Verify /exec requires authentication before exposing externally.
        try:
            proc = await asyncio.create_subprocess_exec(
                *shlex.split(req.command),
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            try:
                stdout, stderr = await asyncio.wait_for(
                    proc.communicate(), timeout=req.timeout
                )
            except asyncio.TimeoutError:
                proc.kill()
                await proc.communicate()
                return {
                    "exit_code": -1,
                    "stdout": "",
                    "stderr": f"Command timed out after {req.timeout}s",
                }
            return {
                "exit_code": proc.returncode,
                "stdout": stdout.decode(errors="replace"),
                "stderr": stderr.decode(errors="replace"),
            }
        except Exception as exc:
            return {"exit_code": -1, "stdout": "", "stderr": str(exc)}

    # -----------------------------------------------------------------------
    # Files
    # -----------------------------------------------------------------------

    @bridge.post("/files/list")
    async def files_list(req: FileListRequest):
        try:
            p = Path(req.path)
            entries = []
            for child in p.iterdir():
                try:
                    stat = child.stat()
                    size = stat.st_size if child.is_file() else 0
                except OSError:
                    size = 0
                entries.append({
                    "name": child.name,
                    "is_dir": child.is_dir(),
                    "size": size,
                })
            return {"entries": entries}
        except Exception as exc:
            return {"entries": [], "error": str(exc)}

    @bridge.post("/files/read")
    async def files_read(req: FileReadRequest):
        try:
            content = Path(req.path).read_text(errors="replace")
            return {"content": content}
        except Exception as exc:
            return {"content": "", "error": str(exc)}

    @bridge.post("/files/write")
    async def files_write(req: FileWriteRequest):
        try:
            p = Path(req.path)
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(req.content)
            return {"status": "ok", "path": str(p)}
        except Exception as exc:
            return {"status": "error", "error": str(exc)}

    @bridge.post("/files/batch")
    async def files_batch(req: FileBatchRequest):
        """Batch file ops via exec — delegates single commands with arguments.
        Shell features (pipes, redirects) are NOT supported; use /exec for those.
        # SECURITY: shlex.split without shell=True prevents metachar injection.
        # NOTE: Must be auth-gated — runs arbitrary commands inside the container.
        """
        try:
            proc = await asyncio.create_subprocess_exec(
                *shlex.split(req.command),
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            try:
                stdout, stderr = await asyncio.wait_for(
                    proc.communicate(), timeout=req.timeout
                )
            except asyncio.TimeoutError:
                proc.kill()
                await proc.communicate()
                return {
                    "exit_code": -1,
                    "stdout": "",
                    "stderr": f"Command timed out after {req.timeout}s",
                }
            return {
                "exit_code": proc.returncode,
                "stdout": stdout.decode(errors="replace"),
                "stderr": stderr.decode(errors="replace"),
            }
        except Exception as exc:
            return {"exit_code": -1, "stdout": "", "stderr": str(exc)}

    # -----------------------------------------------------------------------
    # Visual
    # -----------------------------------------------------------------------

    @bridge.get("/screenshot")
    async def screenshot():
        try:
            proc = await asyncio.create_subprocess_exec(
                "scrot", "-o", "/tmp/screenshot.png",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=10)
            if proc.returncode != 0:
                return {
                    "status": "error",
                    "error": stderr.decode(errors="replace").strip() or "scrot failed",
                }
            data = Path("/tmp/screenshot.png").read_bytes()
            return {
                "status": "ok",
                "image": base64.b64encode(data).decode(),
                "mime": "image/png",
            }
        except FileNotFoundError:
            return {"status": "error", "error": "scrot not found"}
        except asyncio.TimeoutError:
            return {"status": "error", "error": "screenshot timed out"}
        except Exception as exc:
            return {"status": "error", "error": str(exc)}

    @bridge.post("/keyboard")
    async def keyboard(req: KeyboardRequest):
        try:
            proc = await asyncio.create_subprocess_exec(
                "xdotool", "key", req.keys,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=10)
            if proc.returncode != 0:
                return {
                    "status": "error",
                    "error": stderr.decode(errors="replace").strip() or "xdotool failed",
                }
            return {"status": "ok"}
        except FileNotFoundError:
            return {"status": "error", "error": "xdotool not found"}
        except Exception as exc:
            return {"status": "error", "error": str(exc)}

    @bridge.post("/mouse")
    async def mouse(req: MouseRequest):
        try:
            _ALLOWED_BUTTONS = {1, 2, 3, 4, 5}
            if req.button not in _ALLOWED_BUTTONS:
                return {"status": "error", "error": f"Invalid button: {req.button}"}
            proc = await asyncio.create_subprocess_exec(
                "xdotool", "mousemove", str(req.x), str(req.y),
                "click", str(req.button),
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=10)
            if proc.returncode != 0:
                return {
                    "status": "error",
                    "error": stderr.decode(errors="replace").strip() or "xdotool failed",
                }
            return {"status": "ok"}
        except FileNotFoundError:
            return {"status": "error", "error": "xdotool not found"}
        except Exception as exc:
            return {"status": "error", "error": str(exc)}

    @bridge.post("/type")
    async def type_text(req: TypeRequest):
        try:
            proc = await asyncio.create_subprocess_exec(
                "xdotool", "type", "--clearmodifiers", "--", req.text,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=10)
            if proc.returncode != 0:
                return {
                    "status": "error",
                    "error": stderr.decode(errors="replace").strip() or "xdotool failed",
                }
            return {"status": "ok"}
        except FileNotFoundError:
            return {"status": "error", "error": "xdotool not found"}
        except Exception as exc:
            return {"status": "error", "error": str(exc)}

    # -----------------------------------------------------------------------
    # Session
    # -----------------------------------------------------------------------

    @bridge.get("/computer-use")
    async def computer_use_get():
        return {"enabled": state["computer_use"]}

    @bridge.post("/computer-use")
    async def computer_use_set(req: ComputerUseRequest):
        state["computer_use"] = req.enabled
        return {"enabled": state["computer_use"]}

    @bridge.get("/agent/current")
    async def agent_current():
        return {
            "agent_name": state["agent_name"],
            "agent_type": state["agent_type"],
        }

    @bridge.post("/agent/swap")
    async def agent_swap(req: AgentSwapRequest):
        state["agent_name"] = req.agent_name
        state["agent_type"] = req.agent_type
        return {
            "agent_name": state["agent_name"],
            "agent_type": state["agent_type"],
        }

    return bridge


# ---------------------------------------------------------------------------
# Module-level app for `uvicorn tinyagentos.agent_bridge:app`
# ---------------------------------------------------------------------------

app = create_bridge_app()
