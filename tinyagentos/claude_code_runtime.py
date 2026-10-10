"""Claude Code as the system taOS Agent's harness.

One ``claude -p`` process per turn, signed in with a long-lived token from
``claude setup-token`` that lives in the device Secrets store (secret name
:data:`CLAUDE_CODE_TOKEN_SECRET`), never in the controller's environment. The
token reaches the child only as ``CLAUDE_CODE_OAUTH_TOKEN``; nothing of it is
written to disk by taOS. Mirrors :mod:`tinyagentos.picoclaw_runtime`.

The conversation continues across turns with ``--resume <session-id>``; the
session id comes from the first stream-json event that carries one.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import shutil
import tempfile
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

logger = logging.getLogger(__name__)

CLAUDE_CODE_TOKEN_SECRET = "claude_code_oauth_token"
HOME_DIRNAME = "taos-agent-claude-code"

_CLAUDE_FALLBACK_PATHS = ("~/.local/bin/claude", "/usr/local/bin/claude")


class ClaudeCodeBinaryNotFoundError(RuntimeError):
    """The ``claude`` binary is not installed or not reachable."""


class ClaudeCodeTokenMissingError(RuntimeError):
    """The ``claude_code_oauth_token`` secret is absent or empty."""


def _is_executable(path: Path) -> bool:
    try:
        return path.is_file() and os.access(path, os.X_OK)
    except OSError:
        return False


def resolve_claude_binary() -> str | None:
    """``claude`` on PATH, then ``~/.local/bin/claude``, then ``/usr/local/bin/claude``."""
    found = shutil.which("claude")
    if found:
        return found
    for candidate in _CLAUDE_FALLBACK_PATHS:
        path = Path(candidate).expanduser()
        if _is_executable(path):
            return str(path)
    return None


async def claude_code_token_present(app_state) -> bool:
    """Whether the Secrets store holds the token secret. Presence only: the
    value is read and dropped, never logged or returned."""
    store = getattr(app_state, "secrets", None)
    if store is None:
        return False
    try:
        row = await store.get(CLAUDE_CODE_TOKEN_SECRET)
    except Exception:  # noqa: BLE001 - a broken store means no token
        logger.warning("claude_code_runtime: could not read the secrets store")
        return False
    return bool(row and row.get("value"))


def home_for(data_dir: str | Path) -> Path:
    return Path(data_dir) / HOME_DIRNAME


@dataclass
class TurnResult:
    returncode: int
    reply: str
    detail: str = ""
    timed_out: bool = False
    session_id: str | None = None


@dataclass
class ClaudeCodeHarness:
    """One Claude Code home (its HOME and workspace) plus the token it holds."""

    home: Path
    token: str = field(repr=False)
    model: str
    binary: str = "claude"
    turn_timeout: float = 300.0
    system: str | None = None
    _system_prompt_file: str | None = field(init=False, default=None, repr=False)
    """Appended to Claude Code's system prompt with ``--append-system-prompt``."""

    @property
    def workspace(self) -> Path:
        return self.home / "workspace"

    def write_home(self) -> None:
        self.home.mkdir(parents=True, exist_ok=True)
        os.chmod(self.home, 0o700)
        self.workspace.mkdir(parents=True, exist_ok=True)

    def redact(self, text: str) -> str:
        if self.token:
            text = text.replace(self.token, "[redacted]")
        return text

    def _env(self) -> dict[str, str]:
        """A minimal environment: the controller's own env (tokens, flags,
        provider keys) is not inherited by the agent's shell tool."""
        env = {
            "HOME": str(self.home),
            "PATH": os.environ.get("PATH", "/usr/local/bin:/usr/bin:/bin"),
            "LANG": os.environ.get("LANG", "C.UTF-8"),
            "NO_COLOR": "1",
            "CLAUDE_CODE_OAUTH_TOKEN": self.token,
        }
        if os.environ.get("TZ"):
            env["TZ"] = os.environ["TZ"]
        return env

    def _cleanup_system_prompt_file(self) -> None:
        if self._system_prompt_file is not None:
            try:
                os.unlink(self._system_prompt_file)
            except FileNotFoundError:
                pass
            finally:
                self._system_prompt_file = None

    def _argv(self, session_id: str | None) -> list[str]:
        argv = [self.binary, "-p", "--output-format", "stream-json", "--verbose",
                "--model", self.model, "--permission-mode", "bypassPermissions"]
        if self.system:
            # Create a temporary file for the system prompt
            with tempfile.NamedTemporaryFile(mode="w", suffix=".md", delete=False) as f:
                f.write(self.system)
                path = f.name
            os.chmod(path, 0o600)
            self._system_prompt_file = path
            argv += ["--append-system-prompt-file", path]
        if session_id:
            argv += ["--resume", session_id]
        return argv

    async def run_turn(
        self,
        text: str,
        session_id: str | None,
        on_event: Callable[[dict], None] | None = None,
    ) -> TurnResult:
        """Run one ``claude -p`` turn. Never raises for a failed turn."""
        try:
            proc = await asyncio.create_subprocess_exec(
                *self._argv(session_id),
                cwd=str(self.workspace),
                env=self._env(),
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
        except FileNotFoundError as exc:
            raise ClaudeCodeBinaryNotFoundError(f"claude binary not found ({self.binary!r})") from exc

        events: list[dict] = []
        stdout_lines: list[str] = []
        found_session: list[str | None] = [None]

        async def _feed() -> None:
            try:
                proc.stdin.write(text.encode("utf-8"))
                await proc.stdin.drain()
            except (BrokenPipeError, ConnectionResetError):
                pass
            finally:
                try:
                    proc.stdin.close()
                except Exception:  # noqa: BLE001, S110 - best-effort close of a dead pipe
                    pass

        async def _read_stdout() -> None:
            async for raw in proc.stdout:
                line = self.redact(raw.decode("utf-8", "replace")).strip()
                if not line:
                    continue
                stdout_lines.append(line)
                try:
                    evt = json.loads(line)
                except ValueError:
                    continue
                if not isinstance(evt, dict):
                    continue
                if found_session[0] is None and isinstance(evt.get("session_id"), str):
                    found_session[0] = evt["session_id"]
                events.append(evt)
                if on_event is not None:
                    try:
                        on_event(evt)
                    except Exception:
                        logger.exception("claude_code_runtime: on_event raised")

        async def _run() -> bytes:
            _, _, err = await asyncio.gather(_feed(), _read_stdout(), proc.stderr.read())
            await proc.wait()
            return err

        try:
            err = await asyncio.wait_for(_run(), timeout=self.turn_timeout)
        except TimeoutError:
            try:
                proc.kill()
            except ProcessLookupError:
                pass
            await proc.wait()
            return TurnResult(returncode=-1, reply="", timed_out=True, session_id=found_session[0],
                              detail=f"claude code turn timed out after {self.turn_timeout:g}s")
        except asyncio.CancelledError:
            try:
                proc.kill()
            except ProcessLookupError:
                pass
            raise
        finally:
            self._cleanup_system_prompt_file()
        stderr = self.redact(err.decode("utf-8", "replace"))
        reply = ""
        for evt in events:
            if evt.get("type") == "result" and not evt.get("is_error") and isinstance(evt.get("result"), str):
                reply = evt["result"]
        detail = ""
        if proc.returncode != 0 or not reply:
            tail = [ln for ln in (stderr + "\n" + "\n".join(stdout_lines)).splitlines() if ln.strip()]
            detail = tail[-1].strip()[:300] if tail else ""
        return TurnResult(returncode=proc.returncode or 0, reply=reply, detail=detail,
                          session_id=found_session[0])


def scrub_home(data_dir: str | Path) -> None:
    """Present for symmetry with :func:`tinyagentos.picoclaw_runtime.scrub_home`.

    Deletes nothing: taOS never writes the token to disk (it is handed to the
    child in its environment), so there is no secret in the Claude Code home
    to scrub. Claude Code's own session history under the home is kept.
    """
