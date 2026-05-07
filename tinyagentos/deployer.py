"""Agent deployment — create container, install framework, start.

Snapshot model (Phase 2.A): the container rootfs holds everything.
workspace, memory, and home live inside the container image rather than
as host-side bind mounts. The only bind mount is the trace directory
so the host trace-API can read events without incus exec per request.

See ``docs/design/architecture-pivot-v2.md`` §3 and §10 for the full
rationale. The archive unit is the container snapshot; state travels
with the container, not with separately-moved host directories.

A container produced by this deployer can be snapshot-exported as a
single tarball for atomic archive and restore.
"""
from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field
from pathlib import Path

from tinyagentos.agent_image import BASE_IMAGE_ALIAS, is_image_present
from tinyagentos.containers import (
    create_container, exec_in_container, push_file,
    start_container, stop_container, destroy_container,
    add_proxy_device,
)

logger = logging.getLogger(__name__)


@dataclass
class DeployRequest:
    name: str
    framework: str        # agent framework app_id
    model: str | None     # model app_id (optional)
    data_dir: Path        # host data dir — trace and shared state live here
    fallback_models: list[str] = field(default_factory=list)
    color: str = "#888888"
    # Optional unicode emoji shown next to the agent in the UI. Purely
    # presentation — never consumed by the container or worker.
    emoji: str | None = None
    memory_limit: str | None = None
    cpu_limit: int | None = None
    extra_config: dict | None = None
    can_read_user_memory: bool = False
    # Containers reach the host via incus proxy devices on the same port,
    # so 127.0.0.1 inside the container transparently forwards to
    # 127.0.0.1 on the host.
    taos_host: str = "127.0.0.1"
    taos_port: int = 6969
    # Per §10.10: default 40 GiB rootfs quota. Overridable per-agent at
    # deploy time. None disables quota (unlimited, e.g. for dev/test).
    root_size_gib: int = 40


