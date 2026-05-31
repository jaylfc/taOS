"""Container shell — browser-based terminal for agent containers.

Provides a standalone HTML page (Pico CSS + htmx) that lets users run
commands inside agent containers via ``incus exec``.  Separate from the
WebSocket PTY bridge used by the React desktop SPA for resilience — this
page works without JavaScript bundling.

Routes:
  GET  /api/container-shell/{agent_id}       — HTML terminal page
  POST /api/container-shell/{agent_id}/exec  — execute a command, returns HTML
"""

from __future__ import annotations

import asyncio
import html
import logging
import re

from fastapi import APIRouter, Form, Request
from fastapi.responses import HTMLResponse

logger = logging.getLogger(__name__)

router = APIRouter()

# -- runtime auto-registers the /static mount (app.py) ------------------------
_PICO_CSS = "/static/pico.min.css"
_HTMX_JS = "https://unpkg.com/htmx.org@2.0.4/dist/htmx.min.js"

_ANSI_ESCAPE = re.compile(r"\x1b\[[0-?]*[ -/]*[@-~]")

_MAX_COMMAND_LENGTH = 4096
_EXEC_TIMEOUT = 30


def _strip_ansi(text: str) -> str:
    """Remove ANSI escape sequences from terminal output."""
    return _ANSI_ESCAPE.sub("", text)


async def _exec_in_container(container: str, command: str) -> tuple[int, str]:
    """Run a command inside a container via ``incus exec``.

    Returns (returncode, output).  The output has ANSI escapes stripped
    and is HTML-escaped by the caller.
    """
    proc = None
    try:
        proc = await asyncio.create_subprocess_exec(
            "incus", "exec", container, "--", "bash", "-lc", command,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
        )
        stdout_bytes, _ = await asyncio.wait_for(
            proc.communicate(), timeout=_EXEC_TIMEOUT,
        )
    except FileNotFoundError:
        return 127, "incus: command not found (is Incus installed?)"
    except asyncio.TimeoutError:
        if proc is not None:
            try:
                proc.kill()
                await proc.wait()
            except Exception:
                pass
        return 124, f"(command timed out after {_EXEC_TIMEOUT}s)"
    except Exception as exc:
        return 1, f"(error: {exc})"

    output = stdout_bytes.decode("utf-8", errors="replace") if stdout_bytes else ""
    return (proc.returncode or 0), _strip_ansi(output)


# ── HTML page ───────────────────────────────────────────────────────────────

