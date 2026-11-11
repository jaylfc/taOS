"""Kilo as the system taOS Agent's harness.

One ``kilo run --format json`` process per turn, signed in with the
``kilo_api_key`` secret from the device Secrets store (secret name
:data:`KILO_API_KEY_SECRET`), passed to the child only as ``KILO_API_KEY``;
nothing of it is written to disk by taOS and it is never logged. Mirrors
:mod:`tinyagentos.claude_code_runtime`.

The conversation continues across turns with ``-s <session-id>``; the session
id comes from the first stream-json event that carries one (its ``sessionID``
field). The reply is the concatenated ``part.text`` of the ``type == "text"``
events.

``--dir`` must be a git repo or kilo writes zero bytes and hangs, so the
harness home dir (under data_dir, like claude_code's :func:`home_for`) is
``git init``ed once on creation and stdin is closed (DEVNULL): kilo hangs
with a live stdin too.

Models are not hardcoded: the default is :data:`DEFAULT_MODEL`
(``kilo/kilo-auto/free``) and the available list comes from running
``kilo models`` at runtime (one id per line, free ones end in ``:free`` or are
``kilo/kilo-auto/*``).
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import shutil
import subprocess
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

logger = logging.getLogger(__name__)

KILO_API_KEY_SECRET = "kilo_api_key"
HOME_DIRNAME = "taos-agent-kilo"

_KILO_FALLBACK_PATHS = ("/usr/local/bin/kilo", "~/.local/bin/kilo")

DEFAULT_MODEL = "kilo/kilo-auto/free"
TURN_TIMEOUT = 300.0


def _is_executable(path: Path) -> bool:
    try:
        return path.is_file() and os.access(path, os.X_OK)
    except OSError:
        return False


def resolve_kilo_binary() -> str | None:
    """``kilo`` on PATH, then ``/usr/local/bin/kilo``, then ``~/.local/bin/kilo``."""
    found = shutil.which("kilo")
    if found:
        return found
    for candidate in _KILO_FALLBACK_PATHS:
        path = Path(candidate).expanduser()
        if _is_executable(path):
            return str(path)
    return None


class KiloBinaryNotFoundError(RuntimeError):
    """The ``kilo`` binary is not installed or not reachable."""


class KiloKeyMissingError(RuntimeError):
    """The ``kilo_api_key`` secret is absent or empty."""


async def kilo_key_present(app_state) -> bool:
    """Whether the Secrets store holds the kilo_api_key secret. Presence only:
    the value is read and dropped, never logged or returned."""
    store = getattr(app_state, "secrets", None)
    if store is None:
        return False
    try:
        row = await store.get(KILO_API_KEY_SECRET)
    except Exception:  # noqa: BLE001 - a broken store means no key
        logger.warning("kilo_runtime: could not read the secrets store")
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
class KiloHarness:
    """One kilo home (a git repo under data_dir) plus the API key it holds."""

    home: Path
    key: str = field(repr=False)
    model: str = DEFAULT_MODEL
    binary: str = "kilo"
    turn_timeout: float = TURN_TIMEOUT

    @property
    def workspace(self) -> Path:
        return self.home / "workspace"

    def write_home(self) -> None:
        self.home.mkdir(parents=True, exist_ok=True)
        os.chmod(self.home, 0o700)
        self.workspace.mkdir(parents=True, exist_ok=True)
        # kilo refuses a non-git --dir and hangs on a live stdin, so the
        # workspace is a fresh git repo, created once when the home is made.
        if not (self.workspace / ".git").exists():
            subprocess.run(
                ["git", "init"], cwd=self.workspace, check=False,
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            )

    def _env(self) -> dict[str, str]:
        """A minimal environment: the controller's own env is not inherited by
        the agent's shell. The key reaches the child only as KILO_API_KEY."""
        env = {
            "HOME": str(self.home),
            "PATH": os.environ.get("PATH", "/usr/local/bin:/usr/bin:/bin"),
            "LANG": os.environ.get("LANG", "C.UTF-8"),
            "NO_COLOR": "1",
            "KILO_API_KEY": self.key,
        }
        if os.environ.get("TZ"):
            env["TZ"] = os.environ["TZ"]
        return env

    def _argv(self, session_id: str | None, message: str) -> list[str]:
        argv = [self.binary, "run", "--format", "json", "--model", self.model,
                "--dir", str(self.workspace)]
        if session_id:
            argv += ["-s", session_id]
        argv += [message]
        return argv

    async def run_turn(
        self,
        text: str,
        session_id: str | None = None,
        on_event: Callable[[dict], None] | None = None,
    ) -> TurnResult:
        """Run one ``kilo run --format json`` turn. Never raises for a failed
        turn (a missing binary raises :class:`KiloBinaryNotFoundError`)."""
        try:
            proc = await asyncio.create_subprocess_exec(
                *self._argv(session_id, text),
                cwd=str(self.workspace),
                env=self._env(),
                stdin=asyncio.subprocess.DEVNULL,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
        except FileNotFoundError as exc:
            raise KiloBinaryNotFoundError(f"kilo binary not found ({self.binary!r})") from exc

        events: list[dict] = []
        stdout_lines: list[str] = []
        reply_parts: list[str] = []
        found_session: list[str | None] = [None]
        error_message: list[str | None] = [None]

        async def _read_stdout() -> None:
            async for raw in proc.stdout:
                line = raw.decode("utf-8", "replace").strip()
                if not line:
                    continue
                stdout_lines.append(line)
                try:
                    evt = json.loads(line)
                except ValueError:
                    continue
                if not isinstance(evt, dict):
                    continue
                if found_session[0] is None and isinstance(evt.get("sessionID"), str):
                    found_session[0] = evt["sessionID"]
                if evt.get("type") == "text":
                    part = evt.get("part")
                    if isinstance(part, dict) and isinstance(part.get("text"), str):
                        reply_parts.append(part["text"])
                if evt.get("type") == "error":
                    err = evt.get("error")
                    if isinstance(err, dict):
                        data = err.get("data")
                        if isinstance(data, dict) and isinstance(data.get("message"), str):
                            error_message[0] = data["message"]
                events.append(evt)
                if on_event is not None:
                    try:
                        on_event(evt)
                    except Exception:  # noqa: BLE001 - a bad callback never kills a turn
                        logger.exception("kilo_runtime: on_event raised")

        async def _run() -> bytes:
            _, err = await asyncio.gather(_read_stdout(), proc.stderr.read())
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
                              detail=f"kilo turn timed out after {self.turn_timeout:g}s")
        except asyncio.CancelledError:
            try:
                proc.kill()
            except ProcessLookupError:
                pass
            raise
        stderr = err.decode("utf-8", "replace")
        reply = "".join(reply_parts)
        detail = ""
        if error_message[0] is not None:
            detail = error_message[0]
        elif proc.returncode != 0:
            tail = [ln for ln in (stderr + "\n" + "\n".join(stdout_lines)).splitlines() if ln.strip()]
            detail = tail[-1].strip()[:300] if tail else ""
        return TurnResult(returncode=proc.returncode or 0, reply=reply, detail=detail,
                          session_id=found_session[0])


def scrub_home(data_dir: str | Path) -> None:
    """Present for symmetry with :func:`tinyagentos.claude_code_runtime.scrub_home`.

    Deletes nothing: taOS never writes the key to disk (it is handed to the
    child in its environment), so there is no secret in the kilo home to scrub.
    """
