"""opencode host runtime — manage a host-side `opencode serve` process and
drive one agent turn through the opencode adapter.

``OpenCodeServer`` owns one `opencode serve` subprocess.  It writes the LiteLLM
provider config into ``$HOME/.config/opencode/opencode.json``, spawns the
server, and polls ``GET /doc`` until healthy (or raises ``TimeoutError``).

``drive_turn`` runs one turn through :class:`OpenCodeAdapter`, streaming reply
dicts to a sink.  Mirrors :mod:`tinyagentos.openclaw_acp_runtime` — never raises
out of ``drive_turn``; any failure degrades to an ``error`` reply.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import shutil
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

import httpx

from tinyagentos.adapters.opencode_adapter import OpenCodeAdapter, OpenCodeConfig

logger = logging.getLogger(__name__)


class OpenCodeBinaryNotFoundError(RuntimeError):
    """Raised when the ``opencode`` binary cannot be located anywhere.

    opencode's official installer (``curl -fsSL https://opencode.ai/install |
    bash``) drops the binary at ``~/.opencode/bin/opencode`` and makes it
    reachable from a terminal by appending a PATH export to the user's shell
    rc file (``.bashrc``/``.zshrc``). Those rc files are only sourced by
    interactive shells -- the taOS backend normally runs as a systemd service,
    which never sources them, so ``opencode`` can be "installed system-wide"
    and still invisible to the service's PATH. See :func:`resolve_opencode_binary`.
    """


# Standard system-wide install locations to probe beyond PATH. The controller
# runs as the unprivileged ``taos`` service user (a non-login systemd service),
# so it neither sources shell rc PATH exports nor shares a home with whoever
# ran opencode's installer.
_OPENCODE_SYSTEM_PATHS = (
    "/usr/local/bin/opencode",
    "/usr/bin/opencode",
    "/opt/opencode/bin/opencode",
)


def _opencode_candidate_paths() -> list[Path]:
    """Ordered candidate binary locations to probe after PATH lookup.

    Only **trusted** locations: root-controlled system paths, root's own home,
    and the ``taos`` service user's own home. We deliberately do NOT probe
    arbitrary users' ``~/.opencode/bin``: taOS is multi-user, and executing a
    binary from a non-privileged user's home would let that user plant a
    malicious ``opencode`` and escalate to the service account. An operator who
    installed opencode into their own home points at it explicitly via the
    ``TAOS_OPENCODE_BIN`` env override, or installs it to ``/usr/local/bin``.

    Broken out from :func:`resolve_opencode_binary` so tests can neutralise the
    host filesystem.
    """
    candidates: list[Path] = [Path.home() / ".opencode" / "bin" / "opencode"]
    candidates += [Path(p) for p in _OPENCODE_SYSTEM_PATHS]
    # root's own home is writable only by root, so a binary there is trusted.
    candidates.append(Path("/root/.opencode/bin/opencode"))
    return candidates


def resolve_opencode_binary() -> str | None:
    """Locate the opencode binary, working around the PATH gap above.

    Checks, in order (trusted locations only):
      1. ``TAOS_OPENCODE_BIN`` env var -- explicit operator override.
      2. ``shutil.which("opencode")`` -- covers PATH already containing it.
      3. The service user's own ``~/.opencode/bin/opencode``.
      4. Root-controlled locations: standard system paths + ``/root/.opencode``.

    An operator who installed opencode under their own (non-root) home sets
    ``TAOS_OPENCODE_BIN`` or installs to ``/usr/local/bin`` -- we don't probe
    arbitrary user homes, since running a binary from a non-privileged user's
    home on a multi-user box is a privilege-escalation vector (#1616).

    Returns the resolved path, or ``None`` if opencode isn't installed
    anywhere we know to look.
    """
    override = os.environ.get("TAOS_OPENCODE_BIN")
    if override:
        if _is_executable(Path(override)):
            return override
        logger.warning(
            "TAOS_OPENCODE_BIN=%s is not an executable file; ignoring it and "
            "falling back to PATH and standard locations.", override,
        )
    found = shutil.which("opencode")
    if found:
        return found
    for candidate in _opencode_candidate_paths():
        if _is_executable(candidate):
            return str(candidate)
    return None


def _is_executable(path: Path) -> bool:
    """True if ``path`` is an existing executable file. Swallows OSError so an
    inaccessible candidate (e.g. ``/root/.opencode/...`` when not running as
    root -- ``is_file()`` raises PermissionError there) is treated as absent
    rather than aborting resolution."""
    try:
        return path.is_file() and os.access(path, os.X_OK)
    except OSError:
        return False


# ---------------------------------------------------------------------------
# Config + server
# ---------------------------------------------------------------------------

@dataclass
class OpenCodeServerConfig:
    """Configuration for launching a host-side opencode server."""

    home: str
    """Home directory; opencode config is written to ``{home}/.config/opencode/``."""

    port: int
    """Port for ``opencode serve``."""

    server_password: str | None
    """If set, ``OPENCODE_SERVER_PASSWORD`` env var is passed and Basic auth is used."""

    litellm_base_url: str
    """Base URL of the taOS LiteLLM proxy, e.g. ``http://127.0.0.1:7834/v1``."""

    litellm_key: str
    """API key for the LiteLLM proxy (the agent's own virtual key)."""

    model_ids: list[str]
    """Model IDs to expose under the ``litellm`` provider, e.g. ``["gpt-4o"]``."""

    binary: str = "opencode"
    """Path or name of the opencode binary."""


class OpenCodeServer:
    """Manage one host ``opencode serve`` process.

    Typical usage::

        server = OpenCodeServer(cfg)
        await server.ensure_running()
        # … use server.base_url …
        await server.stop()
    """

    def __init__(self, config: OpenCodeServerConfig) -> None:
        self._cfg = config
        self._proc: asyncio.subprocess.Process | None = None
        # Server output is redirected to this file handle (never PIPE — an
        # unread pipe deadlocks the long-lived server once its buffer fills).
        self._log_fh = None

    # ---------------------------------------------------------------- config

    def write_config(self) -> None:
        """Write ``{home}/.config/opencode/opencode.json`` with the LiteLLM
        provider block.  Creates parent directories as needed.  Idempotent and
        unit-testable (no subprocess involvement).
        """
        config_dir = Path(self._cfg.home) / ".config" / "opencode"
        config_dir.mkdir(parents=True, exist_ok=True)
        models = {mid: {} for mid in self._cfg.model_ids}
        payload = {
            "$schema": "https://opencode.ai/config.json",
            "provider": {
                "litellm": {
                    "npm": "@ai-sdk/openai-compatible",
                    "name": "LiteLLM",
                    "options": {
                        "baseURL": self._cfg.litellm_base_url,
                        "apiKey": self._cfg.litellm_key,
                    },
                    "models": models,
                }
            },
        }
        config_path = config_dir / "opencode.json"
        config_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        # The config embeds the agent's LiteLLM key in plaintext — keep it
        # owner-only (mirrors install_hermes.sh's chmod 600 on ~/.hermes/.env).
        try:
            os.chmod(config_path, 0o600)
        except OSError:
            logger.debug("opencode_runtime: could not chmod %s", config_path)
        logger.debug("opencode_runtime: wrote config to %s", config_path)

    # ---------------------------------------------------------------- lifecycle

    @property
    def base_url(self) -> str:
        """HTTP base URL of the running server."""
        return f"http://127.0.0.1:{self._cfg.port}"

    def is_running(self) -> bool:
        """True if the server process exists and has not yet exited."""
        return self._proc is not None and self._proc.returncode is None

    async def ensure_running(
        self,
        *,
        deadline_s: float = 20.0,
        poll_s: float = 0.5,
    ) -> None:
        """Idempotent: start the server if it is not already healthy.

        If our process is alive AND ``GET /doc`` returns 200, return immediately.
        Otherwise write the config, spawn ``opencode serve``, and poll until healthy.

        Raises:
            TimeoutError: if the server does not become healthy within *deadline_s*.
        """
        # Fast path: already running and healthy.
        if self.is_running() and await self._health_check():
            return

        # A live-but-unhealthy child must be reaped before respawning, or we
        # orphan it and the new server collides on the same port.
        if self.is_running():
            await self.stop()

        self.write_config()

        env = {
            **os.environ,
            "HOME": self._cfg.home,
        }
        if self._cfg.server_password:
            env["OPENCODE_SERVER_PASSWORD"] = self._cfg.server_password

        # Redirect output to a log file rather than PIPE: a long-lived server
        # with an unread PIPE deadlocks once the OS buffer fills, and we still
        # want the serve logs for diagnosing the host taOS agent.
        log_path = Path(self._cfg.home) / ".config" / "opencode" / "serve.log"
        self._log_fh = open(log_path, "ab")
        try:
            os.chmod(log_path, 0o600)
        except OSError:
            pass

        # Resolve the bare "opencode" default past the PATH gap described in
        # OpenCodeBinaryNotFoundError; an explicit binary override (tests, or
        # a future config knob) is used as-is.
        binary = self._cfg.binary
        if binary == "opencode":
            binary = resolve_opencode_binary() or binary

        try:
            self._proc = await asyncio.create_subprocess_exec(
                binary,
                "serve",
                "--port", str(self._cfg.port),
                "--hostname", "127.0.0.1",
                stdout=self._log_fh,
                stderr=asyncio.subprocess.STDOUT,
                env=env,
            )
        except FileNotFoundError as exc:
            raise OpenCodeBinaryNotFoundError(
                f"opencode binary not found (tried {binary!r}). Install it with: "
                "curl -fsSL https://opencode.ai/install | bash"
            ) from exc
        logger.info(
            "opencode_runtime: spawned pid=%s port=%d",
            self._proc.pid, self._cfg.port,
        )

        # Poll GET /doc until 200 or a wall-clock deadline. Using the loop clock
        # (not a poll-count) so a slow health check can't overrun deadline_s.
        deadline = asyncio.get_running_loop().time() + deadline_s
        while asyncio.get_running_loop().time() < deadline:
            if await self._health_check():
                logger.info("opencode_runtime: server healthy")
                return
            await asyncio.sleep(poll_s)

        raise TimeoutError(
            f"opencode server on port {self._cfg.port} did not become healthy "
            f"within {deadline_s}s"
        )

    async def stop(self) -> None:
        """Terminate the server process.  Safe to call when not running."""
        proc = self._proc
        if proc is not None and proc.returncode is None:
            try:
                proc.terminate()
                try:
                    await asyncio.wait_for(proc.wait(), timeout=5.0)
                except asyncio.TimeoutError:
                    try:
                        proc.kill()
                        await proc.wait()
                    except ProcessLookupError:
                        pass
            except ProcessLookupError:
                pass
        # Close the serve-log handle (opened per spawn).
        if self._log_fh is not None:
            try:
                self._log_fh.close()
            except OSError:
                pass
            self._log_fh = None

    # ---------------------------------------------------------------- health

    async def _health_check(self) -> bool:
        """Return True if ``GET {base_url}/doc`` responds with 200."""
        auth = None
        if self._cfg.server_password:
            auth = ("opencode", self._cfg.server_password)
        try:
            async with httpx.AsyncClient(auth=auth, timeout=2.0) as client:
                resp = await client.get(f"{self.base_url}/doc")
                return resp.status_code == 200
        except Exception:
            return False


# ---------------------------------------------------------------------------
# Turn driver
# ---------------------------------------------------------------------------

async def drive_turn(
    text: str,
    trace_id: str | None,
    sink,
    *,
    base_url: str,
    model_id: str,
    model_provider_id: str = "litellm",
    server_password: str | None = None,
    adapter_factory: Callable[..., OpenCodeAdapter] = OpenCodeAdapter,
    turn_timeout: float = 300.0,
) -> None:
    """Run one opencode turn, streaming reply dicts to *sink*.

    Mirrors :func:`tinyagentos.openclaw_acp_runtime.drive_turn` in defensive
    style: never raises.  Any failure degrades to exactly one
    ``{"kind":"error",...}`` dict delivered to the sink.

    The turn is bounded by *turn_timeout* (default 300 s).  If the LLM call
    takes longer, an ``asyncio.TimeoutError`` is raised inside the adapter
    operations and degraded to an ``error`` reply.  This prevents an HTTP
    connection from blocking indefinitely when the LLM backend hangs.

    Args:
        text:              User message text.
        trace_id:          Optional trace id forwarded on every reply.
        sink:              Async or sync callable that receives reply dicts.
        base_url:          HTTP base URL of the opencode server.
        model_id:          opencode model ID (e.g. ``"gpt-4o"``).
        model_provider_id: opencode provider ID (default ``"litellm"``).
        server_password:   If set, HTTP Basic auth password (username ``opencode``).
        adapter_factory:   Injectable for tests; defaults to :class:`OpenCodeAdapter`.
        turn_timeout:      Seconds before the turn is cancelled (default 300).
    """
    if turn_timeout <= 0:
        raise ValueError(
            f"turn_timeout must be positive, got {turn_timeout}"
        )
    cfg = OpenCodeConfig(
        base_url=base_url,
        server_password=server_password,
        model_provider_id=model_provider_id,
        model_id=model_id,
    )
    adapter = None
    # Track whether an error reply was already emitted (e.g. by the adapter
    # returning a non-200 status before the timeout fires) so we don't
    # violate the "exactly one error" contract.
    _error_emitted: bool = False

    def _sink(reply: dict) -> None:
        nonlocal _error_emitted
        if reply.get("kind") == "error":
            _error_emitted = True
        return sink(reply)

    try:
        adapter = adapter_factory(cfg, _sink)
        async with asyncio.timeout(turn_timeout):
            await adapter.ensure_session()
            await adapter.prompt(text, trace_id)
    except TimeoutError:
        logger.error(
            "opencode_runtime: drive_turn timed out after %.1fs", turn_timeout,
        )
        if not _error_emitted:
            try:
                reply: dict = {
                    "kind": "error",
                    "trace_id": trace_id,
                    "error": f"agent turn timed out (limit: {turn_timeout:g}s)",
                }
                res = sink(reply)
                if asyncio.iscoroutine(res):
                    await res
            except Exception:
                logger.exception("opencode_runtime: error reply also failed")
    except Exception:
        logger.exception("opencode_runtime: drive_turn failed")
        if not _error_emitted:
            try:
                reply: dict = {"kind": "error", "trace_id": trace_id, "error": "agent turn failed (opencode transport)"}
                res = sink(reply)
                if asyncio.iscoroutine(res):
                    await res
            except Exception:
                logger.exception("opencode_runtime: error reply also failed")
    finally:
        if adapter is not None:
            try:
                await adapter.close()
            except Exception:
                logger.exception("opencode_runtime: adapter close failed")
