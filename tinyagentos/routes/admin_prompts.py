"""Admin prompt library — read-only HTTP surface.

Each prompt is a markdown file at tinyagentos/admin_prompts/*.md with a
YAML front-matter block. The UI fetches one by name and prefills the
Messages composer so the user can dispatch a structured task to an agent
with one click.
"""
from __future__ import annotations

from pathlib import Path

import yaml
from fastapi import APIRouter
from fastapi.responses import JSONResponse

router = APIRouter()

_PROMPTS_DIR = Path(__file__).parent.parent / "admin_prompts"


def _list_prompts() -> list[dict]:
    out = []
    for p in sorted(_PROMPTS_DIR.glob("*.md")):
        text = p.read_text()
        meta, body = _split_front_matter(text)
        out.append({
            "name": meta.get("name") or p.stem,
            "summary": meta.get("summary", ""),
            "version": meta.get("version", 1),
            "required_variables": meta.get("required_variables", []),
        })
    return out


def _split_front_matter(text: str) -> tuple[dict, str]:
    if not text.startswith("---\n"):
        return {}, text
    end = text.find("\n---\n", 4)
    if end == -1:
        return {}, text
    raw = text[4:end]
    body = text[end + 5:].lstrip("\n")
    try:
        meta = yaml.safe_load(raw) or {}
    except Exception:
        meta = {}
    if not isinstance(meta, dict):
        meta = {}
    return meta, body


@router.get("/api/admin-prompts")
async def list_prompts():
    """Enumerate available prompts. Summary only — body not returned here."""
    return {"prompts": _list_prompts()}


@router.get("/api/admin-prompts/{name}")
async def get_prompt(name: str):
    """Return the full prompt body for a named prompt. Prefills composer."""
    if "/" in name or ".." in name:
        return JSONResponse({"error": "invalid prompt name"}, status_code=400)
    path = _PROMPTS_DIR / f"{name}.md"
    if not path.exists():
        return JSONResponse({"error": f"prompt {name!r} not found"}, status_code=404)
    text = path.read_text()
    meta, body = _split_front_matter(text)
    return {
        "name": meta.get("name") or name,
        "summary": meta.get("summary", ""),
        "version": meta.get("version", 1),
        "required_variables": meta.get("required_variables", []),
        "body": body,
    }
