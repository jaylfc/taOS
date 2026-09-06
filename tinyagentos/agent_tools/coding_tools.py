"""Tool dispatch for the Coding Studio agent loop (#86).

This is the substrate the tool-calling loop sits on: a registry of the
workspace file primitives (read/write/exists/list) described as JSON tool
schemas an LLM can be handed, plus a single ``dispatch`` step that validates a
proposed tool call and executes it against a jailed workspace root.

Keeping execution here (rather than in the model glue) means the loop is the
same whether driven by a real model, a test, or a replayed transcript: propose
a call -> dispatch -> feed the result back.
"""

from __future__ import annotations

from typing import Any

from tinyagentos.agent_tools import fs_tools
from tinyagentos.agent_tools.fs_tools import JailViolation

# Cap how much a single read pulls into memory, matching the HTTP read route's
# guard so the agent loop cannot fault the controller by reading a huge file.
MAX_READ_BYTES = 2_000_000


class _ReadTooLarge(ValueError):
    """A read_file target exceeds MAX_READ_BYTES."""

# Anthropic/OpenAI-style function schemas. Handed to the model so it knows the
# available tools and their arguments; the names map 1:1 to _HANDLERS below.
TOOL_SCHEMAS: list[dict[str, Any]] = [
    {
        "name": "read_file",
        "description": "Read a UTF-8 text file inside the workspace.",
        "input_schema": {
            "type": "object",
            "properties": {"path": {"type": "string", "description": "Workspace-relative path."}},
            "required": ["path"],
        },
    },
    {
        "name": "write_file",
        "description": "Create or overwrite a UTF-8 text file inside the workspace.",
        "input_schema": {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "Workspace-relative path."},
                "content": {"type": "string", "description": "Full file contents to write."},
            },
            "required": ["path", "content"],
        },
    },
    {
        "name": "file_exists",
        "description": "Check whether a workspace-relative path is an existing file.",
        "input_schema": {
            "type": "object",
            "properties": {"path": {"type": "string"}},
            "required": ["path"],
        },
    },
    {
        "name": "list_dir",
        "description": "List the entries of a workspace directory (defaults to the root).",
        "input_schema": {
            "type": "object",
            "properties": {"path": {"type": "string", "description": "Workspace-relative dir; '.' for root."}},
            "required": [],
        },
    },
]

TOOL_NAMES = {t["name"] for t in TOOL_SCHEMAS}


def _h_read_file(root, args):
    # Resolve through the same jail fs_tools uses, then size-check before reading.
    target = fs_tools._resolve(root, args["path"])
    if target.stat().st_size > MAX_READ_BYTES:
        raise _ReadTooLarge(f"file exceeds {MAX_READ_BYTES} byte read limit")
    return target.read_text()


def _h_write_file(root, args):
    return fs_tools.write_file(root, args["path"], args["content"])


def _h_file_exists(root, args):
    return fs_tools.file_exists(root, args["path"])


def _h_list_dir(root, args):
    return fs_tools.list_dir(root, args.get("path", "."))


_HANDLERS = {
    "read_file": (_h_read_file, ("path",)),
    "write_file": (_h_write_file, ("path", "content")),
    "file_exists": (_h_file_exists, ("path",)),
    "list_dir": (_h_list_dir, ()),
}


def dispatch(workspace_root, name: str, arguments: dict | None) -> dict:
    """Execute one tool call against the workspace.

    Returns a structured, always-JSON-serialisable result:
      {"ok": True, "result": <value>}  on success
      {"ok": False, "error": <message>} on an unknown tool, missing argument,
        jail violation, or filesystem error.

    Never raises: the loop feeds the error string back to the model so it can
    recover, rather than crashing the turn.
    """
    handler = _HANDLERS.get(name)
    if handler is None:
        return {"ok": False, "error": f"unknown tool: {name!r}"}
    fn, required = handler
    args = arguments or {}
    if not isinstance(args, dict):
        return {"ok": False, "error": "arguments must be an object"}
    missing = [k for k in required if k not in args]
    if missing:
        return {"ok": False, "error": f"missing argument(s): {', '.join(missing)}"}
    try:
        return {"ok": True, "result": fn(workspace_root, args)}
    except JailViolation as exc:
        return {"ok": False, "error": str(exc)}
    except _ReadTooLarge as exc:
        return {"ok": False, "error": str(exc)}
    except UnicodeDecodeError:
        # read_file decodes as UTF-8; a binary/non-UTF-8 file must come back as a
        # soft error, not crash the loop turn.
        return {"ok": False, "error": "binary or non-UTF-8 file"}
    except FileNotFoundError:
        return {"ok": False, "error": "file not found"}
    except OSError as exc:
        return {"ok": False, "error": f"filesystem error: {exc.strerror or exc}"}
