"""PicoClaw host runtime for the system taOS Agent (taOSmobile).

PicoClaw (sipeed/picoclaw, pinned 0.3.1) has no long-lived server to supervise:
one agent turn is one ``picoclaw agent -m <text> -s <session>`` process. This
module owns the three things that make that process the taOS Agent:

- its HOME: ``<data_dir>/taos-agent-picoclaw`` (0700), holding ``config.json``
  (0600: the only place the gateway key is written) and ``workspace/``, which
  PicoClaw is restricted to and where it keeps its sessions and memory;
- its CONFIG: one model_list entry per model the agent may use, every one
  pointing at the controller's own LLM gateway (``/api/llm/v1``) with the
  agent's scoped gateway key. The default model is ``taos-default``, which the
  gateway resolves to the model picked in the taOS Agent settings;
- its PROMPT: the taOS Agent manual (or the persona override) written to
  ``workspace/AGENTS.md``, which PicoClaw loads into its system prompt, with a
  section that tells it to reach taOS through ``bin/taos``;
- its taOS ACCESS: ``workspace/bin/taos``, a helper that calls the
  controller's HTTP API on loopback with the credential in
  ``workspace/.taos_credential`` (0600). opencode runs unconfined as the
  service user and reaches the same API with the host local token; PicoClaw is
  confined to its workspace, so the same credential is copied in, no more.
  The helper reads it from the file (never argv, so it is not in ``ps``) and
  never prints it.

Measured against the real 0.3.1 binary: ``PICOCLAW_CONFIG`` selects the config
file; the model call is a non-streaming POST to ``<api_base>/chat/completions``
with ``Authorization: Bearer <api_keys[0]>`` and the ``openai/`` prefix
stripped from ``model``; stdout is a colour banner then the answer after a
lobster (U+1F99E).

The key never leaves config.json: it is not logged, not put in argv or the
environment, and any output that echoed it is redacted before it is surfaced.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import shutil
from dataclasses import dataclass, field
from pathlib import Path

from tinyagentos.atomic_io import atomic_write_text

logger = logging.getLogger(__name__)

HOME_DIRNAME = "taos-agent-picoclaw"
CREDENTIAL_FILENAME = ".taos_credential"
HELPER_RELPATH = "bin/taos"
DEFAULT_MODEL = "taos-default"
LOBSTER = "\U0001F99E"
_ANSI = re.compile(r"\x1b\[[0-9;?]*[A-Za-z]")
_LOG_LINE = re.compile(r"^\d\d:\d\d:\d\d (WRN|ERR|INF|DBG) ")

# Root-controlled install locations beyond PATH (the catalog installer drops
# the binary at /usr/local/bin/picoclaw). As with opencode, user homes are
# deliberately NOT probed: a binary there could be planted by another user.
_PICOCLAW_SYSTEM_PATHS = ("/usr/local/bin/picoclaw", "/usr/bin/picoclaw")

# Tools PicoClaw may use, mirroring what the opencode-run taOS Agent has
# (shell + file tools + web fetch), confined to the workspace. Everything
# that would reach outside taOS's control (channels, cron, skill registries,
# hardware buses) stays off.
_TOOLS = {
    "exec": {"enabled": True, "enable_deny_patterns": True},
    "read_file": {"enabled": True},
    "write_file": {"enabled": True},
    "edit_file": {"enabled": True},
    "append_file": {"enabled": True},
    "list_dir": {"enabled": True},
    "web": {"enabled": False},
    "web_fetch": {"enabled": True},
    "cron": {"enabled": False},
    "mcp": {"enabled": False},
    "skills": {"enabled": False, "registries": {"clawhub": {"enabled": False}}},
    "install_skill": {"enabled": False},
    "find_skills": {"enabled": False},
    "spawn": {"enabled": False},
    "subagent": {"enabled": False},
    "message": {"enabled": False},
    "send_file": {"enabled": False},
    "load_image": {"enabled": False},
    "i2c": {"enabled": False},
    "spi": {"enabled": False},
    "serial": {"enabled": False},
}


_HELPER = '''#!{python}
"""taos: call the taOS API on this device as the taOS Agent.

    taos METHOD api/PATH [JSON_BODY | -]     (- reads the JSON body from stdin)
    taos UPLOAD api/PATH LOCAL_FILE          (multipart form field "file")

