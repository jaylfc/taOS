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

# Map TinyAgentOS backend types to LiteLLM model prefixes
BACKEND_TYPE_MAP = {
    "ollama": "ollama",
    "rkllama": "ollama",  # rkllama is ollama-compatible on /api/embed too
    "llama-cpp": "openai",
    "vllm": "openai",
    "exo": "openai",
    "mlx": "openai",
    "openai": "openai",
    "anthropic": "anthropic",
    "openrouter": "openrouter",
    "kilocode": "openai",  # kilocode is OpenAI-compatible; api_base set explicitly
    "openai-compatible": "openai",  # user-supplied OpenAI-compatible endpoint; api_base required
}

# Chat prefix is different from the embedding prefix for ollama-compat
# backends: ollama_chat uses /api/chat, plain ollama uses /api/generate and
# /api/embed. LiteLLM needs the right one to route requests correctly.
CHAT_BACKEND_TYPE_MAP = {
    **BACKEND_TYPE_MAP,
    "ollama": "ollama_chat",
    "rkllama": "ollama_chat",
}

# Cloud provider types that may serve multiple named models and require
# per-model model_list entries so agents can route by exact model id.
CLOUD_BACKEND_TYPES = {"openai", "anthropic", "openrouter", "kilocode", "openai-compatible"}

# Canonical alias the deployer injects into agent containers as
# TAOS_EMBEDDING_MODEL. Agents that want an embedding call this name and
# LiteLLM routes it to whatever concrete embedding model the host has.
# See docs/design/framework-agnostic-runtime.md.
EMBEDDING_ALIAS = "taos-embedding-default"

# Shared master key used for LiteLLM auth. When LiteLLM runs without a
# Postgres DB it cannot issue per-agent virtual keys, so every client
# (openclaw gateway, host-side key admin calls) authenticates with this
# single value. Written into the config yaml AND exported into the
# litellm subprocess env so whichever source LiteLLM reads first agrees.
TAOS_LITELLM_MASTER_KEY = "sk-taos-master"


def _is_embedding_model(name: str) -> bool:
    """Classify a model name as embedding vs chat.

    Heuristic match against the common embedding-model families. LiteLLM
    has no reliable way to ask a backend "is this model an embedder?" so
    we infer from the slug. Known families: anything containing ``embed``
    (nomic-embed, qwen3-embedding, mxbai-embed-large), plus the BGE, GTE,
    E5, and arctic-embed families whose canonical names don't always
    include the word.

    Rerankers include ``rerank`` in the slug and are always excluded —
    they're a different endpoint shape that LiteLLM doesn't front today.
    """
    n = name.lower()
    if "rerank" in n:
        return False
    if "embed" in n:
        return True
    # Known embedding families where the slug doesn't include "embed"
    # verbatim. Match on dash-separated prefixes so "bge-something" hits
    # but a hypothetical "bgem-chat" doesn't.
    for prefix in ("bge-", "gte-", "e5-", "arctic-"):
        if n.startswith(prefix):
            return True
    return False


def _discover_ollama_models(url: str, timeout: float = 2.0) -> list[str]:
    """Probe an ollama-compatible backend for its loaded model names.

    Returns ``[]`` on any failure (backend down, network error, schema
    drift) — the caller treats an empty list as "no models to auto-wire"
    and falls back to the ``default`` alias. Kept sync because
    ``generate_litellm_config`` is called from both sync and async
    contexts and adding a timeout-bounded probe here is simpler than
    plumbing async all the way through the subscriber chain.
    """
    try:
        resp = httpx.get(f"{url.rstrip('/')}/api/tags", timeout=timeout)
        if resp.status_code != 200:
            return []
        data = resp.json()
        return [m.get("name", "") for m in data.get("models", []) if m.get("name")]
    except Exception as exc:
        logger.debug("ollama model probe at %s failed: %s", url, exc)
        return []


