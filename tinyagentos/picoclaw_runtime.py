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
  ``workspace/AGENTS.md``, which PicoClaw loads into its system prompt.

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

    @property
    def config_path(self) -> Path:
        return self.home / "config.json"

    @property
    def workspace(self) -> Path:
        return self.home / "workspace"

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
        logger.info("picoclaw_runtime: wrote config for %d model(s) at %s",
                    len(self.models) + 1, self.config_path)

    def write_system_prompt(self, text: str | None) -> None:
        """PicoClaw reads ``workspace/AGENTS.md`` into its system prompt."""
        self.workspace.mkdir(parents=True, exist_ok=True)
        target = self.workspace / "AGENTS.md"
        body = (text or "").strip() + "\n"
        try:
            if target.read_text(encoding="utf-8") == body:
                return
        except OSError:
            pass
        atomic_write_text(target, body, mode=0o600)

    def scrub(self) -> None:
        """Delete the key-bearing config; the workspace (history) is kept."""
        try:
            self.config_path.unlink()
        except FileNotFoundError:
            pass

    def redact(self, text: str) -> str:
        return text.replace(self.key, "[redacted]") if self.key else text

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