PATH has no leading slash: PicoClaw's exec guard refuses a command that
names an absolute path outside the workspace ("/api/..." reads as one).

Prints the response body; exits non-zero when the call failed. The
credential is read from a file beside bin/, never from the command line,
and never printed.
"""
import http.client, json, os, sys, urllib.error, urllib.request, uuid

BASE = {base!r}
CRED = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), {cred!r})
USAGE = "usage: taos METHOD api/PATH [JSON_BODY|-]  |  taos UPLOAD api/PATH LOCAL_FILE"


def main(argv):
    if len(argv) >= 2 and argv[1].startswith("api/"):
        argv = [argv[0], "/" + argv[1]] + argv[2:]
    if len(argv) < 2 or not argv[1].startswith("/api/"):
        print(USAGE, file=sys.stderr)
        return 2
    # A "/api/" prefix is only a boundary if the path cannot climb out of it:
    # "api/../auth/..." starts with /api/ and names something else.
    segments = argv[1].split("?", 1)[0].split("/")
    if any(seg in (".", "..") for seg in segments) or "%2e" in argv[1].lower():
        print(USAGE, file=sys.stderr)
        return 2
    method, path = argv[0].upper(), argv[1]
    try:
        with open(CRED) as fh:
            token = fh.read().strip()
    except OSError:
        print("taos: no credential here (is the taOS Agent running on PicoClaw?)", file=sys.stderr)
        return 2
    headers = {{"Authorization": "Bearer " + token}}
    data = None
    if method == "UPLOAD":
        if len(argv) < 3:
            print(USAGE, file=sys.stderr)
            return 2
        boundary = uuid.uuid4().hex
        name = os.path.basename(argv[2]).replace('"', "")
        with open(argv[2], "rb") as fh:
            payload = fh.read()
        data = (("--%s\\r\\nContent-Disposition: form-data; name=\\"file\\"; filename=\\"%s\\"\\r\\n"
                 "Content-Type: application/octet-stream\\r\\n\\r\\n") % (boundary, name)).encode() \\
            + payload + ("\\r\\n--%s--\\r\\n" % boundary).encode()
        headers["Content-Type"] = "multipart/form-data; boundary=" + boundary
        method = "POST"
    elif len(argv) > 2:
        body = sys.stdin.read() if argv[2] == "-" else argv[2]
        try:
            json.loads(body)
        except ValueError:
            print("taos: the body must be JSON", file=sys.stderr)
            return 2
        data = body.encode()
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(BASE + path, data=data, method=method, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=120) as resp:
            code, out = resp.status, resp.read()
    except urllib.error.HTTPError as exc:
        code, out = exc.code, exc.read()
    except (urllib.error.URLError, OSError, http.client.HTTPException) as exc:
        print("taos: cannot reach taOS: %s" % getattr(exc, "reason", exc), file=sys.stderr)
        return 1
    text = out.decode("utf-8", "replace").replace(token, "[redacted]")
    sys.stdout.write(text if text.endswith("\\n") or not text else text + "\\n")
    if code >= 400:
        print("taos: HTTP %d" % code, file=sys.stderr)
        return 1
    return 0


sys.exit(main(sys.argv[1:]))
'''

TOOLS_SECTION = """
---

# Reaching taOS from PicoClaw

You run on PicoClaw, confined to your workspace. Every taOS tool named in this
manual is an HTTP call to taOS on this device, made with `bin/taos` through
your `exec` tool (run it from your workspace):

    bin/taos METHOD api/PATH [JSON_BODY]
    bin/taos UPLOAD api/PATH LOCAL_FILE

Write the path WITHOUT a leading slash (`api/...`): your exec tool refuses a
command naming an absolute path. It prints the response body and exits
non-zero on failure. It acts as the
device owner; its credential is handled for you. Never look for it, print it,
copy it or pass it to anything.

Desktop (always through the control API):

- open_app: `bin/taos POST api/desktop/command '{"kind":"open-app","payload":{"app":"projects"}}'`
  (add `"props":{...}` to the payload to deep-link)
- arrange_windows: `bin/taos POST api/desktop/command '{"kind":"window","payload":{"action":"arrange","preset":"tile-2"}}'`
- any window action (open, close, focus, minimize, restore, maximize, move,
  resize, snap): `bin/taos POST api/desktop/command '{"kind":"window","payload":{"action":"focus","appId":"notes"}}'`
