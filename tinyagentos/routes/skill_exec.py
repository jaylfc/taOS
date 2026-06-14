"""Skill execution runtime.

Exposes assigned skills as HTTP endpoints so deployed agents can discover and
invoke them at runtime. Each built-in skill maps to an in-process implementation
function; agents first hit ``GET /api/skill-exec/tools`` to discover the tool
schemas for their assigned skills, then POST to ``/api/skill-exec/{id}/call``
to execute them.
"""

from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

router = APIRouter()


# ---------------------------------------------------------------------------
# Built-in skill implementations
# ---------------------------------------------------------------------------


async def _skill_memory_search(args: dict, request: Request) -> dict:
    """Search agent memory via QMD.

    Agents have their own QMD — this is a stub that would proxy to the agent's
    QMD_SERVER. For now, return an empty result with a hint.
    """
    _ = args.get("query", "")
    _ = args.get("limit", 10)
    return {
        "status": "ok",
        "results": [],
        "note": "Route queries via agent's QMD_SERVER",
    }


def _resolve_agent_workspace(request: Request, args: dict) -> Path:
    """Return the per-agent workspace directory on the host.

    Same directory that ``deployer.deploy_agent`` bind-mounts into the
    container at ``/workspace``. Skills executed on behalf of an agent
    read and write through this path so the state survives a container
    rebuild or framework swap. See ``docs/design/framework-agnostic-runtime.md``.
    """
    agent_name = (
        args.get("agent_name")
        or request.query_params.get("agent_name")
        or "default"
    )
    base = Path(request.app.state.agent_workspaces_dir) / agent_name
    base.mkdir(parents=True, exist_ok=True)
    return base


async def _skill_file_read(args: dict, request: Request) -> dict:
    """Read a file from the calling agent's workspace."""
    path = args.get("path", "")
    workspace = _resolve_agent_workspace(request, args)
    target = (workspace / path).resolve()
    try:
        if workspace not in target.parents and target != workspace:
            return {"error": "Path outside workspace"}
        if not target.is_file():
            return {"error": "File not found"}
        return {"content": target.read_text(errors="replace")}
    except Exception as exc:
        return {"error": str(exc)}


async def _skill_file_write(args: dict, request: Request) -> dict:
    """Write a file to the calling agent's workspace."""
    path = args.get("path", "")
    content = args.get("content", "")
    workspace = _resolve_agent_workspace(request, args)
    target = (workspace / path).resolve()
    try:
        if workspace not in target.parents and target != workspace:
            return {"error": "Path outside workspace"}
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content)
        return {"status": "written", "bytes": len(content)}
    except Exception as exc:
        return {"error": str(exc)}


async def _skill_web_search(args: dict, request: Request) -> dict:
    """Search the web via SearXNG (if available)."""
    import httpx

    query = args.get("query", "")
    max_results = args.get("max_results", 5)
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.get(
                f"http://localhost:8888/search?q={query}&format=json"
            )
            if resp.status_code == 200:
                data = resp.json()
                return {"results": data.get("results", [])[:max_results]}
    except Exception:
        pass
    return {"error": "Web search not configured. Install SearXNG."}


async def _skill_code_exec(args: dict, request: Request) -> dict:
    """Execute Python code in a basic sandbox."""
    import subprocess

    code = args.get("code", "")
    try:
        result = subprocess.run(
            ["python3", "-c", code],
            capture_output=True,
            text=True,
            timeout=10,
        )
        return {
            "stdout": result.stdout,
            "stderr": result.stderr,
            "returncode": result.returncode,
        }
    except subprocess.TimeoutExpired:
        return {"error": "Execution timed out (10s limit)"}
    except Exception as exc:
        return {"error": str(exc)}


async def _skill_http_request(args: dict, request: Request) -> dict:
    """Make an HTTP request to an external URL."""
    import httpx

    url = args.get("url", "")
    method = args.get("method", "GET")
    try:
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.request(method, url)
            return {
                "status": resp.status_code,
                "headers": dict(resp.headers),
                "body": resp.text[:10000],
            }
    except Exception as exc:
        return {"error": str(exc)}


