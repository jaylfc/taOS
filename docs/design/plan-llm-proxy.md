# Abstraction Plan 1: LLM Proxy (LiteLLM Integration)

**Status:** Implemented — this plan has landed; see the feature on `master` for the current state.

**Amended:** 2026-04-11 — the LiteLLM config follows **backend-driven
discovery**. Rather than generating a static config once at startup from
the backend list, TinyAgentOS regenerates the LiteLLM config whenever the
live `BackendCatalog` changes (backend comes up, model loads/unloads,
worker joins/leaves). The proxy routes only to models that are currently
advertised as ready. See
[resource-scheduler.md §Backend-driven discovery](resource-scheduler.md).

**Goal:** Run LiteLLM as a hidden internal proxy so all agent frameworks access models via a single OpenAI-compatible endpoint with per-agent virtual keys, auto-configured from TinyAgentOS backend config.

**Architecture:** LiteLLM runs on localhost:4000 as a subprocess managed by TinyAgentOS. On startup, TinyAgentOS generates LiteLLM config from its backend list. When agents are deployed, TinyAgentOS creates per-agent virtual keys via LiteLLM's API and injects them as OPENAI_API_KEY env vars.

**Tech Stack:** litellm (Python package), httpx for API calls, existing backend_adapters.py for health checks, existing config.py for backend definitions.

**Spec:** `docs/specs/2026-04-06-abstraction-layers-design.md` section 2.

---

## File Map

```
tinyagentos/
├── tinyagentos/
│   ├── llm_proxy.py              # LiteLLM lifecycle: start, stop, configure, create keys
│   ├── routes/providers.py       # Provider management UI + API (test connection, CRUD)
│   └── templates/providers.html  # Provider management page
├── tests/
│   ├── test_llm_proxy.py         # LiteLLM config generation, key management
│   └── test_routes_providers.py  # Provider API tests
```

---

### Task 1: LiteLLM Config Generator

**Files:**
- Create: `tinyagentos/tinyagentos/llm_proxy.py`
- Create: `tinyagentos/tests/test_llm_proxy.py`

- [ ] **Step 1: Write failing tests**

```python
# tests/test_llm_proxy.py
import pytest
from tinyagentos.llm_proxy import generate_litellm_config, LLMProxy


class TestConfigGeneration:
    def test_generates_config_from_backends(self):
        backends = [
            {"name": "fedora-gpu", "type": "ollama", "url": "http://fedora:11434", "priority": 1},
            {"name": "local-npu", "type": "rkllama", "url": "http://localhost:7833", "priority": 3},
        ]
        config = generate_litellm_config(backends)
        assert "model_list" in config
        assert len(config["model_list"]) >= 2
        # First entry should be highest priority
        assert config["model_list"][0]["litellm_params"]["api_base"] == "http://fedora:11434"

    def test_empty_backends_returns_empty_model_list(self):
        config = generate_litellm_config([])
        assert config["model_list"] == []

    def test_ollama_backend_uses_ollama_prefix(self):
        backends = [{"name": "local", "type": "ollama", "url": "http://localhost:11434", "priority": 1}]
        config = generate_litellm_config(backends)
        model_param = config["model_list"][0]["litellm_params"]["model"]
        assert model_param.startswith("ollama/") or model_param.startswith("ollama_chat/")

    def test_openai_backend_uses_direct_model(self):
        backends = [{"name": "cloud", "type": "openai", "url": "https://api.openai.com", "priority": 1, "api_key_secret": "openai-key"}]
        config = generate_litellm_config(backends)
        assert "api_base" not in config["model_list"][0]["litellm_params"] or config["model_list"][0]["litellm_params"]["api_base"] == "https://api.openai.com"

    def test_rkllama_treated_as_ollama_compat(self):
        backends = [{"name": "npu", "type": "rkllama", "url": "http://localhost:7833", "priority": 1}]
        config = generate_litellm_config(backends)
        # rkllama is ollama-compatible
        model_param = config["model_list"][0]["litellm_params"]["model"]
        assert "ollama" in model_param.lower() or config["model_list"][0]["litellm_params"].get("api_base")


class TestLLMProxy:
    def test_proxy_not_running_initially(self):
        proxy = LLMProxy(port=14000)
        assert not proxy.is_running()

    def test_proxy_url(self):
        proxy = LLMProxy(port=14000)
        assert proxy.url == "http://localhost:14000"
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
cd /home/jay/tinyagentos && pytest tests/test_llm_proxy.py -v
```