- read_layout: `bin/taos POST api/desktop/layout '{}'`
- screenshot: `bin/taos POST api/desktop/screenshot '{}'`

Every other tool (create_project, add_task, canvas_add_image, export_storybook,
describe_image_capabilities, generate_image as image_generation,
notes_list_shared_docs, notes_add_entry, todo_list_lists, todo_add_item,
todo_set_done, memory_search, list_projects, list_tasks, notify_user,
request_decision) is one call, with that tool's arguments in "args":

    bin/taos POST api/skill-exec/<tool>/call '{"agent_name":"taos-agent","args":{...}}'

e.g. `bin/taos POST api/skill-exec/notes_add_entry/call '{"agent_name":"taos-agent","args":{"doc_id":"<id>","text":"milk"}}'`

Project files: `bin/taos GET api/projects/<slug>/files?path=<subdir>`,
`bin/taos GET api/projects/<slug>/files/<path>`,
`bin/taos POST api/projects/<slug>/mkdir '{"path":"<subdir>"}'`,
`bin/taos UPLOAD api/projects/<slug>/files/upload?path=<subdir> <local file>`.
"""


class PicoClawBinaryNotFoundError(RuntimeError):
    """No ``picoclaw`` binary in any trusted location."""


def resolve_picoclaw_binary() -> str | None:
    """``TAOS_PICOCLAW_BIN``, then PATH, then root-controlled system paths."""
    override = os.environ.get("TAOS_PICOCLAW_BIN")
    if override:
        if _is_executable(Path(override)):
            return override
        logger.warning("TAOS_PICOCLAW_BIN=%s is not an executable file; ignoring it", override)
    found = shutil.which("picoclaw")
    if found:
        return found
    for candidate in _PICOCLAW_SYSTEM_PATHS:
        if _is_executable(Path(candidate)):
            return candidate
    return None


def _is_executable(path: Path) -> bool:
    try:
        return path.is_file() and os.access(path, os.X_OK)
    except OSError:
        return False


def home_for(data_dir: str | Path) -> Path:
    return Path(data_dir) / HOME_DIRNAME


def extract_reply(stdout: str) -> str:
    """The answer is everything after the LAST lobster, ANSI codes stripped.

    With no lobster, the banner and log noise are never passed off as an
    answer: only the last line that is not a log line is returned.
    """
    clean = _ANSI.sub("", stdout).replace("\r", "")
    if LOBSTER in clean:
        return clean.rsplit(LOBSTER, 1)[1].strip()
    lines = [ln for ln in clean.splitlines() if ln.strip() and not _LOG_LINE.match(ln)]
    return lines[-1].strip() if lines else ""


@dataclass
class TurnResult:
    returncode: int
    reply: str
    detail: str = ""
    timed_out: bool = False


@dataclass
class PicoClawHarness:
    """One provisioned PicoClaw home: config + workspace + the key it holds."""

    home: Path
    api_base: str
    key: str = field(repr=False)
    models: list[str]
    binary: str = "picoclaw"
    turn_timeout: float = 300.0
    credential: str | None = field(default=None, repr=False)
    """The credential ``bin/taos`` uses: the same one opencode reaches taOS with."""
    controller_base: str = ""
    """e.g. ``http://127.0.0.1:6969``: where ``bin/taos`` sends its calls."""

    @property
    def config_path(self) -> Path:
        return self.home / "config.json"

    @property
    def workspace(self) -> Path:
        return self.home / "workspace"

    @property
    def credential_path(self) -> Path:
        return self.workspace / CREDENTIAL_FILENAME

    @property
    def helper_path(self) -> Path:
        return self.workspace / HELPER_RELPATH

    def render_config(self) -> dict:
        names = [DEFAULT_MODEL] + [m for m in self.models if m != DEFAULT_MODEL]
        return {
            "version": 3,
            "agents": {
                "defaults": {
                    "workspace": str(self.workspace),
                    "restrict_to_workspace": True,
                    "model_name": DEFAULT_MODEL,
                    "max_tool_iterations": 12,
                },
            },
            "model_list": [
                {
                    "model_name": name,
                    "model": f"openai/{name}",
                    "api_keys": [self.key],
                    "api_base": self.api_base,
                }
                for name in names
            ],
            "tools": _TOOLS,
        }

    def write_config(self) -> None:
        self.home.mkdir(parents=True, exist_ok=True)
        os.chmod(self.home, 0o700)
        self.workspace.mkdir(parents=True, exist_ok=True)
        atomic_write_text(self.config_path, json.dumps(self.render_config(), indent=1), mode=0o600)
        if self.credential:
            self.write_taos_access()
        logger.info("picoclaw_runtime: wrote config for %d model(s) at %s",
                    len(self.models) + 1, self.config_path)

    def write_taos_access(self) -> None:
        """``bin/taos`` (0700) and the credential it reads (0600)."""
        import sys

        self.helper_path.parent.mkdir(parents=True, exist_ok=True)
        atomic_write_text(self.credential_path, self.credential or "", mode=0o600)
        atomic_write_text(
            self.helper_path,
            _HELPER.format(python=sys.executable, base=self.controller_base, cred=CREDENTIAL_FILENAME),
            mode=0o700,
        )

    def write_system_prompt(self, text: str | None) -> None:
        """PicoClaw reads ``workspace/AGENTS.md`` into its system prompt: the
        manual or persona, then how to reach taOS through ``bin/taos``."""
        self.workspace.mkdir(parents=True, exist_ok=True)
        target = self.workspace / "AGENTS.md"
        body = (text or "").strip() + "\n" + TOOLS_SECTION
        try:
            if target.read_text(encoding="utf-8") == body:
                return
        except OSError:
            pass
        atomic_write_text(target, body, mode=0o600)

    def redact(self, text: str) -> str:
        for secret in (self.key, self.credential):
            if secret:
                text = text.replace(secret, "[redacted]")
        return text

    def _env(self) -> dict[str, str]:
        """A minimal environment: the controller's own env (tokens, flags,
        provider keys) is not inherited by the agent's shell tool."""
        env = {
            "HOME": str(self.home),
            "PICOCLAW_HOME": str(self.home),
            "PICOCLAW_CONFIG": str(self.config_path),
            "PATH": os.environ.get("PATH", "/usr/local/bin:/usr/bin:/bin"),
            "LANG": os.environ.get("LANG", "C.UTF-8"),
            "NO_COLOR": "1",
        }
        if os.environ.get("TZ"):
            env["TZ"] = os.environ["TZ"]
        return env

    async def run_turn(self, text: str, session: str) -> TurnResult:
        """Run one ``picoclaw agent`` turn. Never raises for a failed turn."""
        try:
            proc = await asyncio.create_subprocess_exec(
                self.binary, "agent", "--no-color", "-m", text, "-s", session,
                cwd=str(self.workspace),
                env=self._env(),
                stdin=asyncio.subprocess.DEVNULL,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
        except FileNotFoundError as exc:
            raise PicoClawBinaryNotFoundError(f"picoclaw binary not found ({self.binary!r})") from exc
        try:
            out, err = await asyncio.wait_for(proc.communicate(), timeout=self.turn_timeout)
        except asyncio.TimeoutError:
            try:
                proc.kill()
            except ProcessLookupError:
                pass
            await proc.wait()
            return TurnResult(returncode=-1, reply="", timed_out=True,
                              detail=f"picoclaw turn timed out after {self.turn_timeout:g}s")
        except asyncio.CancelledError:
            try:
                proc.kill()
            except ProcessLookupError:
                pass
            raise
        stdout = self.redact(out.decode("utf-8", "replace"))
        stderr = self.redact(err.decode("utf-8", "replace"))
        reply = extract_reply(stdout) if proc.returncode == 0 else ""
        detail = ""
        if proc.returncode != 0 or not reply:
            tail = [ln for ln in _ANSI.sub("", stderr + "\n" + stdout).splitlines() if ln.strip()]
            detail = tail[-1].strip()[:300] if tail else ""
        return TurnResult(returncode=proc.returncode or 0, reply=reply, detail=detail)


def scrub_home(data_dir: str | Path) -> None:
    """Delete every secret PicoClaw's home holds (the key-bearing config and
    the credential copy, with the helper that reads it). The rest of the
    workspace, PicoClaw's sessions and memory, is kept."""
    home = home_for(data_dir)
    for path in (home / "config.json", home / "workspace" / CREDENTIAL_FILENAME,
                 home / "workspace" / HELPER_RELPATH):
        try:
            path.unlink()
        except FileNotFoundError:
            pass