_CONTAINER_SHELL_HTML = r"""<!DOCTYPE html>
<html lang="en" data-theme="dark">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Container Shell — {agent_id} — TinyAgentOS</title>
<link rel="stylesheet" href="$$PICO_CSS$$">
<style>
:root {
  --shell-bg: #151625;
  --shell-fg: rgba(255,255,255,0.85);
  --shell-prompt: #8b92a3;
}
body {
  margin: 0; padding: 0; background: var(--shell-bg);
  color: var(--shell-fg); font-family:
    'JetBrains Mono','Fira Code','Cascadia Code','SF Mono',monospace;
  font-size: 14px; line-height: 1.3;
  display: flex; flex-direction: column; height: 100vh; overflow: hidden;
}
.header {
  padding: 0.6rem 1rem; border-bottom: 1px solid rgba(255,255,255,0.08);
  display: flex; align-items: center; gap: 0.75rem;
  font-size: 0.85rem; color: var(--shell-prompt); flex-shrink: 0;
}
.header strong { color: var(--shell-fg); }
.output {
  flex: 1; overflow-y: auto; padding: 0.75rem 1rem; white-space: pre-wrap;
  word-break: break-all;
}
.output .cmd-line { color: var(--shell-prompt); margin-bottom: 0.2rem; }
.output .cmd-out  { margin-bottom: 0.75rem; }
.output .cmd-err  { color: #ff5f57; }
.output .cmd-info { color: var(--shell-prompt); font-style: italic; }
.input-bar {
  padding: 0.5rem 1rem; border-top: 1px solid rgba(255,255,255,0.08);
  flex-shrink: 0; display: flex; gap: 0.5rem;
}
.input-bar form { display: flex; gap: 0.5rem; width: 100%; }
.input-bar input {
  flex: 1; background: rgba(255,255,255,0.06); border: 1px solid rgba(255,255,255,0.12);
  color: var(--shell-fg); font-family: inherit; font-size: inherit;
  padding: 0.4rem 0.6rem; border-radius: 4px; outline: none;
}
.input-bar input:focus { border-color: #8b92a3; }
.input-bar button {
  background: rgba(255,255,255,0.08); border: 1px solid rgba(255,255,255,0.12);
  color: var(--shell-fg); font-family: inherit; font-size: inherit;
  padding: 0.4rem 0.8rem; border-radius: 4px; cursor: pointer;
  white-space: nowrap;
}
.input-bar button:hover { background: rgba(255,255,255,0.14); }
.htmx-indicator { opacity: 0; transition: opacity 0.2s; }
.htmx-request .htmx-indicator { opacity: 1; }
.htmx-request#shell-btn { opacity: 0.5; pointer-events: none; }
.empty-state {
  color: var(--shell-prompt); font-style: italic; padding: 2rem 0;
}
</style>
</head>
<body hx-ext="disable-element">
<div class="header">
  <span>&#128187;</span>
  <span>Container: <strong id="container-name">{container_name}</strong></span>
  <span style="margin-left:auto;opacity:0.5">{agent_id}</span>
</div>

<div
  class="output" id="output" role="log" aria-live="polite"
  aria-label="Terminal output"
  hx-on::after-settle="this.scrollTop = this.scrollHeight"
>
  <div class="empty-state">
    Container shell ready. Type a command below.
  </div>
</div>

<div class="input-bar">
  <form
    hx-post="/api/container-shell/{agent_id}/exec"
    hx-target="#output"
    hx-swap="beforeend"
    hx-indicator="#shell-btn"
    hx-on::after-request="this.reset(); document.getElementById('shell-cmd').focus()"
  >
    <label for="shell-cmd" hidden>Command</label>
    <input
      id="shell-cmd"
      name="command"
      type="text"
      autocomplete="off"
      autocorrect="off"
      autocapitalize="off"
      spellcheck="false"
      placeholder="ls -la /"
      maxlength="$$MAX_CMD_LEN$$"
      required
      autofocus
      aria-label="Shell command"
    >
    <button type="submit" id="shell-btn" aria-label="Run command">
      Run
      <span class="htmx-indicator">&#8230;</span>
    </button>
  </form>
</div>

<!-- htmx from CDN -->
<script src="$$HTMX_JS$$" integrity="sha384-L6YHG9qPUPXKpqZ1jJGp3Z3vGL5pJE1vJBlbQ3eJR7okILhYZcsW0E6+5jFxVxFE" crossorigin="anonymous"></script>
<script>
// Strip the empty-state placeholder on first output
(function() {{
  var out = document.getElementById('output');
  out.addEventListener('htmx:beforeSwap', function() {{
    var es = out.querySelector('.empty-state');
    if (es) es.remove();
  }}, {{once: true}});
}})();
</script>
</body>
</html>"""


@router.get("/api/container-shell/{agent_id}", response_class=HTMLResponse)
async def container_shell_page(agent_id: str, request: Request):
    """Serve the container shell HTML page for an agent.

    The page uses htmx to POST commands to the /exec endpoint and streams
    output into the log region.  No JavaScript build step required.
    """
    container_name = f"taos-agent-{agent_id}"
    html_str = _CONTAINER_SHELL_HTML
    html_str = html_str.replace("$$PICO_CSS$$", _PICO_CSS)
    html_str = html_str.replace("$$HTMX_JS$$", _HTMX_JS)
    html_str = html_str.replace("$$MAX_CMD_LEN$$", str(_MAX_COMMAND_LENGTH))
    html_str = html_str.replace("{agent_id}", html.escape(agent_id))
    html_str = html_str.replace("{container_name}", html.escape(container_name))
    return HTMLResponse(html_str)


@router.post("/api/container-shell/{agent_id}/exec", response_class=HTMLResponse)
async def container_shell_exec(agent_id: str, command: str = Form(...)):
    """Execute a command inside the agent container and return HTML fragment."""
    if not command or not command.strip():
        return HTMLResponse(
            '<div class="cmd-info">(empty command)</div>'
        )

    command = command.strip()
    if len(command) > _MAX_COMMAND_LENGTH:
        return HTMLResponse(
            '<div class="cmd-err">Command too long '
            f'(max {_MAX_COMMAND_LENGTH} characters)</div>'
        )

    container_name = f"taos-agent-{agent_id}"
    escaped_cmd = html.escape(command)
    escaped_container = html.escape(container_name)

    rc, output = await _exec_in_container(container_name, command)

    if rc == 127:
        # incus not installed
        return HTMLResponse(
            f'<div class="cmd-line">$ {escaped_cmd}</div>'
            f'<div class="cmd-err">{html.escape(output)}</div>'
        )
    if rc == 124:
        return HTMLResponse(
            f'<div class="cmd-line">$ {escaped_cmd}</div>'
            f'<div class="cmd-info">{html.escape(output)}</div>'
        )

    escaped_output = html.escape(output.rstrip()) if output else "(no output)"
    return HTMLResponse(
        f'<div class="cmd-line">$ {escaped_cmd}</div>'
        f'<div class="cmd-out">{escaped_output}</div>'
    )
