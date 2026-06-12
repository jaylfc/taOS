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
import secrets
from dataclasses import dataclass, field
from pathlib import Path

from tinyagentos.agent_image import BASE_IMAGE_ALIAS, is_image_present
from tinyagentos.containers import (
    create_container, exec_in_container, push_file,
    start_container, stop_container, destroy_container,
    add_proxy_device,
)

logger = logging.getLogger(__name__)


def _is_unprivileged_userns(uid_map_path: str = "/proc/self/uid_map") -> bool:
    """True when this process runs in an unprivileged user namespace.

    In an unprivileged container (e.g. an unprivileged LXC) the root user is
    mapped to a non-zero host UID. The first line of ``/proc/self/uid_map`` is
    ``<ns_start> <host_start> <count>``; a privileged container or the host maps
    ``0 0 ...`` (root↦root), while an unprivileged one maps ``0 100000 ...``.
    Nested container creation can't remap a new container's rootfs in that case,
    so agent deploys fail with an "idmapped storage / change ownership" error.
    """
    try:
        with open(uid_map_path, encoding="ascii") as f:
            fields = f.readline().split()
    except OSError:
        return False  # no procfs (e.g. macOS) — not an unprivileged Linux userns
    return len(fields) == 3 and fields[1] != "0"


def _explain_container_failure(raw_error: object) -> str:
    """Turn a raw container-creation error into an actionable message.

    The kernel-level "Failed to handle idmapped storage / change ownership"
    error is opaque to users. When we see it — or we can confirm we're in an
    unprivileged user namespace — explain that taOS needs a privileged
    container and how to fix it (the common Proxmox case)."""
    text = str(raw_error).strip() if raw_error else "unknown error"
    idmap_failure = ("idmapped storage" in text.lower()
                     or "change ownership" in text.lower())
    if idmap_failure or _is_unprivileged_userns():
        return (
            "Container creation failed — taOS appears to be running in an "
            "unprivileged container, which cannot create the nested agent "
            "container (the kernel can't remap the new container's filesystem). "
            "Fix: run taOS in a privileged container with nesting enabled. On "
            "Proxmox, set the LXC to Privileged and enable Nesting (Options → "
            "Features: nesting=1, keyctl=1, fuse=1), then redeploy. "
            f"Underlying error: {text}"
        )
    return f"Container creation failed: {text}"

_TAOSMD_BEGIN = "<!-- taosmd:rules-begin -->"
_TAOSMD_END = "<!-- taosmd:rules-end -->"

# Per-framework AGENTS.md path inside the agent's container.
# Frameworks read this file on every turn to pick up agent rules
# (per the taosmd contract — see issue #378).
AGENTS_MD_PATHS: dict[str, str] = {
    "openclaw": "/root/.openclaw/AGENTS.md",
    "hermes": "/root/.hermes/AGENTS.md",
}