async def deploy_agent(req: DeployRequest) -> dict:
    """Full agent deployment: create container → install framework → start.

    Snapshot model: the container rootfs holds workspace, memory, and home.
    ONE bind mount for traces only — host_trace_dir → /root/.taos/trace/ —
    so the host trace-API can read without incus exec per request.

    Rolls back (destroys container) on any critical failure after creation.
    Re-running on the same name is safe if the previous container was cleaned
    up (idempotent at the deploy level).
    """
    import asyncio

    container_name = f"taos-agent-{req.name}"
    steps = []

    # Trace directory on the host — the only host-side path this deployer
    # creates. Layout matches the target for Phase 2.C trace_store migration:
    # {data_dir}/trace/{slug}/ → /root/.taos/trace/ inside container.
    host_trace = req.data_dir / "trace" / req.name
    host_trace.mkdir(parents=True, exist_ok=True)

    # ONE bind mount: trace only.
    mounts = [
        (str(host_trace), "/root/.taos/trace"),
    ]

    # Env vars injected at container creation time.
    env: dict[str, str] = {}

    # LLM proxy (LiteLLM).
    llm_key = None
    if req.extra_config and req.extra_config.get("llm_proxy"):
        proxy = req.extra_config["llm_proxy"]
        if proxy.is_running():
            # Scope the virtual key to exactly the models this agent is
            # allowed to call. An empty list is preserved as empty (not
            # ["default"]) when the agent was deployed without a model
            # pick so mint failure isn't masked by an ambient alias.
            key_models = [m for m in [req.model, *(req.fallback_models or [])] if m]
            llm_key = await proxy.create_agent_key(req.name, models=key_models or None)
            if llm_key is None:
                db_url = getattr(proxy, "database_url", None)
                if db_url is None:
                    # Routing-only mode — LiteLLM can't mint virtual keys
                    # without a Postgres DB. Fall back to the shared master
                    # key so the container can still authenticate.
                    from tinyagentos.llm_proxy import TAOS_LITELLM_MASTER_KEY
                    llm_key = TAOS_LITELLM_MASTER_KEY
                    logger.info(
                        "deploy %s: LiteLLM routing-only mode — using shared master key (no DB configured for virtual keys)",
                        req.name,
                    )
                else:
                    # DB configured but key mint failed — something else
                    # is wrong (migration pending, DB unreachable, master
                    # key mismatch). Don't silently fall back; that hides
                    # the bug and ships broken agents.
                    db_host = db_url.split("@")[-1] if "@" in db_url else db_url
                    msg = (
                        f"virtual key mint failed despite DB configured at {db_host}"
                    )
                    logger.error("deploy %s: %s", req.name, msg)
                    return {"success": False, "error": msg, "steps": steps}
            from tinyagentos.llm_proxy import EMBEDDING_ALIAS
            # Primary key for openclaw's litellm provider.
            env["LITELLM_API_KEY"] = llm_key
            # Compat shim — smolagents and other frameworks still expect OPENAI_API_KEY.
            env["OPENAI_API_KEY"] = llm_key
            env["OPENAI_BASE_URL"] = f"{proxy.url}/v1"
            # Host-side embedding endpoint — same LiteLLM process,
            # OpenAI-compatible /v1/embeddings. Framework-agnostic.
            env["TAOS_EMBEDDING_URL"] = f"{proxy.url}/v1/embeddings"
            # Stable alias the host LiteLLM routes to whichever
            # concrete embedding model the backends actually have loaded.
            env["TAOS_EMBEDDING_MODEL"] = EMBEDDING_ALIAS

    # User memory (optional, permission-gated)
    if req.can_read_user_memory:
        env["TAOS_USER_MEMORY_URL"] = (
            f"http://{req.taos_host}:{req.taos_port}"
            f"/api/user-memory/agent-search?agent_name={req.name}"
        )

    # Skill runtime — all skill execution happens on the host via the
    # in-process Skill MCP server. Container just needs the URL.
    skill_server_url = f"http://{req.taos_host}:{req.taos_port}/api/skill-exec"
    env["TAOS_SKILLS_URL"] = skill_server_url
    env["TAOS_SKILLS_MCP_URL"] = skill_server_url
    env["TAOS_SKILLS_TOOLS_URL"] = (
        f"{skill_server_url}/tools?agent_name={req.name}"
    )
    env["TAOS_AGENT_NAME"] = req.name
    # Home is always /root inside the container (rootfs).
    env["TAOS_AGENT_HOME"] = "/root"

    # Selected model name (always set; empty string when not configured).
    env["TAOS_MODEL"] = req.model or ""
    # Fallback models as comma-separated list for install.sh.
    env["TAOS_FALLBACK_MODELS"] = ",".join(req.fallback_models or [])

    # Trace capture — local auth token + trace API URL.
    try:
        local_token_path = req.data_dir / ".auth_local_token"
        if local_token_path.exists():
            env["TAOS_LOCAL_TOKEN"] = local_token_path.read_text().strip()
    except Exception:
        pass
    env["TAOS_TRACE_URL"] = f"http://{req.taos_host}:{req.taos_port}/api/trace"

    # openclaw bridge connection info — injected so install.sh can write
    # /root/.openclaw/openclaw.json and /root/.openclaw/env inside the container
    # from these env vars. Bridge URL is how the openclaw service phones home.
    env["TAOS_BRIDGE_URL"] = f"http://{req.taos_host}:{req.taos_port}"
    # OPENAI_BASE_URL defaults to LiteLLM proxy if no llm_proxy in config.
    if "OPENAI_BASE_URL" not in env:
        env["OPENAI_BASE_URL"] = "http://127.0.0.1:4000/v1"
    if "OPENAI_API_KEY" not in env:
        env["OPENAI_API_KEY"] = ""
    if "LITELLM_API_KEY" not in env:
        env["LITELLM_API_KEY"] = ""

    # Pre-built base image fast-path — see tinyagentos/agent_image.py.
    # When the cached image is imported locally we launch from it and
    # install.sh skips the openclaw/apt steps; on a cold host we fall
    # back to images:debian/bookworm and install.sh does the full run.
    base_image_ready = False
    if req.framework == "openclaw":
        try:
            base_image_ready = await is_image_present(BASE_IMAGE_ALIAS)
        except Exception:
            base_image_ready = False
    launch_image = BASE_IMAGE_ALIAS if base_image_ready else "images:debian/bookworm"
    if base_image_ready:
        env["TAOS_BASE_IMAGE_PRESENT"] = "1"
        logger.info(f"Deploy {req.name}: using cached base image {BASE_IMAGE_ALIAS}")
    else:
        logger.info(f"Deploy {req.name}: cached base image not present, using {launch_image}")

    # Step 1: Create container with trace mount + env baked in at launch time.
    # root_size_gib applies the disk quota (40 GiB default per §10.10).
    logger.info(f"Creating container {container_name}")
    result = await create_container(
        container_name,
        image=launch_image,
        memory_limit=req.memory_limit,
        cpu_limit=req.cpu_limit,
        mounts=mounts,
        env=env,
        host_uid=os.getuid(),
        root_size_gib=req.root_size_gib,
    )
    if not result["success"]:
        return {"success": False, "error": f"Container creation failed: {result.get('error')}", "steps": steps}
    steps.append("container_created")

    try:
        # Incus proxy devices: let the container reach host services via
        # its own 127.0.0.1.
        proxy_ports = [
            ("taos-proxy-litellm", 4000),
            ("taos-proxy-taos", req.taos_port),
        ]
        for dev_name, port in proxy_ports:
            res = await add_proxy_device(
                container_name,
                dev_name,
                listen=f"tcp:127.0.0.1:{port}",
                connect=f"tcp:127.0.0.1:{port}",
                bind_mode="instance",
            )
            if not res.get("success"):
                raise RuntimeError(
                    f"failed to attach proxy device {dev_name}:{port}: {res.get('output', '')}"
                )
        steps.append("proxy_devices_attached")

        # Step 2: Wait for network
        for _ in range(10):
            code, output = await exec_in_container(container_name, ["hostname", "-I"])
            if code == 0 and output.strip():
                break
            await asyncio.sleep(2)
        steps.append("network_ready")

        # Step 3: Install base dependencies (framework needs these).
        # Skipped entirely when the pre-built openclaw base image is in
        # use — everything this apt-get pulls is already baked in.
        if base_image_ready:
            logger.info(f"Skipping dep install in {container_name} (base image already has them)")
            steps.append("deps_skipped_base_image")
        else:
            logger.info(f"Installing dependencies in {container_name}")
            code, output = await exec_in_container(
                container_name,
                ["bash", "-c", "apt-get update -qq && DEBIAN_FRONTEND=noninteractive apt-get install -y -qq --no-install-recommends python3 python3-pip python3-venv python3-dev git curl wget ca-certificates gnupg build-essential nodejs npm"],
                timeout=900,
            )
            if code != 0:
                raise RuntimeError(f"Dependency install failed: {output}")
            steps.append("deps_installed")

        # Step 4: Install agent framework (if specified and not just "none").
        if req.framework and req.framework != "none":
            manifest = None
            if req.extra_config and req.extra_config.get("registry"):
                manifest = req.extra_config["registry"].get(req.framework)

            if manifest is None:
                method = "pip"
                package = req.framework
            else:
                method = manifest.install.get("method")
                package = manifest.install.get("package")

            logger.info(f"Installing framework {req.framework} via {method} in {container_name}")

            # If a framework ships an in-repo install script at
            # tinyagentos/scripts/install_<framework>.sh, prefer that over the
            # generic pip path. These scripts drop the full per-framework bridge
            # (NO_RESPONSE handling, context rendering, force_respond) and the
            # upstream framework itself; pip alone cannot wire those pieces.
            from pathlib import Path as _P
            _script = _P(__file__).parent / "scripts" / f"install_{req.framework}.sh"
            if _script.exists():
                logger.info(f"Pushing install script {_script.name} into {container_name}")
                _push_rc, _push_out = await push_file(
                    container_name, str(_script), f"/tmp/install_{req.framework}.sh",
                )
                if _push_rc != 0:
                    raise RuntimeError(
                        f"Failed to push install script (rc={_push_rc}): {_push_out[-300:]}"
                    )
                code, output = await exec_in_container(
                    container_name,
                    ["bash", f"/tmp/install_{req.framework}.sh"],
                    timeout=900,
                )
                if code != 0:
                    raise RuntimeError(
                        f"Framework install (script) failed ({code}): {output[-1500:]}"
                    )
            elif method == "pip":
                pkg = package if manifest is not None else req.framework
                # PEP 668: Debian 13+ base images refuse system-wide pip
                # installs without this flag, so container deploys otherwise
                # stall on 'externally-managed-environment'.
                code, output = await exec_in_container(
                    container_name,
                    ["pip3", "install", "--break-system-packages", pkg],
                    timeout=300,
                )
                if code != 0:
                    raise RuntimeError(f"Framework install failed ({code}): {output[-500:]}")
            elif method == "script":
                script_name = manifest.install.get("script")
                script_path = manifest.manifest_dir / script_name
                if not script_path.exists():
                    raise RuntimeError(f"Install script missing: {script_path}")
                code, output = await push_file(
                    container_name, str(script_path), "/tmp/install.sh"
                )
                code, output = await exec_in_container(
                    container_name,
                    ["bash", "/tmp/install.sh"],
                    timeout=900,
                )
                if code != 0:
                    raise RuntimeError(f"Framework install failed ({code}): {output[-500:]}")
            else:
                raise RuntimeError(f"Unsupported install method: {method!r} for framework {req.framework}")

            steps.append("framework_installed")

        # OpenClaw: inject taosmd agent rules into AGENTS.md so the framework
        # picks them up on every turn (per taosmd contract — see issue #378).
        # Non-fatal: the runtime system-prompt prepend in prompt_assembly.py
        # remains as a backstop until all frameworks are wired.
        if req.framework == "openclaw":
            try:
                import taosmd as _taosmd
                _agent_rules_fn = getattr(_taosmd, "agent_rules", None)
                if callable(_agent_rules_fn):
                    _rules = _agent_rules_fn().replace("<your-agent-name>", req.name)
                    import tempfile
                    import os as _os
                    with tempfile.NamedTemporaryFile("w", suffix=".md", delete=False) as _tf:
                        _tf.write(_rules)
                        _tmp_path = _tf.name
                    _agents_md_path = "/root/.openclaw/AGENTS.md"
                    _push_rc, _push_out = await push_file(container_name, _tmp_path, _agents_md_path)
                    _os.unlink(_tmp_path)
                    if _push_rc != 0:
                        logger.warning(
                            "openclaw: failed to push AGENTS.md (rc=%s): %s",
                            _push_rc, _push_out[-200:],
                        )
                    else:
                        logger.info(
                            "openclaw: pushed AGENTS.md (taosmd rules) to %s",
                            _agents_md_path,
                        )
            except ImportError:
                logger.warning("openclaw: taosmd not installed — skipping AGENTS.md injection")
            except Exception:
                logger.exception("openclaw: AGENTS.md injection failed")

        # Step 5: Get container IP
        code, output = await exec_in_container(container_name, ["hostname", "-I"])
        container_ip = output.strip().split()[0] if code == 0 and output.strip() else None
        steps.append("deployment_complete")

        return {
            "success": True,
            "name": req.name,
            "container": container_name,
            "ip": container_ip,
            "llm_key": llm_key,
            "steps": steps,
        }

    except Exception as exc:
        logger.error(f"Deploy failed at step {steps[-1] if steps else 'init'}: {exc}")
        logger.info(f"Rolling back: destroying container {container_name}")
        await destroy_container(container_name)
        steps.append("rolled_back")
        return {"success": False, "error": str(exc), "steps": steps}


async def undeploy_agent(name: str, *, data_dir: Path | None = None, delete_state: bool = False) -> dict:
    """Stop and destroy an agent's container.

    In the snapshot model, all state lives inside the container rootfs.
    The only host-side path this deployer creates is the trace directory
    (``{data_dir}/trace/{name}``). Pass ``delete_state=True`` to also
    remove it, but note this is destructive and irreversible — only do
    so on a true delete, not a stop/rebuild flow.
    """
    container_name = f"taos-agent-{name}"
    result = await destroy_container(container_name)
    if delete_state and data_dir is not None:
        import shutil
        for sub in ("agent-workspaces", "agent-memory"):
            target = data_dir / sub / name
            if target.exists():
                shutil.rmtree(target, ignore_errors=True)
    return {"success": result["success"], "name": name}