def generate_litellm_config(backends: list[dict], default_model: str = "default") -> dict:
    """Generate LiteLLM config from TinyAgentOS backend list.

    Emits two kinds of model_list entries:

    - Chat entries under the ``default`` alias, routing through each
      ollama-compatible backend. Frameworks using the OpenAI SDK against
      ``OPENAI_BASE_URL`` hit these with ``model="default"``.
    - Embedding entries discovered by probing each ollama-compatible
      backend's ``/api/tags``. The first embedding model found is also
      aliased as ``taos-embedding-default`` so the deployer can inject
      a stable model name into every agent container via
      ``TAOS_EMBEDDING_MODEL``. Entries carry
      ``model_info.mode: embedding`` so LiteLLM routes
      ``/v1/embeddings`` requests to them instead of chat.
    """
    model_list = []
    sorted_backends = sorted(backends, key=lambda b: b.get("priority", 99))
    aliased_embedding_claimed = False

    for backend in sorted_backends:
        backend_type = backend.get("type", "ollama")
        # Loudly flag cloud-type entries missing url/models so the next
        # round of silent drops (the kilocode regression) is visible in
        # logs instead of surfacing only as a broken agent much later.
        if backend_type in CLOUD_BACKEND_TYPES:
            if not backend.get("url") or not backend.get("models"):
                logger.warning(
                    "backend %s skipped — missing url or models (type=%s)",
                    backend.get("name"),
                    backend_type,
                )
        prefix = CHAT_BACKEND_TYPE_MAP.get(backend_type, "openai")
        url = backend.get("url", "").rstrip("/")
        model_name = backend.get("model", "default")

        litellm_params = {
            "model": f"{prefix}/{model_name}",
        }

        # Set api_base for local/self-hosted backends and openai-compatible
        if backend_type in ("ollama", "rkllama", "llama-cpp", "vllm", "exo", "mlx", "openai-compatible"):
            litellm_params["api_base"] = url

        # API key from secrets reference
        if backend.get("api_key_secret"):
            litellm_params["api_key"] = f"os.environ/{backend['api_key_secret']}"
        elif backend.get("api_key"):
            litellm_params["api_key"] = backend["api_key"]

        # For cloud backends with a declared models list, register each model
        # by its exact id so agents can route requests to a specific model.
        if backend_type in CLOUD_BACKEND_TYPES:
            declared_models: list[str] = []
            for m in backend.get("models") or []:
                if isinstance(m, dict):
                    mid = m.get("id") or m.get("name") or ""
                else:
                    mid = str(m)
                if mid:
                    declared_models.append(mid)

            for model_id in declared_models:
                per_model_params: dict = {
                    "model": f"{prefix}/{model_id}",
                }
                # kilocode isn't a native LiteLLM provider — must set api_base
                if backend_type == "kilocode":
                    per_model_params["api_base"] = url
                elif url and backend_type not in ("openai", "anthropic"):
                    # For openrouter and other pass-through types, set api_base
                    # when an explicit url is provided
                    per_model_params["api_base"] = url
                # API key resolution
                if backend.get("api_key_secret"):
                    per_model_params["api_key"] = f"os.environ/{backend['api_key_secret']}"
                elif backend.get("api_key"):
                    per_model_params["api_key"] = backend["api_key"]
                model_list.append({
                    "model_name": model_id,
                    "litellm_params": per_model_params,
                    "metadata": {
                        "priority": backend.get("priority", 99),
                        "backend_name": backend.get("name", ""),
                    },
                })

        model_list.append({
            "model_name": default_model,
            "litellm_params": litellm_params,
            "metadata": {
                "priority": backend.get("priority", 99),
                "backend_name": backend.get("name", ""),
            },
        })

        # Auto-discover embedding models on ollama-compatible backends and
        # register each as its own LiteLLM entry. The first embedding model
        # found across all backends also claims the stable
        # ``taos-embedding-default`` alias so containers have one name to
        # inject regardless of which rkllama box holds the model.
        if backend_type in ("ollama", "rkllama"):
            discovered = _discover_ollama_models(url)
            for discovered_name in discovered:
                if not _is_embedding_model(discovered_name):
                    continue
                embed_params = {
                    "model": f"ollama/{discovered_name}",
                    "api_base": url,
                }
                model_list.append({
                    "model_name": discovered_name,
                    "litellm_params": embed_params,
                    "model_info": {"mode": "embedding"},
                    "metadata": {
                        "priority": backend.get("priority", 99),
                        "backend_name": backend.get("name", ""),
                    },
                })
                if not aliased_embedding_claimed:
                    model_list.append({
                        "model_name": EMBEDDING_ALIAS,
                        "litellm_params": dict(embed_params),
                        "model_info": {"mode": "embedding"},
                        "metadata": {
                            "priority": backend.get("priority", 99),
                            "backend_name": backend.get("name", ""),
                            "aliases": discovered_name,
                        },
                    })
                    aliased_embedding_claimed = True

    return {
        "model_list": model_list,
        "router_settings": {
            "routing_strategy": "simple-shuffle",
            "num_retries": 2,
            "timeout": 120,
            "enable_pre_call_checks": False,
        },
        "general_settings": {
            "master_key": TAOS_LITELLM_MASTER_KEY,
            "background_health_checks": False,
            "disable_spend_logs": True,
        },
        # LiteLLM's proxy reads custom logger classes from
        # ``litellm_settings.callbacks``. The loader (get_instance_fn) resolves
        # the dotted path relative to the config file's directory — so the
        # sibling ``taos_callback.py`` shim written by ``write_config`` below
        # imports our installed module and re-exports the instance.
        "litellm_settings": {
            "callbacks": "taos_callback.proxy_handler_instance",
        },
    }


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
        port: int = 4000,
        config_dir: Path | None = None,
        database_url: str | None = None,
        local_token: str | None = None,
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

    def write_config(self, backends: list[dict]) -> Path:
        """Generate and write LiteLLM config file.

        Also writes a sibling ``taos_callback.py`` shim so LiteLLM's
        ``get_instance_fn`` can load the CustomLogger instance via its
        config-dir-relative importer. The shim re-exports the instance
        from the installed ``tinyagentos`` package — keeping the real
        callback code in one place.
        """
        self.config_dir.mkdir(parents=True, exist_ok=True)
        config = generate_litellm_config(backends)
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

        config_path = self.write_config(backends)

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
        env["LITELLM_MASTER_KEY"] = TAOS_LITELLM_MASTER_KEY
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
        new_path = self.write_config(backends)
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
                                          headers={"Authorization": f"Bearer {TAOS_LITELLM_MASTER_KEY}"})
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

    async def delete_agent_key(self, key: str) -> bool:
        """Delete a per-agent virtual key."""
        if not self.is_running():
            return False
        try:
            async with httpx.AsyncClient(timeout=10) as client:
                resp = await client.post(f"{self.url}/key/delete", json={"keys": [key]},
                                          headers={"Authorization": f"Bearer {TAOS_LITELLM_MASTER_KEY}"})
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
                                         headers={"Authorization": f"Bearer {TAOS_LITELLM_MASTER_KEY}"})
                if resp.status_code == 200:
                    return resp.json()
                logger.warning(
                    "LiteLLM /key/info returned %d body=%.200s",
                    resp.status_code, resp.text,
                )
        except Exception:
            pass
        return None