def _splice_taosmd_block(existing: str, new_rules: str) -> str:
    """Insert or replace the taosmd-rules sentinel block in *existing*,
    preserving any user content outside the sentinels.

    Sentinels are HTML comments so they're invisible in rendered Markdown
    but trivial to find in raw text.  The pattern follows tools like
    pre-commit, direnv and nvm that manage a section inside user-editable
    files without silently blowing away the rest.

    Three cases:
      1. Sentinels present  → replace only the content between them.
      2. File empty/absent  → return only the sentinel-wrapped block.
      3. File exists, no sentinels → append block with a blank separator.
    """
    block = f"{_TAOSMD_BEGIN}\n{new_rules.rstrip()}\n{_TAOSMD_END}"
    if _TAOSMD_BEGIN in existing and _TAOSMD_END in existing:
        before, _, rest = existing.partition(_TAOSMD_BEGIN)
        _, _, after = rest.partition(_TAOSMD_END)
        return f"{before}{block}{after}"
    if not existing.strip():
        return block + "\n"
    return existing.rstrip() + "\n\n" + block + "\n"


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
                # Key mint failed — either LiteLLM is running without a
                # Postgres DB (routing-only mode, so /key/generate is
                # unavailable) or the DB is configured but the call failed
                # (migration pending, DB unreachable, master key mismatch).
                # Either way we must NOT fall back to the shared master key:
                # injecting it gives every agent full admin API access and
                # access to all other agents' keys. Fail loudly instead.
                db_url = getattr(proxy, "database_url", None)
                if db_url is None:
                    msg = (
                        "per-agent LiteLLM virtual key could not be minted: "
                        "LiteLLM is running in routing-only mode (no Postgres "
                        "DATABASE_URL configured). Configure a Postgres database "
                        "for LiteLLM so per-agent scoped keys can be issued. "
                        "Deploying with the shared master key is not permitted "
                        "because it grants agents full admin API access."
                    )
                else:
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

    # Agent-bridge shared token (issue #672 — defense-in-depth auth guard).
    # Generate a per-deployment secret so only the controller (which knows
    # the token) can call the command-executing bridge endpoints.
    env["TAOS_BRIDGE_TOKEN"] = secrets.token_hex(32)

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
        # os.getuid() is the UID of the controller process (the 'taos' system
        # user when running under systemd).  raw.idmap maps container-root to
        # this host UID so the trace bind-mount is writable by the container.
        host_uid=os.getuid(),
        root_size_gib=req.root_size_gib,
    )
    if not result["success"]:
        return {"success": False, "error": _explain_container_failure(result.get("error")), "steps": steps}
    steps.append("container_created")

    try:
        # Incus proxy devices: let the container reach host services via
        # its own 127.0.0.1.
        #
        # Each tuple: (device_name, container_listen_port, host_connect_port).
        # LiteLLM: the container-side URL is always tcp:127.0.0.1:4000 (baked
        # into OPENAI_BASE_URL and openclaw.json via the proxy device above),
        # but the host may run LiteLLM on any configured port (default 7834).
        # The connect side tracks the live proxy port so the tunnel reaches the
        # right host process regardless of whether the operator kept the legacy
        # 4000 or migrated to 7834.
        llm_proxy_ref = (req.extra_config or {}).get("llm_proxy")
        litellm_host_port = llm_proxy_ref.port if llm_proxy_ref is not None else 7834
        proxy_devices = [
            ("taos-proxy-litellm", 4000, litellm_host_port),
            ("taos-proxy-taos", req.taos_port, req.taos_port),
        ]
        for dev_name, listen_port, connect_port in proxy_devices:
            res = await add_proxy_device(
                container_name,
                dev_name,
                listen=f"tcp:127.0.0.1:{listen_port}",
                connect=f"tcp:127.0.0.1:{connect_port}",
                bind_mode="instance",
            )
            if not res.get("success"):
                raise RuntimeError(
                    f"failed to attach proxy device {dev_name}: {res.get('output', '')}"
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

        # Inject taosmd agent rules into AGENTS.md for supported frameworks so
        # the framework picks them up on every turn (per taosmd contract —
        # see issue #378).  Non-fatal: the runtime system-prompt prepend in
        # prompt_assembly.py remains as a backstop until all frameworks are wired.
        #
        # Sentinel-aware write: we wrap the taosmd block between HTML comment
        # sentinels so it never conflicts with a user-supplied template.  If
        # the file already exists (from the Store or hand-crafted), we splice
        # only our block in; everything outside the sentinels is preserved.
        if req.framework in AGENTS_MD_PATHS:
            target_path = AGENTS_MD_PATHS[req.framework]
            try:
                import taosmd as _taosmd
                _agent_rules_fn = getattr(_taosmd, "agent_rules", None)
                if callable(_agent_rules_fn):
                    _rules = _agent_rules_fn().replace("<your-agent-name>", req.name)
                    import tempfile
                    import os as _os
                    # Read existing file from the container; treat any error as absent.
                    _read_rc, _existing = await exec_in_container(
                        container_name, ["cat", target_path]
                    )
                    _existing_content = _existing if _read_rc == 0 else ""
                    _new_content = _splice_taosmd_block(_existing_content, _rules)
                    with tempfile.NamedTemporaryFile("w", suffix=".md", delete=False) as _tf:
                        _tf.write(_new_content)
                        _tmp_path = _tf.name
                    _push_rc, _push_out = await push_file(container_name, _tmp_path, target_path)
                    _os.unlink(_tmp_path)
                    if _push_rc != 0:
                        logger.warning(
                            "%s: failed to push AGENTS.md (rc=%s): %s",
                            req.framework, _push_rc, _push_out[-200:],
                        )
                    else:
                        logger.info(
                            "%s: pushed AGENTS.md (taosmd rules) to %s",
                            req.framework, target_path,
                        )
            except ImportError:
                logger.warning("%s: taosmd not installed — skipping AGENTS.md injection", req.framework)
            except Exception:
                logger.exception("%s: AGENTS.md injection failed", req.framework)

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
