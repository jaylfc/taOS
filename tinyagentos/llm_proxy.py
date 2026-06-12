"""LiteLLM proxy management — hidden internal LLM gateway.

Honours the framework-agnostic runtime rule (see
``docs/design/framework-agnostic-runtime.md``): this proxy is the single
host-side entry point for LLM chat *and* embeddings, so every agent
container can point at one URL (``OPENAI_BASE_URL`` + optional
``TAOS_EMBEDDING_URL``) and swap frameworks without rewiring.
"""
from __future__ import annotations

import asyncio
import logging
import os
import signal
import subprocess
import time
from pathlib import Path

import httpx

from tinyagentos.providers import CLOUD_TYPES as CLOUD_BACKEND_TYPES
from tinyagentos.litellm_config import (
    EMBEDDING_ALIAS,
    _is_embedding_model,
    _discover_ollama_backends_concurrent,
    generate_litellm_config,
    get_litellm_master_key,
)

logger = logging.getLogger(__name__)


def _pid_alive(pid: int) -> bool:
    """Return True if ``pid`` is alive (signal 0 succeeds)."""
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        # Process exists but we don't own it — still alive.
        return True


def _pids_listening_on(port: int) -> list[int]:
    """Best-effort lookup of PIDs holding a TCP listen on ``port``.

    Uses ``lsof -ti:<port>`` which is available on macOS, most Linux
    distros, and the Fedora LXC we ship on the Pi. Returns ``[]`` when
    ``lsof`` is missing, errors, or reports nothing.
    """
    try:
        out = subprocess.check_output(
            ["lsof", "-ti", f":{port}"], text=True, stderr=subprocess.DEVNULL
        ).strip()
    except (subprocess.CalledProcessError, FileNotFoundError):
        return []
    pids: list[int] = []
    for line in out.split("\n"):
        line = line.strip()
        if not line:
            continue
        try:
            pids.append(int(line))
        except ValueError:
            continue
    return pids