Expected: FAIL — `ModuleNotFoundError`

- [ ] **Step 3: Implement LLM proxy module**

```python
# tinyagentos/llm_proxy.py
"""LiteLLM proxy management — hidden internal LLM gateway."""
from __future__ import annotations

import asyncio
import json
import logging
import subprocess
import time
from pathlib import Path

import httpx

logger = logging.getLogger(__name__)

# Map TinyAgentOS backend types to LiteLLM model prefixes
BACKEND_TYPE_MAP = {
    "ollama": "ollama_chat",
    "rkllama": "ollama_chat",  # rkllama is ollama-compatible
    "llama-cpp": "openai",
    "vllm": "openai",
    "exo": "openai",
    "mlx": "openai",
    "openai": "openai",
    "anthropic": "anthropic",
}


def generate_litellm_config(backends: list[dict], default_model: str = "default") -> dict:
    """Generate LiteLLM config from TinyAgentOS backend list."""
    model_list = []
    sorted_backends = sorted(backends, key=lambda b: b.get("priority", 99))

    for backend in sorted_backends:
        backend_type = backend.get("type", "ollama")
        prefix = BACKEND_TYPE_MAP.get(backend_type, "openai")
        url = backend.get("url", "").rstrip("/")
        model_name = backend.get("model", "default")

        litellm_params = {
            "model": f"{prefix}/{model_name}",
        }

        # Set api_base for local/self-hosted backends
        if backend_type in ("ollama", "rkllama", "llama-cpp", "vllm", "exo", "mlx"):
            litellm_params["api_base"] = url

        # API key from secrets reference
        if backend.get("api_key_secret"):
            litellm_params["api_key"] = f"os.environ/{backend['api_key_secret']}"
        elif backend.get("api_key"):
            litellm_params["api_key"] = backend["api_key"]

        model_list.append({
            "model_name": default_model,
            "litellm_params": litellm_params,
            "metadata": {
                "priority": backend.get("priority", 99),
                "backend_name": backend.get("name", ""),
            },
        })

    return {
        "model_list": model_list,
        "router_settings": {
            "routing_strategy": "latency-based-routing",
            "num_retries": 2,
            "timeout": 120,
        },
    }


class LLMProxy:
    """Manages LiteLLM proxy as a subprocess."""

    def __init__(self, port: int = 4000, config_dir: Path | None = None):
        self.port = port
        self.config_dir = config_dir or Path("/tmp/taos-litellm")
        self._process: subprocess.Popen | None = None

    @property
    def url(self) -> str:
        return f"http://localhost:{self.port}"

    def is_running(self) -> bool:
        if not self._process:
            return False
        return self._process.poll() is None

    def write_config(self, backends: list[dict]) -> Path:
        """Generate and write LiteLLM config file."""
        self.config_dir.mkdir(parents=True, exist_ok=True)
        config = generate_litellm_config(backends)
        config_path = self.config_dir / "litellm_config.yaml"

        import yaml
        config_path.write_text(yaml.dump(config, default_flow_style=False))
        return config_path

    async def start(self, backends: list[dict]) -> bool:
        """Start LiteLLM proxy with auto-generated config."""
        if self.is_running():
            return True

        config_path = self.write_config(backends)

        try:
            self._process = subprocess.Popen(
                [
                    "litellm",
                    "--config", str(config_path),
                    "--port", str(self.port),
                    "--host", "127.0.0.1",
                    "--detailed_debug",
                ],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            # Wait for startup
            for _ in range(30):
                await asyncio.sleep(1)
                try:
                    async with httpx.AsyncClient(timeout=3) as client:
                        resp = await client.get(f"{self.url}/health")
                        if resp.status_code == 200:
                            logger.info(f"LiteLLM proxy started on port {self.port}")
                            return True
                except Exception:
                    pass
            logger.error("LiteLLM proxy failed to start within 30s")
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

    async def create_agent_key(self, agent_name: str, models: list[str] | None = None,
                                max_budget: float | None = None) -> str | None:
        """Create a per-agent virtual key via LiteLLM API."""
        if not self.is_running():
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
                                          headers={"Authorization": "Bearer sk-taos-master"})
                if resp.status_code == 200:
                    data = resp.json()
                    return data.get("key", data.get("token"))
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
                                          headers={"Authorization": "Bearer sk-taos-master"})
                return resp.status_code == 200
        except Exception:
            return False

    async def get_key_usage(self, key: str) -> dict | None:
        """Get usage stats for an agent's key."""
        if not self.is_running():
            return None
        try:
            async with httpx.AsyncClient(timeout=10) as client:
                resp = await client.get(f"{self.url}/key/info", params={"key": key},
                                         headers={"Authorization": "Bearer sk-taos-master"})
                if resp.status_code == 200:
                    return resp.json()
        except Exception:
            pass
        return None
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
pytest tests/test_llm_proxy.py -v
```

