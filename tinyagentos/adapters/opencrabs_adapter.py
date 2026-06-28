"""OpenCrabs adapter - drives the opencrabs CLI non-interactively.

opencrabs (adolfousier/opencrabs) is a single Rust binary autonomous agent
inspired by OpenClaw. Unlike the HTTP-gateway frameworks, it has no long-running
server in its released CLI; taOS drives it headlessly with

    opencrabs run "<prompt>" --format json --auto-approve

which executes the prompt once and prints a JSON result. The binary and a
configured provider live inside the agent's container; this sidecar only
translates a taOS ``/message`` into that invocation and maps the result onto the
``{content}`` reply the chat bridge expects.

The subprocess call goes through the module-level ``run_opencrabs`` callable so
the parsing logic is unit-tested without a real binary (tests rebind it).
"""
from __future__ import annotations

import asyncio
import json
import os

from fastapi import FastAPI

app = FastAPI()

# Binary name (or absolute path) and optional taOS-managed profile. The agent's
# container installs the binary on PATH as ``opencrabs``; an explicit path can
# be injected for non-standard layouts.
OPENCRABS_BIN = os.environ.get("OPENCRABS_BIN", "opencrabs")
OPENCRABS_PROFILE = os.environ.get("OPENCRABS_PROFILE", "")
# A single autonomous turn can run tools; give it a generous ceiling.
_TIMEOUT = float(os.environ.get("OPENCRABS_TIMEOUT", "300"))

# Keys an `opencrabs run --format json` payload may carry the reply under,
# tried in order. opencrabs versions have varied the field name, so the parser
# is deliberately tolerant and falls back to the raw stdout.
_REPLY_KEYS = ("response", "content", "text", "output", "message", "result")


def build_argv(text: str) -> list[str]:
    """Build the argv for a one-shot ``opencrabs run`` of *text*."""
    argv = [OPENCRABS_BIN]
    if OPENCRABS_PROFILE:
        argv += ["--profile", OPENCRABS_PROFILE]
    argv += ["run", text, "--format", "json", "--auto-approve"]
    return argv


def extract_reply(stdout: str) -> str:
    """Pull the assistant reply text out of ``opencrabs run --format json``.

    Tolerant by design: the payload may be a bare JSON object, JSON preceded by
    a human "Processing..." line, or (on older builds) plain text. Falls back to
    the stripped stdout when no known reply field is found.
    """
    stdout = (stdout or "").strip()
    if not stdout:
        return ""

    data = _parse_json_object(stdout)
    if isinstance(data, dict):
        for key in _REPLY_KEYS:
            value = data.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()
        # Some shapes nest the text under message: {message: {content|text}}.
        nested = data.get("message")
        if isinstance(nested, dict):
            value = nested.get("content") or nested.get("text")
            if isinstance(value, str) and value.strip():
                return value.strip()
    return stdout


def _parse_json_object(stdout: str):
    """Return the JSON object in *stdout*, or None. Tries the whole string then
    the last ``{...}`` line (to skip a leading progress line)."""
    try:
        return json.loads(stdout)
    except json.JSONDecodeError:
        pass
    for line in reversed(stdout.splitlines()):
        line = line.strip()
        if line.startswith("{"):
            try:
                return json.loads(line)
            except json.JSONDecodeError:
                continue
    return None


async def _default_runner(argv: list[str]) -> tuple[int, str, str]:
    """Run *argv*, returning (returncode, stdout, stderr). Enforces _TIMEOUT."""
    proc = await asyncio.create_subprocess_exec(
        *argv,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    try:
        out, err = await asyncio.wait_for(proc.communicate(), timeout=_TIMEOUT)
    except asyncio.TimeoutError:
        proc.kill()
        # Reap the killed child and drain its pipes so it does not linger as a
        # zombie; communicate() after kill returns promptly.
        try:
            await proc.communicate()
        except Exception:  # noqa: BLE001
            pass
        raise
    return (
        proc.returncode,
        out.decode("utf-8", "replace"),
        err.decode("utf-8", "replace"),
    )


# Injectable for tests; defaults to the real subprocess runner.
run_opencrabs = _default_runner


@app.post("/message")
async def handle_message(msg: dict):
    text = (msg.get("text") or "").strip()
    if not text:
        return {"content": ""}
    try:
        rc, out, err = await run_opencrabs(build_argv(text))
    except FileNotFoundError:
        return {"content": f"OpenCrabs binary {OPENCRABS_BIN!r} not found. Is it installed in the agent container?"}
    except asyncio.TimeoutError:
        return {"content": "OpenCrabs timed out before producing a reply."}
    except Exception as exc:  # noqa: BLE001 - surface any drive error as chat content
        return {"content": f"OpenCrabs error: {exc}"}
    if rc != 0:
        detail = (err or out).strip() or f"exit {rc}"
        return {"content": f"OpenCrabs run failed: {detail}"}
    return {"content": extract_reply(out)}


@app.get("/health")
async def health():
    return {"status": "ok", "framework": "opencrabs", "agent": os.environ.get("TAOS_AGENT_NAME", "")}


if __name__ == "__main__":
    import uvicorn

    port = int(os.environ.get("TAOS_ADAPTER_PORT", "9001"))
    uvicorn.run(app, host="127.0.0.1", port=port, log_level="warning")