async def _skill_image_generation(args: dict, request: Request) -> dict:
    """Generate an image via local Stable Diffusion."""
    try:
        from tinyagentos.tools.image_tool import execute_image_generation

        result = await execute_image_generation(
            prompt=args.get("prompt", ""),
            size=args.get("size", "512x512"),
            steps=args.get("steps", 4),
            model=args.get("model") or None,
            guidance_scale=float(args.get("guidance_scale", 7.5)),
            negative_prompt=args.get("negative_prompt", ""),
        )
        return result
    except Exception as exc:
        return {"error": str(exc)}


async def _skill_list_image_models(args: dict, request: Request) -> dict:
    """List installed image-generation models."""
    try:
        from tinyagentos.tools.image_tool import execute_list_image_models

        result = await execute_list_image_models()
        return result
    except Exception as exc:
        return {"error": str(exc)}


async def _skill_open_app(args: dict, request: Request) -> dict:
    """Open an app on the user's desktop (agent OS control)."""
    try:
        from tinyagentos.tools.desktop_tools import execute_open_app

        return await execute_open_app(args, request)
    except Exception as exc:
        return {"error": str(exc)}


async def _skill_arrange_windows(args: dict, request: Request) -> dict:
    """Arrange the user's desktop windows (agent OS control)."""
    try:
        from tinyagentos.tools.desktop_tools import execute_arrange_windows

        return await execute_arrange_windows(args, request)
    except Exception as exc:
        return {"error": str(exc)}


SKILL_IMPLEMENTATIONS = {
    "memory_search": _skill_memory_search,
    "file_read": _skill_file_read,
    "file_write": _skill_file_write,
    "web_search": _skill_web_search,
    "code_exec": _skill_code_exec,
    "http_request": _skill_http_request,
    "image_generation": _skill_image_generation,
    "list_image_models": _skill_list_image_models,
    "open_app": _skill_open_app,
    "arrange_windows": _skill_arrange_windows,
}


# ---------------------------------------------------------------------------
# HTTP endpoints
# ---------------------------------------------------------------------------


@router.get("/api/skill-exec/tools")
async def list_tools(request: Request, agent_name: str):
    """Return tool schemas for an agent's assigned skills.

    Agents call this on startup to discover their available tools. The response
    format matches the OpenAI / MCP tool definition so adapters can pass it
    straight through to framework tool registries.
    """
    skill_store = request.app.state.skills
    skills = await skill_store.get_agent_skills(agent_name)

    tools = []
    for skill in skills:
        schema = skill.get("tool_schema") or {}
        if not schema:
            continue
        tools.append(
            {
                "type": "function",
                "function": {
                    "name": schema.get("name", skill["id"]),
                    "description": schema.get(
                        "description", skill.get("description", "")
                    ),
                    "parameters": schema.get("input_schema", {}),
                },
                "skill_id": skill["id"],
                "exec_url": f"/api/skill-exec/{skill['id']}/call",
            }
        )

    return JSONResponse({"tools": tools})


@router.post("/api/skill-exec/{skill_id}/call")
async def execute_skill(skill_id: str, request: Request):
    """Execute a skill with the given arguments."""
    body = await request.json()
    args = body.get("args", {})
    # Propagate agent_name from the request body into args so file-read
    # and file-write resolve the right per-agent workspace.
    if "agent_name" in body and "agent_name" not in args:
        args["agent_name"] = body["agent_name"]

    skill_store = request.app.state.skills
    skill = await skill_store.get_skill(skill_id)
    if not skill:
        return JSONResponse(
            {"error": f"Skill {skill_id} not found"}, status_code=404
        )

    impl = SKILL_IMPLEMENTATIONS.get(skill_id)
    if not impl:
        return JSONResponse(
            {"error": f"No implementation for {skill_id}"}, status_code=501
        )

    try:
        result = await impl(args, request)
        return JSONResponse(result)
    except Exception as exc:
        return JSONResponse({"error": str(exc)}, status_code=500)