Expected: All tests PASS.

- [ ] **Step 5: Commit**

```bash
git add tinyagentos/llm_proxy.py tests/test_llm_proxy.py
git commit -m "feat: LiteLLM proxy manager — config generation, per-agent keys, lifecycle"
```

---

### Task 2: Provider Management API

**Files:**
- Create: `tinyagentos/tinyagentos/routes/providers.py`
- Create: `tinyagentos/tests/test_routes_providers.py`

- [ ] **Step 1: Write failing tests**

```python
# tests/test_routes_providers.py
import pytest

@pytest.mark.asyncio
class TestProviderAPI:
    async def test_list_providers(self, client):
        resp = await client.get("/api/providers")
        assert resp.status_code == 200
        data = resp.json()
        assert isinstance(data, list)

    async def test_test_connection_missing_url(self, client):
        resp = await client.post("/api/providers/test", json={"type": "ollama"})
        assert resp.status_code == 400

    async def test_add_provider(self, client):
        resp = await client.post("/api/providers", json={
            "name": "test-ollama", "type": "ollama",
            "url": "http://localhost:11434", "priority": 1,
        })
        assert resp.status_code == 200

    async def test_delete_provider(self, client):
        # Add then delete
        await client.post("/api/providers", json={
            "name": "to-delete", "type": "ollama",
            "url": "http://localhost:11434", "priority": 5,
        })
        resp = await client.delete("/api/providers/to-delete")
        assert resp.status_code == 200

    async def test_providers_page_renders(self, client):
        resp = await client.get("/providers")
        assert resp.status_code == 200
        assert "Provider" in resp.text
```

- [ ] **Step 2: Run tests to verify they fail**

- [ ] **Step 3: Implement provider routes**

```python
# tinyagentos/routes/providers.py
from __future__ import annotations
from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, JSONResponse
from pydantic import BaseModel
from tinyagentos.backend_adapters import get_adapter
from tinyagentos.config import save_config_locked, VALID_BACKEND_TYPES

router = APIRouter()

class ProviderCreate(BaseModel):
    name: str
    type: str
    url: str
    priority: int = 99
    api_key_secret: str | None = None
    model: str = "default"

class ProviderTest(BaseModel):
    type: str
    url: str

@router.get("/providers", response_class=HTMLResponse)
async def providers_page(request: Request):
    """Provider management page."""
    templates = request.app.state.templates
    config = request.app.state.config
    return templates.TemplateResponse(request, "providers.html", {
        "active_page": "settings",
        "backends": config.backends,
        "valid_types": sorted(VALID_BACKEND_TYPES),
    })

@router.get("/api/providers")
async def list_providers(request: Request):
    """List all configured providers with live status."""
    config = request.app.state.config
    http_client = request.app.state.http_client
    providers = []
    for backend in config.backends:
        status = "unknown"
        response_ms = 0
        models = []
        try:
            adapter = get_adapter(backend["type"])
            result = await adapter.health(http_client, backend["url"])
            status = result.get("status", "error")
            response_ms = result.get("response_ms", 0)
            models = result.get("models", [])
        except Exception:
            status = "error"
        providers.append({
            **backend,
            "status": status,
            "response_ms": response_ms,
            "models": models,
        })
    return providers

@router.post("/api/providers/test")
async def test_provider(request: Request, body: ProviderTest):
    """Test connectivity to a provider before saving."""
    if not body.url:
        return JSONResponse({"error": "URL required"}, status_code=400)
    if body.type not in VALID_BACKEND_TYPES:
        return JSONResponse({"error": f"Invalid type. Must be one of: {sorted(VALID_BACKEND_TYPES)}"}, status_code=400)
    try:
        adapter = get_adapter(body.type)
        http_client = request.app.state.http_client
        result = await adapter.health(http_client, body.url)
        return {
            "reachable": result["status"] == "ok",
            "response_ms": result.get("response_ms", 0),
            "models": result.get("models", []),
        }
    except Exception as e:
        return {"reachable": False, "error": str(e)}

@router.post("/api/providers")
async def add_provider(request: Request, body: ProviderCreate):
    """Add a new provider to the configuration."""
    config = request.app.state.config
    if any(b["name"] == body.name for b in config.backends):
        return JSONResponse({"error": f"Provider '{body.name}' already exists"}, status_code=409)
    config.backends.append(body.model_dump(exclude_none=True))
    await save_config_locked(config, config.config_path)
    # Reconfigure LLM proxy if running
    proxy = getattr(request.app.state, "llm_proxy", None)
    if proxy and proxy.is_running():
        proxy.write_config(config.backends)
    return {"status": "added", "name": body.name}

@router.delete("/api/providers/{name}")
async def delete_provider(request: Request, name: str):
    """Remove a provider."""
    config = request.app.state.config
    config.backends = [b for b in config.backends if b.get("name") != name]
    await save_config_locked(config, config.config_path)
    return {"status": "deleted", "name": name}
```