class LLMProxy:
    """Manages LiteLLM proxy as a subprocess.

    When ``database_url`` is set, it's exported as ``DATABASE_URL`` to
    the LiteLLM subprocess so LiteLLM can connect to Postgres and issue
    per-agent virtual keys via ``/key/generate``. Without it LiteLLM
    runs in routing-only mode — virtual key endpoints return 5xx and
    the deployer falls back to the shared master key.
    """

    def __init__(
        self,
        port: int = 7834,
        config_dir: Path | None = None,
        database_url: str | None = None,
        local_token: str | None = None,
        registry=None,
        data_dir: Path | None = None,
    ):
        self.port = port
        self.config_dir = config_dir or Path("/tmp/taos-litellm")
        self.database_url = database_url
        # Local auth token for taOS callbacks (POST /api/trace). Exported
        # to the LiteLLM subprocess as ``TAOS_LOCAL_TOKEN`` so the custom
        # logger in ``tinyagentos.litellm_callback`` can authenticate to
        # the taOS bridge — without it, every llm_call event lands a 401
        # and trace rows never get ``llm_call`` entries.
        self.local_token = local_token
        # AppRegistry — when provided, generate_litellm_config can register
        # every installed model that targets a local backend as its own
        # model_name alias. Without this, an agent picker that says "use
        # gemma-4-e2b-gguf" would 400 on the proxy because the alias was
        # never created.
        self._registry = registry
        # data_dir drives the per-install master key file (.litellm_master_key).
        # When None, an in-memory key is used (acceptable in tests / routing-only mode).
        self._data_dir = data_dir
        self._process: subprocess.Popen | None = None

    @property
    def url(self) -> str:
        return f"http://localhost:{self.port}"

    def is_running(self) -> bool:
        """True iff we own a live LiteLLM subprocess.

        We never adopt a foreign process — ``start()`` terminates any
        stranger on our port and spawns its own — so the only running
        state worth reporting is "our Popen is still alive".
        """
        if not self._process:
            return False
        return self._process.poll() is None

    async def write_config(self, backends: list[dict]) -> Path:
        """Generate and write LiteLLM config file.

        Probes all ollama/rkllama backends concurrently so the
        per-backend 2s timeout does not compound serially.

        Also writes a sibling ``taos_callback.py`` shim so LiteLLM's
        ``get_instance_fn`` can load the CustomLogger instance via its
        config-dir-relative importer. The shim re-exports the instance
        from the installed ``tinyagentos`` package — keeping the real
        callback code in one place.
        """
        self.config_dir.mkdir(parents=True, exist_ok=True)
        discovered = await _discover_ollama_backends_concurrent(backends)
        config = generate_litellm_config(
            backends,
            registry=self._registry,
            master_key=get_litellm_master_key(self._data_dir),
            discovered=discovered,
        )
        config_path = self.config_dir / "litellm_config.yaml"

        import yaml
        config_path.write_text(yaml.dump(config, default_flow_style=False))

        shim_path = self.config_dir / "taos_callback.py"
        shim_path.write_text(
            "from tinyagentos.litellm_callback import taos_callback "
            "as proxy_handler_instance\n"
        )
        return config_path

    async def start(
        self,
        backends: list[dict],
        secrets: dict[str, str] | None = None,
    ) -> bool:
        """Start LiteLLM proxy with auto-generated config.

        If another process (a stale taOS, a manual launch) is already on
        our port, terminate it first — adopting it is unsafe because the
        foreign instance may have been started with a different master
        key or an outdated model config that the UI cannot update
        without a SIGHUP we have no PID for.

        ``secrets`` maps secret_name → value for every ``api_key_secret``
        referenced from ``backends``. Each entry is exported as an env
        var before the litellm subprocess starts so the ``os.environ/...``
        markers in the generated config resolve to real API keys.
        Without this, cloud providers authenticated via the secrets
        store return 401 from LiteLLM.
        """
        if self.is_running():
            return True

        # Detect any foreign process on our port and terminate it so we
        # can spawn with the current config + master key. Use
        # ``/health/readiness`` — ``/health`` gates on the master-key and
        # returns 401 without auth, which would look like a "port in use"
        # signal on a perfectly-fine LiteLLM and trigger a needless SIGKILL.
        try:
            async with httpx.AsyncClient(timeout=3) as client:
                resp = await client.get(f"{self.url}/health/readiness")
                port_in_use = resp.status_code < 500
        except Exception:
            port_in_use = False

        if port_in_use:
            pids = _pids_listening_on(self.port)
            if pids:
                logger.info(
                    "LiteLLM on port %d owned by foreign PID(s) %r — "
                    "terminated so taOS can spawn its own",
                    self.port,
                    pids,
                )
                for pid in pids:
                    try:
                        os.kill(pid, signal.SIGTERM)
                    except ProcessLookupError:
                        pass
                deadline = time.monotonic() + 5
                while time.monotonic() < deadline and any(_pid_alive(p) for p in pids):
                    await asyncio.sleep(0.2)
                for pid in pids:
                    if _pid_alive(pid):
                        try:
                            os.kill(pid, signal.SIGKILL)
                        except ProcessLookupError:
                            pass
            else:
                logger.warning(
                    "LiteLLM responding on port %d but no owning PID "
                    "discovered (lsof missing?) — spawn may fail to bind",
                    self.port,
                )

        config_path = await self.write_config(backends)

        # Resolve litellm binary from the same venv that's running
        # TinyAgentOS. systemd doesn't inherit the venv's bin/ on PATH, so
        # a bare "litellm" lookup fails even when the package is installed
        # in the venv. Falling back to PATH lets hand-run dev instances
        # still work.
        import shutil
        import sys
        venv_bin = Path(sys.executable).parent / "litellm"
        litellm_cmd = str(venv_bin) if venv_bin.exists() else shutil.which("litellm")
        if not litellm_cmd:
            logger.warning("LiteLLM not installed — proxy disabled. Install with: pip install litellm[proxy]")
            return False

        # Belt-and-braces: the yaml config already carries master_key, but
        # LiteLLM also honours the env var — exporting both guarantees
        # whichever path the subprocess reads first matches the value the
        # deployer uses when auth'ing /key/generate and agent requests.
        env = os.environ.copy()
        env["LITELLM_MASTER_KEY"] = get_litellm_master_key(self._data_dir)
        # Forward the local auth token so the TaosLiteLLMCallback inside
        # the subprocess can POST to taOS's /api/trace (otherwise 401).
        if self.local_token:
            env["TAOS_LOCAL_TOKEN"] = self.local_token
        # LiteLLM's CLI shells out to a bare ``prisma`` during startup to
        # detect whether Prisma is runnable before calling PrismaManager.
        # Under systemd the unit file doesn't put the venv's bin/ on PATH,
        # so that lookup raises FileNotFoundError and LiteLLM prints
        # "prisma package not found" and skips DB setup entirely. Prepend
        # the venv bin that hosts our litellm binary so the child resolves
        # both ``prisma`` and ``prisma-client-py``.
        venv_bin = str(Path(litellm_cmd).parent)
        existing_path = env.get("PATH", "")
        if venv_bin not in existing_path.split(os.pathsep):
            env["PATH"] = venv_bin + os.pathsep + existing_path if existing_path else venv_bin
        # DATABASE_URL enables Postgres-backed virtual keys. Without it
        # LiteLLM still routes chat/embeddings fine but /key/generate
        # returns a server error.
        if self.database_url:
            env["DATABASE_URL"] = self.database_url
        # Resolve every api_key_secret into a real env var so the
        # os.environ/<name> markers in the generated config resolve to
        # actual API keys. LiteLLM reads them by name at request time.
        if secrets:
            for name, value in secrets.items():
                if name and value:
                    env[name] = value

        # Capture LiteLLM's stderr to a sibling log file so boot failures
        # (prisma errors, config parse errors, model-router load errors)
        # are visible instead of silently discarded. stdout stays on
        # DEVNULL — it's mostly noisy per-request logs we don't need.
        stderr_log_path = config_path.parent / "litellm.stderr.log"
        stderr_handle = stderr_log_path.open("a", buffering=1)
        try:
            self._process = subprocess.Popen(
                [
                    litellm_cmd,
                    "--config", str(config_path),
                    "--port", str(self.port),
                    "--host", "127.0.0.1",
                ],
                stdout=subprocess.DEVNULL,
                stderr=stderr_handle,
                env=env,
            )
            # Wait for startup. LiteLLM on a fresh Pi DB runs
            # ``prisma migrate deploy`` against an empty database before
            # opening its HTTP port — that can take 45-60s on ARM.
            # Poll ``/health/readiness`` (public) rather than ``/health``
            # (requires master key → 401 for the polling client).
            for _ in range(120):
                await asyncio.sleep(1)
                try:
                    async with httpx.AsyncClient(timeout=3) as client:
                        resp = await client.get(f"{self.url}/health/readiness")
                        if resp.status_code == 200:
                            logger.info(f"LiteLLM proxy started on port {self.port}")
                            return True
                except Exception:
                    pass
            logger.error("LiteLLM proxy failed to start within 120s")
            return False
        except FileNotFoundError:
            logger.warning("LiteLLM not installed — proxy disabled. Install with: pip install litellm[proxy]")
            return False

    def stop(self):
        """Stop the LiteLLM proxy."""
        if self._process:
            self._process.terminate()
            try:
                self._process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self._process.kill()
            self._process = None
            logger.info("LiteLLM proxy stopped")

    async def reload_config(
        self,
        backends: list[dict],
        secrets: dict[str, str] | None = None,
    ) -> bool:
        """Rewrite the LiteLLM config with a new backend list and restart
        the proxy so it re-reads the file.

        Earlier this sent SIGHUP, but single-worker uvicorn (what LiteLLM
        runs as — no ``--workers``) does not register a SIGHUP handler,
        so the default action fires: the process terminates. A full
        stop+start is the only reliable way to pick up config changes.
        """
        new_path = await self.write_config(backends)
        if not self.is_running():
            return False
        logger.info("LiteLLM proxy restarting for config reload (%s)", new_path)
        self.stop()
        return await self.start(backends, secrets=secrets)

    async def create_agent_key(self, agent_name: str, models: list[str] | None = None,
                                max_budget: float | None = None) -> str | None:
        """Create a per-agent virtual key via LiteLLM API."""
        if not self.is_running():
            return None
        # LiteLLM virtual keys require a Postgres DB. Without one,
        # /key/generate returns a 500 "DB not connected" error that looks
        # alarming in logs even though the deployer's master-key fallback
        # handles it fine. Skip the round-trip in routing-only mode.
        if not self.database_url:
            return None
        try:
            async with httpx.AsyncClient(timeout=10) as client:
                body = {
                    "key_alias": f"taos-{agent_name}",
                    "models": models or ["default"],
                    "metadata": {"agent": agent_name, "managed_by": "tinyagentos"},
                }
                if max_budget is not None:
                    body["max_budget"] = max_budget
                resp = await client.post(f"{self.url}/key/generate", json=body,
                                          headers={"Authorization": f"Bearer {get_litellm_master_key(self._data_dir)}"})
                if resp.status_code == 200:
                    data = resp.json()
                    return data.get("key", data.get("token"))
                logger.warning(
                    "LiteLLM /key/generate returned %d for agent=%s body=%.200s",
                    resp.status_code, agent_name, resp.text,
                )
                return None
        except Exception as e:
            logger.warning(f"Failed to create LiteLLM key for {agent_name}: {e}")
        return None

    async def update_agent_key(self, key: str, models: list[str]) -> bool:
        """Re-scope an existing virtual key's allowed models via /key/update.

        Keeps the key VALUE unchanged (no container env push / restart needed):
        the framework's ``/v1/models`` with this key then reflects the new
        permitted set, so it natively sees exactly what the agent is allowed to
        use. Returns True on success. No-op (False) in routing-only mode (no DB)
        — there are no per-agent keys to scope there.
        """
        if not self.is_running() or not self.database_url or not key:
            return False
        if not models:
            # An empty scope is a caller error, not a request to allow nothing.
            # Refuse rather than silently substitute a bogus "default" model
            # (which would scope the key to a model that does not exist).
            logger.warning("update_agent_key called with empty models; refusing to re-scope key")
            return False
        try:
            async with httpx.AsyncClient(timeout=10) as client:
                resp = await client.post(
                    f"{self.url}/key/update",
                    json={"key": key, "models": models},
                    headers={"Authorization": f"Bearer {get_litellm_master_key(self._data_dir)}"},
                )
                if resp.status_code == 200:
                    return True
                logger.warning(
                    "LiteLLM /key/update returned %d body=%.200s",
                    resp.status_code, resp.text,
                )
        except Exception as e:
            logger.warning("Failed to update LiteLLM key models: %s", e)
        return False

    async def delete_agent_key(self, key: str) -> bool:
        """Delete a per-agent virtual key."""
        if not self.is_running():
            return False
        try:
            async with httpx.AsyncClient(timeout=10) as client:
                resp = await client.post(f"{self.url}/key/delete", json={"keys": [key]},
                                          headers={"Authorization": f"Bearer {get_litellm_master_key(self._data_dir)}"})
                if resp.status_code == 200:
                    return True
                logger.warning(
                    "LiteLLM /key/delete returned %d body=%.200s",
                    resp.status_code, resp.text,
                )
                return False
        except Exception:
            return False

    async def get_key_usage(self, key: str) -> dict | None:
        """Get usage stats for an agent's key."""
        if not self.is_running():
            return None
        try:
            async with httpx.AsyncClient(timeout=10) as client:
                resp = await client.get(f"{self.url}/key/info", params={"key": key},
                                         headers={"Authorization": f"Bearer {get_litellm_master_key(self._data_dir)}"})
                if resp.status_code == 200:
                    return resp.json()
                logger.warning(
                    "LiteLLM /key/info returned %d body=%.200s",
                    resp.status_code, resp.text,
                )
        except Exception:
            pass
        return None