- [ ] **Step 4: Create providers template**

Simple page with provider table, add form with test button, status indicators.

- [ ] **Step 5: Wire into app.py**

Add LLMProxy to app.state, include providers router. Start proxy in lifespan if LiteLLM is installed.

- [ ] **Step 6: Run tests, commit**

```bash
pytest tests/ -v
git add tinyagentos/routes/providers.py tinyagentos/templates/providers.html tests/test_routes_providers.py tinyagentos/app.py
git commit -m "feat: provider management — add/test/remove with live status and LiteLLM integration"
```

---

### Task 3: Per-Agent Key Management

**Files:**
- Modify: `tinyagentos/tinyagentos/deployer.py`
- Modify: `tinyagentos/tinyagentos/routes/agents.py`

- [ ] **Step 1: Update deployer to inject LLM proxy env vars**

In `deployer.py`, when deploying an agent:
1. Check if LLM proxy is running
2. If yes, create a per-agent virtual key via `llm_proxy.create_agent_key()`
3. Inject `OPENAI_API_KEY` and `OPENAI_BASE_URL` into the agent's environment

- [ ] **Step 2: Add key usage to agent workspace**

In `routes/workspace.py`, add an endpoint that fetches the agent's LiteLLM key usage:
- `GET /api/agents/{name}/workspace/usage` — returns token counts, cost, latency stats

- [ ] **Step 3: Tests**

- Test that deployer injects proxy env vars when proxy is running
- Test usage endpoint returns data

- [ ] **Step 4: Commit**

```bash
git commit -m "feat: per-agent LiteLLM keys — auto-created on deploy, usage tracking in workspace"
```

---

### Task 4: Add LiteLLM to Catalog and Dependencies

**Files:**
- Create: `app-catalog/services/litellm/manifest.yaml`
- Modify: `pyproject.toml`

- [ ] **Step 1: Add LiteLLM catalog manifest**

```yaml
id: litellm
name: LiteLLM Proxy
type: service
version: 1.55.0
description: "Internal LLM gateway — unified API for all providers, per-agent keys, auto-configured by TinyAgentOS"
homepage: https://github.com/BerriAI/litellm
license: MIT
requires:
  ram_mb: 256
install:
  method: pip
  package: "litellm[proxy]"
hardware_tiers:
  arm-npu-16gb: full
  arm-cpu-8gb: full
  x86-cuda-12gb: full
  cpu-only: full
```

- [ ] **Step 2: Add optional litellm dependency to pyproject.toml**

```toml
[project.optional-dependencies]
proxy = ["litellm[proxy]>=1.50.0"]
```

- [ ] **Step 3: Commit**

```bash
git commit -m "feat: add LiteLLM to catalog and optional dependencies"
```

---

## Self-Review

**Spec coverage:**
- LiteLLM config generation ✓ (Task 1)
- Per-agent virtual keys ✓ (Task 3)
- Auto-configuration from backends ✓ (Task 1)
- Provider management UI ✓ (Task 2)
- Test connection before save ✓ (Task 2)
- Hidden from users ✓ (runs on localhost only, no dashboard exposed)
- Usage monitoring ✓ (Task 3)
- Catalog entry ✓ (Task 4)

**Not covered (separate plans):**
- Channel Hub (Plan 2)
- Adapter system (Plan 3)
- Response translator (Plan 4)
