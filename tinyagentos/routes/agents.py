from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel

import taosmd.agents as tm_agents

from tinyagentos.agent_db import find_agent, get_agent_summaries
from tinyagentos.config import save_config_locked, validate_agent_name, slugify_agent_name
from tinyagentos.providers import CLOUD_TYPES

logger = logging.getLogger(__name__)

EXPORT_VERSION = 1

# Cloud provider backend types whose advertised models are routable for a
# deploy / model-change. Single source of truth is providers.CLOUD_TYPES
# (#351); aliased here so the call sites below read clearly.
_CLOUD_PROVIDER_TYPES = CLOUD_TYPES

router = APIRouter()


def _archive_timestamp() -> str:
    """UTC timestamp as YYYYMMDDTHHMMSS for archive naming."""
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S")


class AgentCreate(BaseModel):
    name: str
    host: str
    qmd_index: str
    color: str = "#888888"
    can_read_user_memory: bool = False


class AgentUpdate(BaseModel):
    host: str | None = None
    qmd_index: str | None = None
    color: str | None = None
    emoji: str | None = None
    can_read_user_memory: bool | None = None


@router.get("/api/agents")
async def list_agents(request: Request):
    """List all configured agents."""
    return request.app.state.config.agents


@router.get("/api/agents/containers")
async def list_agent_containers(request: Request):
    """List live LXC container status for all agent containers."""
    from tinyagentos.containers import list_containers
    containers = await list_containers(prefix="taos-agent-")
    return [
        {
            "name": c.name,
            "agent_name": c.name.removeprefix("taos-agent-"),
            "status": c.status,
            "ip": c.ip,
            "memory_mb": c.memory_mb,
            "cpu_cores": c.cpu_cores,
        }
        for c in containers
    ]


@router.get("/api/agents/archived")
async def list_archived_agents(request: Request):
    config = request.app.state.config
    return config.archived_agents


@router.get("/api/agents/{name}/deploy-status")
async def get_deploy_status(request: Request, name: str):
    """Get the background deploy task status for an agent."""
    deploy_tasks = request.app.state.deploy_tasks
    task = deploy_tasks.get(name)
    if task is None:
        return JSONResponse({"error": f"No deploy task found for '{name}'"}, status_code=404)
    return task


@router.get("/api/agents/{name}")
async def get_agent_endpoint(request: Request, name: str):
    """Get agent details by name."""
    config = request.app.state.config
    agent = find_agent(config, name)
    if not agent:
        return JSONResponse({"error": f"Agent '{name}' not found"}, status_code=404)
    return agent


@router.post("/api/agents")
async def add_agent(request: Request, body: AgentCreate):
    """Add a new agent to the configuration.

    The user-supplied name is stored verbatim as ``display_name`` for UI
    purposes and slugified into ``name`` for container and path safety.
    If the slug collides with an existing agent, a numeric suffix is
    appended until it's unique.
    """
    config = request.app.state.config
    display_name = body.name.strip()
    name_error = validate_agent_name(display_name)
    if name_error:
        return JSONResponse({"error": name_error}, status_code=400)

    slug = slugify_agent_name(display_name)
    unique_slug = slug
    suffix = 2
    while find_agent(config, unique_slug):
        unique_slug = f"{slug}-{suffix}"
        suffix += 1
        if suffix > 100:
            return JSONResponse({"error": "Could not generate a unique agent slug"}, status_code=400)

    agent = body.model_dump()
    agent["name"] = unique_slug
    agent["display_name"] = display_name
    config.agents.append(agent)
    await save_config_locked(config, config.config_path)
    return {"status": "created", "name": unique_slug, "display_name": display_name}


@router.put("/api/agents/{name}")
async def update_agent(request: Request, name: str, body: AgentUpdate):
    """Update an existing agent's configuration."""
    config = request.app.state.config
    agent = find_agent(config, name)
    if not agent:
        return JSONResponse({"error": f"Agent '{name}' not found"}, status_code=404)
    if body.host is not None:
        agent["host"] = body.host
    if body.qmd_index is not None:
        agent["qmd_index"] = body.qmd_index
    if body.color is not None:
        agent["color"] = body.color
    if body.emoji is not None:
        emoji = body.emoji.strip()
        agent["emoji"] = emoji if emoji else None
    if body.can_read_user_memory is not None:
        agent["can_read_user_memory"] = body.can_read_user_memory
    await save_config_locked(config, config.config_path)
    return {"status": "updated", "name": name}


class AgentPermissions(BaseModel):
    can_read_user_memory: bool | None = None


@router.put("/api/agents/{name}/permissions")
async def update_agent_permissions(request: Request, name: str, body: AgentPermissions):
    """Update an agent's permissions (e.g. user memory access)."""
    config = request.app.state.config
    agent = find_agent(config, name)
    if not agent:
        return JSONResponse({"error": f"Agent '{name}' not found"}, status_code=404)
    if body.can_read_user_memory is not None:
        agent["can_read_user_memory"] = body.can_read_user_memory
    await save_config_locked(config, config.config_path)
    return {
        "status": "updated",
        "name": name,
        "permissions": {
            "can_read_user_memory": agent.get("can_read_user_memory", False),
        },
    }


async def _archive_agent_fully(request: Request, name: str) -> dict:
    """Archive via incus snapshot. Zero-copy on btrfs/ZFS pools; rsync fallback
    on dir-backed. Container stays intact (snapshots live alongside); restoring
    is snapshot restore. Purge = incus delete.

    Archive target:
      - pool: (default) — snapshot lives in-pool alongside the container.
      - path:/abs/path — export snapshot tarball to that path.
      - s3://bucket — export + upload (not yet implemented; log + skip).

    Returns ``{"error": ..., "status_code": ...}`` on failure so callers
    can re-raise as JSONResponse.
    """
    import json as _json
    from tinyagentos.containers import container_exists, stop_container, snapshot_create

    config = request.app.state.config
    agent = find_agent(config, name)
    if agent is None:
        return {"error": f"Agent '{name}' not found", "status_code": 404}

    agent_id = agent.get("id")
    if not agent_id:
        import uuid
        agent_id = uuid.uuid4().hex[:12]
        agent["id"] = agent_id

    ts = _archive_timestamp()
    slug = agent["name"]
    container = f"taos-agent-{slug}"
    snapshot_name = f"taos-archive-{ts}"
    data_dir = request.app.state.data_dir
    archive_subdir = f"{slug}-{ts}"
    archive_base = data_dir / "archive" / archive_subdir

    # 0) Probe container existence first. A failed deploy can leave a config
    #    row with no container behind it; in that case we skip stop/snapshot
    #    entirely and decide between hard-delete and tombstone based on
    #    whether there is any history worth preserving.
    has_container = await container_exists(container)

    if not has_container:
        # Hard-delete when the agent has no chat history and no trace dir —
        # a tombstone for a never-used failed deploy is just archive clutter.
        # Otherwise create a tombstone (no snapshot) so the user can still
        # see it in Archived and purge from there.
        has_chat_history = False
        channel_id = agent.get("chat_channel_id")
        if channel_id:
            try:
                msg_store = request.app.state.chat_messages
                msgs = await msg_store.get_all_messages_for_channel(channel_id)
                has_chat_history = bool(msgs)
            except Exception as exc:  # noqa: BLE001
                logger.warning("archive(orphan): chat check failed for %s: %s", slug, exc)
        trace_dir = data_dir / "trace" / slug
        has_trace_history = trace_dir.exists() and any(trace_dir.iterdir())

        # Always revoke LiteLLM key first (best effort, same as below).
        llm_key = agent.get("llm_key")
        llm_proxy = getattr(request.app.state, "llm_proxy", None)
        if llm_key and llm_proxy and llm_proxy.is_running():
            try:
                await llm_proxy.delete_agent_key(llm_key)
            except Exception:
                pass

        if not has_chat_history and not has_trace_history:
            # Hard-delete: drop the config row and return. No tombstone for
            # orphan rows from a never-used failed deploy.
            config.agents = [a for a in config.agents if a["name"] != name]
            await save_config_locked(config, config.config_path)
            return {
                "status": "deleted",
                "name": slug,
                "id": agent_id,
                "note": "orphan config row (no container); hard-deleted",
            }

        # Tombstone path: write an archive entry with no snapshot so the
        # user can purge it from the Archived view.
        original_snapshot = dict(agent)
        archive_entry = {
            "id": agent_id,
            "archived_at": ts,
            "archived_slug": slug,
            "snapshot_name": None,
            "export_path": None,
            "archive_dir": f"archive/{archive_subdir}",
            "original": original_snapshot,
        }
        config.agents = [a for a in config.agents if a["name"] != name]
        config.archived_agents.append(archive_entry)
        await save_config_locked(config, config.config_path)
        return {
            "status": "archived",
            "name": slug,
            "id": agent_id,
            "archived_at": ts,
            "snapshot_name": None,
            "export_path": None,
            "note": "orphan config row (no container); tombstone created",
        }

    # 1) Force-stop the container. Best-effort — container may not exist
    #    (partial deploy), which is fine; the snapshot step below is the gate.
    try:
        await stop_container(container, force=True)
    except Exception as exc:  # noqa: BLE001
        logger.warning("archive: stop failed for %s: %s", slug, exc)

    # 2) Create snapshot. If this fails (container not found, pool error, etc.)
    #    we abort without mutating config so the agent stays in the live list.
    snap_result = await snapshot_create(container, snapshot_name)
    if not snap_result.get("success"):
        out = (snap_result.get("output") or "").strip()
        return {
            "error": (
                f"archive failed: could not create snapshot {snapshot_name} "
                f"on {container}: {out or 'unknown error'}. "
                f"Agent left in live list — fix container state and retry."
            ),
            "status_code": 500,
        }

    # 3) If archive.target is set to something other than "pool:", export
    #    the snapshot as a tarball. "s3://" is logged + skipped (not yet
    #    implemented per pivot doc §10.2).
    export_path: str | None = None
    archive_target = (config.archive or {}).get("target", "pool:")
    if archive_target and archive_target != "pool:":
        if archive_target.startswith("path:"):
            dest_dir = archive_target[len("path:"):]
            tarball_dir = data_dir / "archive" / archive_subdir
            tarball_dir.mkdir(parents=True, exist_ok=True)
            tarball_path = tarball_dir / f"{snapshot_name}.tar.gz"
            from tinyagentos.containers import _run as _c_run
            ecode, eout = await _c_run([
                "incus", "export", f"{container}/{snapshot_name}",
                str(tarball_path),
            ], timeout=600)
            if ecode == 0:
                export_path = str(tarball_path)
                logger.info("archive: exported snapshot to %s", export_path)
            else:
                logger.warning(
                    "archive: incus export failed for %s/%s: %s — "
                    "snapshot still in-pool, export path not recorded",
                    container, snapshot_name, eout,
                )
        elif archive_target.startswith("s3://"):
            logger.warning(
                "archive: s3 export target '%s' not yet implemented — "
                "snapshot lives in-pool only",
                archive_target,
            )
        else:
            logger.warning("archive: unknown archive.target '%s', ignoring", archive_target)

    # 4) Export chat history to a host-side path alongside any tarball.
    #    Chat is host-owned state; we don't write inside the container.
    channel_id = agent.get("chat_channel_id")
    if channel_id:
        try:
            msg_store = request.app.state.chat_messages
            all_msgs = await msg_store.get_all_messages_for_channel(channel_id)
            chat_export_dir = archive_base / "chat"
            chat_export_dir.mkdir(parents=True, exist_ok=True)
            chat_export_file = chat_export_dir / "chat-export.jsonl"
            try:
                with chat_export_file.open("w", encoding="utf-8") as fh:
                    for m in all_msgs:
                        fh.write(_json.dumps(m, default=str) + "\n")
                chat_export_file.chmod(0o600)
                logger.info(
                    "archive: wrote %d messages to %s", len(all_msgs), chat_export_file
                )
            except Exception as exc:  # noqa: BLE001
                logger.warning(
                    "archive: chat-export write failed for %s: %s — "
                    "messages still in global DB, re-export safe to retry",
                    slug, exc,
                )
        except Exception as exc:  # noqa: BLE001
            logger.warning("archive: chat-export failed for %s: %s", slug, exc)

        # 4b) Flag the channel archived with full metadata.
        try:
            ch_store = request.app.state.chat_channels
            await ch_store.set_settings(
                channel_id,
                {
                    "archived": True,
                    "archived_at": ts,
                    "archived_agent_id": agent_id,
                    "archived_agent_slug": slug,
                },
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning("archive: channel flag failed for %s: %s", channel_id, exc)

    # 5) Revoke LiteLLM key (best effort).
    llm_key = agent.get("llm_key")
    llm_proxy = getattr(request.app.state, "llm_proxy", None)
    if llm_key and llm_proxy and llm_proxy.is_running():
        try:
            await llm_proxy.delete_agent_key(llm_key)
        except Exception:
            pass

    # 6) Move config entry out of agents into archived_agents.
    original_snapshot = dict(agent)
    archive_entry = {
        "id": agent_id,
        "archived_at": ts,
        "archived_slug": slug,
        "snapshot_name": snapshot_name,
        "export_path": export_path,
        "archive_dir": f"archive/{archive_subdir}",
        "original": original_snapshot,
    }
    config.agents = [a for a in config.agents if a["name"] != name]
    config.archived_agents.append(archive_entry)
    await save_config_locked(config, config.config_path)

    return {
        "status": "archived",
        "name": slug,
        "id": agent_id,
        "archived_at": ts,
        "snapshot_name": snapshot_name,
        "export_path": export_path,
    }


@router.delete("/api/agents/{name}")
async def delete_agent(request: Request, name: str):
    """Archive an agent instead of hard-deleting it. The agent's
    container, workspace, and memory are preserved under an archive
    bucket so the user can restore it later — or permanently purge it
    via ``DELETE /api/agents/archived/{id}``.
    """
    result = await _archive_agent_fully(request, name)
    if "error" in result:
        return JSONResponse({"error": result["error"]}, status_code=result["status_code"])
    return result


class DeployAgentRequest(BaseModel):
    name: str
    framework: str = "none"
    model: str | None = None
    color: str = "#888888"
    # Optional unicode emoji shown next to the agent in the UI.  Stored on
    # the agent record; the frontend falls back to a framework-specific
    # default when unset.
    emoji: str | None = None
    memory_limit: str | None = None
    cpu_limit: int | None = None
    can_read_user_memory: bool = False
    # Optional pin: user explicitly picked a specific worker to run on.
    # None means "controller decides" (and for worker-hosted models the
    # controller will route to wherever the model already lives).
    target_worker: str | None = None
    # Worker failure policy fields (added in worker-failure-handling).
    on_worker_failure: str = "pause"
    fallback_models: list[str] = []
    # Persona fields (v2 persona system).
    soul_md: str = ""
    agent_md: str = ""
    memory_plugin: str | None = "taosmd"
    # Per-agent override for taOSmd device + tier. None → use global default
    # from data_dir/taosmd_default.json set by the memory wizard.
    memory_config: dict | None = None
    source_persona_id: str | None = None
    save_to_library: dict | None = None  # {"name": str, "description": str|None}


@router.post("/api/agents/deploy")
async def deploy_agent_endpoint(request: Request, body: DeployAgentRequest):
    """Deploy a new agent.

    Resolution order for the requested model (task #176 route-only stub):

    1. Controller-local model — runs on the controller like today.
    2. Cloud provider model (openai / anthropic / litellm) — unchanged,
       LiteLLM proxy handles it on the controller.
    3. Worker-hosted model, no ``target_worker`` pin — route to the
       worker that holds the model and return 202. The actual remote
       launch is deferred to Phase 1.5; for now we return the holder's
       name so the caller (and the user) knows where the agent needs
       to land.
    4. Worker-hosted model, ``target_worker`` pinned AND that worker has
       the model — runs on the pinned worker (also 202 stub today).
    5. Worker-hosted model, ``target_worker`` pinned but that worker
       does NOT have the model — 409 with the list of workers that do.
       We never silently retarget a pinned deploy.
    6. Model not found anywhere in the cluster — 404.
    """
    config = request.app.state.config
    display_name = body.name.strip()
    name_error = validate_agent_name(display_name)
    if name_error:
        return JSONResponse({"error": name_error}, status_code=400)
    # Derive a container-safe slug and ensure uniqueness
    slug = slugify_agent_name(display_name)
    unique_slug = slug
    suffix = 2
    while find_agent(config, unique_slug):
        unique_slug = f"{slug}-{suffix}"
        suffix += 1
        if suffix > 100:
            return JSONResponse({"error": "Could not generate a unique agent slug"}, status_code=400)
    # Rewrite body.name to the unique slug; the original user-entered name
    # is preserved as display_name for the UI.
    body.name = unique_slug
    # Validate framework against catalog instead of hardcoded list
    if body.framework != "none":
        registry = request.app.state.registry
        known = {a.id for a in registry.list_available(type_filter="agent-framework")}
        if body.framework not in known:
            return JSONResponse({"error": f"Unknown framework '{body.framework}'. Available: {sorted(known)}"}, status_code=400)

    # ------------------------------------------------------------------
    # Cross-worker deploy routing (task #176 stub)
    # ------------------------------------------------------------------
    # Resolve the requested model's host BEFORE we touch config or kick
    # off a background deploy. If the model lives on a remote worker we
    # need to either route there (no local container) or reject the
    # deploy with a clear 409 when the user's pin conflicts with where
    # the model actually is.
    if body.model:
        from tinyagentos.cluster.model_resolver import find_model_hosts

        cluster = getattr(request.app.state, "cluster_manager", None)
        catalog = getattr(request.app.state, "backend_catalog", None)
        local_models = catalog.all_models() if catalog is not None else []

        # Cloud model ids are whatever openai/anthropic-typed backends
        # advertise. Keep this a best-effort: missing provider state just
        # means cloud models resolve as "not_found" and the caller can
        # retry once providers are configured.
        cloud_models: list[str] = []
        try:
            for b in config.backends or []:
                # All cloud provider types, not just openai/anthropic — kilocode,
                # openrouter and openai-compatible advertise routable cloud models
                # too. Mirrors providers.CLOUD_TYPES (#351 tracks consolidating
                # these lists). Without this, deploying an agent on e.g.
                # kilo-auto/free 404s as "model not found".
                if b.get("type") in _CLOUD_PROVIDER_TYPES:
                    for m in b.get("models") or []:
                        if isinstance(m, dict):
                            mid = m.get("id") or m.get("name") or ""
                        else:
                            mid = str(m)
                        if mid:
                            cloud_models.append(mid)
        except Exception:  # noqa: BLE001
            pass

        location = find_model_hosts(
            body.model,
            cluster_state=cluster,
            local_models=local_models,
            cloud_models=cloud_models,
        )

        if location.kind == "not_found":
            return JSONResponse(
                {
                    "error": (
                        f"model '{body.model}' was not found on the controller, "
                        f"on any online worker, or among configured cloud providers. "
                        f"Download it first or pick a model that is already in the cluster."
                    ),
                },
                status_code=404,
            )

        if location.kind == "worker":
            # Case 5: pin conflict — user asked for a specific worker
            # that does not hold the model.
            if body.target_worker and body.target_worker not in location.hosts:
                return JSONResponse(
                    {
                        "error": (
                            f"model '{body.model}' is not on worker "
                            f"'{body.target_worker}'. It is available on: "
                            f"{location.hosts}. Deploy there, or wait for "
                            f"Phase 1.5 network model placement."
                        ),
                        "model": body.model,
                        "pinned_worker": body.target_worker,
                        "available_on": location.hosts,
                    },
                    status_code=409,
                )

            # Cases 3 + 4: route to the worker that holds the model.
            # Phase 1.5 will actually instruct the worker to launch; for
            # now we return a 202 naming the destination so the caller
            # knows where the agent needs to land. Deliberately do NOT
            # add a local agent entry — a ghost entry on the controller
            # for an agent that lives on Fedora would confuse both the
            # UI and the LXC bulk-ops endpoints.
            chosen = body.target_worker or location.canonical_host
            return JSONResponse(
                {
                    "status": "routed",
                    "name": body.name,
                    "model": body.model,
                    "worker": chosen,
                    "available_on": location.hosts,
                    "message": (
                        f"model '{body.model}' lives on worker '{chosen}'. "
                        f"Routed deploy target only — remote launch lands "
                        f"with Phase 1.5 network model placement."
                    ),
                },
                status_code=202,
            )
        # kind == "controller" or "cloud": fall through to the unchanged
        # controller-local deploy path below.

    # Register the agent with taosmd BEFORE mutating config so a failure
    # here aborts cleanly with no half-state.
    try:
        tm_agents.register_agent(unique_slug)
    except tm_agents.AgentExistsError:
        pass  # idempotent — agent already registered, proceed normally
    except Exception as e:
        logger.exception("register_agent(%s) failed", unique_slug)
        return JSONResponse(
            {"error": f"Could not register agent with taosmd: {e}"},
            status_code=500,
        )

    # Add agent entry immediately with deploying status. qmd_url has
    # been removed from the agent schema — every agent reads and writes
    # through the shared host qmd.service, addressed by agent name and
    # the bind-mounted per-agent SQLite at /memory. See
    # docs/design/framework-agnostic-runtime.md.
    from tinyagentos.config import normalize_agent
    emoji = (body.emoji or "").strip() or None
    new_agent = normalize_agent({
        "name": body.name,
        "display_name": display_name,
        "host": "",
        "color": body.color,
        "emoji": emoji,
        "status": "deploying",
        "can_read_user_memory": body.can_read_user_memory,
        "on_worker_failure": body.on_worker_failure,
        "fallback_models": [m for m in body.fallback_models if m],
        "model": body.model,
        "framework": body.framework,
    })
    # Apply persona fields to the agent record.
    new_agent["soul_md"] = body.soul_md
    new_agent["agent_md"] = body.agent_md
    new_agent["memory_plugin"] = body.memory_plugin
    new_agent["memory_config"] = body.memory_config
    new_agent["source_persona_id"] = body.source_persona_id
    new_agent["migrated_to_v2_personas"] = True

    config.agents.append(new_agent)
    await save_config_locked(config, config.config_path)

    # Write to user persona library if requested.
    if body.save_to_library:
        store = getattr(request.app.state, "user_personas", None)
        if store is not None:
            store.create(
                name=body.save_to_library.get("name") or body.name,
                description=body.save_to_library.get("description"),
                soul_md=body.soul_md,
                agent_md=body.agent_md,
            )

    # Record deploy task
    deploy_tasks = request.app.state.deploy_tasks
    deploy_tasks[body.name] = {"status": "deploying", "name": body.name}

    from tinyagentos.deployer import deploy_agent, DeployRequest
    data_dir = request.app.state.data_dir
    llm_proxy = getattr(request.app.state, "llm_proxy", None)

    async def _background_deploy():
        try:
            result = await deploy_agent(DeployRequest(
                name=body.name,
                framework=body.framework,
                model=body.model,
                data_dir=data_dir,
                color=body.color,
                emoji=emoji,
                memory_limit=body.memory_limit,
                cpu_limit=body.cpu_limit,
                extra_config={
                    "llm_proxy": llm_proxy,
                    "registry": request.app.state.registry,
                },
                can_read_user_memory=body.can_read_user_memory,
            ))
            agent = find_agent(config, body.name)
            if result.get("success"):
                if agent is not None:
                    agent["host"] = result.get("ip", "")
                    agent["status"] = "running"
                    agent["llm_key"] = result.get("llm_key")
                    # Save config now so the bootstrap endpoint can return
                    # the llm_key before we start the openclaw service.
                    await save_config_locked(config, config.config_path)
                    # Start the openclaw service now that llm_key is written.
                    # install.sh enables the service but defers start so the
                    # bootstrap endpoint (which requires llm_key) succeeds.
                    if body.framework == "openclaw":
                        try:
                            from tinyagentos.containers import exec_in_container
                            container_name = f"taos-agent-{body.name}"
                            start_code, start_out = await exec_in_container(
                                container_name,
                                ["systemctl", "start", "openclaw.service"],
                                timeout=30,
                            )
                            if start_code != 0:
                                logger.warning(
                                    "openclaw service start returned %d: %s",
                                    start_code, start_out
                                )
                            else:
                                logger.info("openclaw.service started in %s", container_name)
                        except Exception as exc:  # noqa: BLE001
                            logger.warning("Failed to start openclaw.service: %s", exc)
                    # Auto-create a 1:1 DM channel so the Messages app
                    # shows this agent immediately. Safe to call even if
                    # the channel exists from a prior failed deploy — the
                    # store doesn't enforce uniqueness on name, so at worst
                    # you get duplicate entries. In practice this branch
                    # only runs on first successful deploy for a given
                    # agent record because the route creates the agent
                    # with status='deploying'.
                    if not agent.get("chat_channel_id"):
                        try:
                            ch_store = request.app.state.chat_channels
                            channel = await ch_store.create_channel(
                                name=display_name,
                                type="dm",
                                created_by="user",
                                members=["user", body.name],
                                description=f"Direct messages with {display_name}",
                            )
                            if channel and channel.get("id"):
                                agent["chat_channel_id"] = channel["id"]
                        except Exception as exc:  # noqa: BLE001
                            # DM channel creation failing shouldn't fail
                            # the whole deploy — the user can still see
                            # the agent in the Agents app and retry from
                            # there. Log and move on.
                            logger.warning("DM channel create failed for %s: %s", body.name, exc)
                deploy_tasks[body.name] = {"status": "success", "name": body.name, "result": result}
            else:
                if agent is not None:
                    agent["status"] = "failed"
                deploy_tasks[body.name] = {
                    "status": "failed",
                    "name": body.name,
                    "error": result.get("error", "unknown error"),
                }
        except Exception as exc:  # noqa: BLE001
            agent = find_agent(config, body.name)
            if agent is not None:
                agent["status"] = "failed"
            deploy_tasks[body.name] = {
                "status": "failed",
                "name": body.name,
                "error": str(exc),
            }
        finally:
            await save_config_locked(config, config.config_path)

    asyncio.create_task(_background_deploy())

    # Archive smoke-check: verify trace path end-to-end after provisioning.
    # A failure here does NOT abort the deploy — it surfaces a warning flag.
    archive = getattr(request.app.state, "archive", None)
    smoke_ok = False
    if archive is not None:
        try:
            await archive.record(
                event_type="agent_deployed",
                data={"slug": unique_slug, "framework": body.framework},
                agent_name=unique_slug,
                summary=f"deployed {unique_slug}",
            )
            rows = await archive.query(agent_name=unique_slug, limit=1)
            smoke_ok = bool(rows)
        except Exception:
            logger.exception("archive smoke-check failed for %s", unique_slug)
            smoke_ok = False

    return {"status": "deploying", "name": body.name, "archive_smoke_ok": smoke_ok}


@router.post("/api/agents/bulk/start")
async def bulk_start_agents(request: Request):
    """Start all agent containers."""
    from tinyagentos.containers import start_container
    config = request.app.state.config
    results = {}
    for agent in config.agents:
        name = agent["name"]
        try:
            result = await start_container(f"taos-agent-{name}")
            results[name] = {"success": result.get("success", False)}
        except Exception as e:
            results[name] = {"success": False, "error": str(e)}
    return {"action": "start", "results": results}


@router.post("/api/agents/bulk/stop")
async def bulk_stop_agents(request: Request):
    """Stop all agent containers, running graceful prepare first."""
    from tinyagentos.containers import stop_container
    config = request.app.state.config
    orchestrator = getattr(request.app.state, "orchestrator", None)
    report = {}
    if orchestrator is not None:
        report = await orchestrator.prepare("all", "stop")
    results = {}
    for agent in config.agents:
        name = agent["name"]
        try:
            result = await stop_container(f"taos-agent-{name}")
            results[name] = {"success": result.get("success", False)}
        except Exception as e:
            results[name] = {"success": False, "error": str(e)}
    return {"action": "stop", "prepare_report": report, "results": results}


@router.post("/api/agents/bulk/restart")
async def bulk_restart_agents(request: Request):
    """Restart all agent containers."""
    from tinyagentos.containers import restart_container
    config = request.app.state.config
    results = {}
    for agent in config.agents:
        name = agent["name"]
        try:
            result = await restart_container(f"taos-agent-{name}")
            results[name] = {"success": result.get("success", False)}
        except Exception as e:
            results[name] = {"success": False, "error": str(e)}
    return {"action": "restart", "results": results}


@router.post("/api/agents/{name}/start")
async def start_agent(request: Request, name: str):
    """Start an agent's LXC container."""
    from tinyagentos.containers import start_container
    return await start_container(f"taos-agent-{name}")


@router.post("/api/agents/{name}/pause")
async def pause_agent(request: Request, name: str):
    """Gracefully prepare an agent for pause (paused=True, container still running)."""
    config = request.app.state.config
    agent = find_agent(config, name)
    if not agent:
        return JSONResponse({"error": f"Agent '{name}' not found"}, status_code=404)
    orchestrator = getattr(request.app.state, "orchestrator", None)
    report = {}
    if orchestrator is not None:
        report = await orchestrator.prepare([name], "pause")
    return {"status": "paused", "name": name, "report": report}


@router.post("/api/agents/{name}/stop")
async def stop_agent(request: Request, name: str):
    """Gracefully prepare then stop an agent's LXC container."""
    from tinyagentos.containers import stop_container
    config = request.app.state.config
    agent = find_agent(config, name)
    if not agent:
        return JSONResponse({"error": f"Agent '{name}' not found"}, status_code=404)
    orchestrator = getattr(request.app.state, "orchestrator", None)
    report = {}
    if orchestrator is not None:
        report = await orchestrator.prepare([name], "stop")
    stop_result = await stop_container(f"taos-agent-{name}")
    return {"prepare_report": report, "stop_result": stop_result}


@router.post("/api/agents/{name}/restart")
async def restart_agent(request: Request, name: str):
    """Restart an agent's LXC container."""
    from tinyagentos.containers import restart_container
    return await restart_container(f"taos-agent-{name}")


@router.get("/api/agents/{name}/logs")
async def agent_logs(request: Request, name: str, lines: int = 100):
    """Get recent journal logs from an agent's container."""
    from tinyagentos.containers import get_container_logs
    logs = await get_container_logs(f"taos-agent-{name}", lines=lines)
    return {"name": name, "logs": logs}


@router.get("/api/agents/{name}/export")
async def export_agent(request: Request, name: str):
    """Export an agent's full config as portable JSON."""
    config = request.app.state.config
    agent = find_agent(config, name)
    if not agent:
        return JSONResponse({"error": f"Agent '{name}' not found"}, status_code=404)

    # Gather channel assignments
    channel_store = request.app.state.channels
    channels = await channel_store.list_for_agent(name)
    channel_export = [
        {"type": ch["type"], "config": ch.get("config", {})}
        for ch in channels
    ]

    # Gather group memberships
    relationship_mgr = request.app.state.relationships
    groups = await relationship_mgr.get_agent_groups(name)
    group_names = [g["name"] for g in groups]

    return {
        "version": EXPORT_VERSION,
        "agent": {k: v for k, v in agent.items()},
        "channels": channel_export,
        "groups": group_names,
    }


class AgentImport(BaseModel):
    version: int = 1
    agent: dict
    channels: list[dict] = []
    groups: list[str] = []


@router.post("/api/agents/import")
async def import_agent(request: Request, body: AgentImport):
    """Import an agent from an exported JSON config."""
    config = request.app.state.config

    agent_data = body.agent
    name = agent_data.get("name", "")
    if not name:
        return JSONResponse({"error": "Agent name is required in export data"}, status_code=400)
    name_error = validate_agent_name(name)
    if name_error:
        return JSONResponse({"error": name_error}, status_code=400)
    if find_agent(config, name):
        return JSONResponse({"error": f"Agent '{name}' already exists"}, status_code=409)

    # Create the agent
    config.agents.append(agent_data)
    await save_config_locked(config, config.config_path)

    # Restore channel assignments
    channel_store = request.app.state.channels
    for ch in body.channels:
        await channel_store.add(name, ch.get("type", ""), ch.get("config", {}))

    # Restore group memberships
    relationship_mgr = request.app.state.relationships
    existing_groups = await relationship_mgr.list_groups()
    group_map = {g["name"]: g["id"] for g in existing_groups}
    for group_name in body.groups:
        if group_name in group_map:
            await relationship_mgr.add_member(group_map[group_name], name)

    return {"status": "imported", "name": name}


@router.delete("/api/agents/{name}/destroy")
async def destroy_agent(request: Request, name: str):
    """Kept for API compatibility. Same behaviour as DELETE
    /api/agents/{name} — archives the agent. True permanent deletion
    happens via ``DELETE /api/agents/archived/{id}``.
    """
    result = await _archive_agent_fully(request, name)
    if "error" in result:
        return JSONResponse({"error": result["error"]}, status_code=result["status_code"])
    return result


@router.post("/api/agents/archived/{archive_id}/restore")
async def restore_archived_agent(request: Request, archive_id: str):
    import json as _json
    from tinyagentos.containers import (
        snapshot_restore, rename_container, start_container, set_env, exec_in_container,
    )

    config = request.app.state.config
    entry = next((a for a in config.archived_agents if a.get("id") == archive_id), None)
    if entry is None:
        return JSONResponse({"error": f"Archived agent '{archive_id}' not found"}, status_code=404)

    original = entry.get("original", {}) or {}
    desired_slug = entry.get("archived_slug") or original.get("name")
    if not desired_slug:
        return JSONResponse({"error": "Archive entry is corrupted (no slug)"}, status_code=500)

    snapshot_name = entry.get("snapshot_name")
    if not snapshot_name:
        return JSONResponse(
            {"error": "Archive entry has no snapshot_name — created with legacy archive path"},
            status_code=500,
        )

    # The container name is derived from the original slug (unchanged since
    # archive; we snapshot in-place, not rename).
    container = f"taos-agent-{desired_slug}"

    # Resolve slug collisions with currently-live agents.
    final_slug = desired_slug
    suffix = 2
    while find_agent(config, final_slug):
        final_slug = f"{desired_slug}-{suffix}"
        suffix += 1
        if suffix > 100:
            return JSONResponse({"error": "Could not resolve restore slug"}, status_code=500)

    data_dir = request.app.state.data_dir
    archive_base = data_dir / entry.get("archive_dir", "")

    # 1) Restore snapshot. Container must be stopped (archive left it stopped).
    snap_result = await snapshot_restore(container, snapshot_name)
    if not snap_result.get("success"):
        out = (snap_result.get("output") or "").strip()
        return JSONResponse(
            {
                "error": (
                    f"restore failed: snapshot_restore {container}/{snapshot_name} "
                    f"failed: {out or 'unknown error'}"
                ),
            },
            status_code=500,
        )

    # 2) Rename if the slug changed (collision resolution).
    rename_ok = True
    target_container = f"taos-agent-{final_slug}"
    if final_slug != desired_slug:
        try:
            rename_result = await rename_container(container, target_container)
            rename_ok = rename_result.get("success", False)
            if not rename_ok:
                logger.warning(
                    "restore: rename %s -> %s failed: %s",
                    container, target_container, rename_result.get("output", ""),
                )
        except Exception as exc:  # noqa: BLE001
            rename_ok = False
            logger.warning("restore: rename failed: %s", exc)
    else:
        target_container = container

    # 3) Start container.
    try:
        await start_container(target_container)
    except Exception as exc:  # noqa: BLE001
        logger.warning("restore: start_container failed for %s: %s", target_container, exc)

    # 4) Mint new LiteLLM key if proxy running.
    llm_proxy = getattr(request.app.state, "llm_proxy", None)
    new_key = None
    if llm_proxy and llm_proxy.is_running():
        try:
            new_key = await llm_proxy.create_agent_key(final_slug)
        except Exception:
            pass

    # 5) Update openclaw env in the restored container with the new key.
    #    Uses incus config set environment.OPENAI_API_KEY=<new> so the
    #    value persists in the container's config and does not require a
    #    bind-mounted file. Also restart openclaw.service if present.
    if new_key is not None:
        try:
            env_result = await set_env(target_container, "OPENAI_API_KEY", new_key)
            if not env_result.get("success"):
                logger.warning(
                    "restore: set_env OPENAI_API_KEY failed for %s: %s",
                    target_container, env_result.get("output", ""),
                )
            else:
                # Best-effort: restart openclaw.service if it is present.
                try:
                    await exec_in_container(
                        target_container,
                        ["systemctl", "restart", "openclaw.service"],
                        timeout=30,
                    )
                except Exception:
                    pass
        except Exception as exc:  # noqa: BLE001
            logger.warning("restore: env key update failed for %s: %s", target_container, exc)

    # 6) Re-import chat-export.jsonl from the archive path if present.
    try:
        chat_export_file = archive_base / "chat" / "chat-export.jsonl"
        if chat_export_file.exists():
            msg_store = request.app.state.chat_messages
            imported = 0
            try:
                with chat_export_file.open("r", encoding="utf-8") as fh:
                    for raw_line in fh:
                        raw_line = raw_line.strip()
                        if not raw_line:
                            continue
                        try:
                            msg = _json.loads(raw_line)
                            await msg_store.ensure_message(msg)
                            imported += 1
                        except Exception as line_exc:  # noqa: BLE001
                            logger.warning("restore: bad export line, skipping: %s", line_exc)
                logger.info("restore: re-imported %d messages from chat-export", imported)
            except Exception as exc:  # noqa: BLE001
                logger.warning("restore: chat-export read failed for %s: %s", final_slug, exc)
    except Exception as exc:  # noqa: BLE001
        logger.warning("restore: chat re-import outer failed: %s", exc)

    # 7) Unflag all channels where archived_agent_id matches this archive entry.
    agent_id_to_unflag = entry.get("id")
    try:
        ch_store = request.app.state.chat_channels
        channel_id = original.get("chat_channel_id")
        if channel_id:
            await ch_store.set_settings(channel_id, {"archived": False})
        all_channels = await ch_store.list_channels(archived=True)
        for ch in all_channels:
            ch_settings = ch.get("settings") or {}
            if ch_settings.get("archived_agent_id") == agent_id_to_unflag:
                await ch_store.set_settings(ch["id"], {"archived": False})
    except Exception as exc:  # noqa: BLE001
        logger.warning("restore: channel unflag failed: %s", exc)

    # 8) Move config entry from archived_agents back to agents.
    restored = dict(original)
    restored["name"] = final_slug
    restored["status"] = "stopped"
    restored["host"] = ""
    if new_key is not None:
        restored["llm_key"] = new_key
    config.agents.append(restored)
    config.archived_agents = [a for a in config.archived_agents if a.get("id") != archive_id]
    await save_config_locked(config, config.config_path)

    return {
        "status": "restored",
        "id": archive_id,
        "name": final_slug,
        "display_name": restored.get("display_name", final_slug),
        "container_renamed": rename_ok,
        "new_llm_key": new_key is not None,
    }


@router.delete("/api/agents/archived/{archive_id}")
async def purge_archived_agent(request: Request, archive_id: str):
    """True permanent deletion: destroys the archived container (and all its
    snapshots) via ``incus delete --force``, wipes any exported tarball,
    deletes chat channels and messages, and drops the config entry.
    Irreversible.
    """
    import shutil
    from tinyagentos.containers import destroy_container

    config = request.app.state.config
    entry = next((a for a in config.archived_agents if a.get("id") == archive_id), None)
    if entry is None:
        return JSONResponse({"error": f"Archived agent '{archive_id}' not found"}, status_code=404)

    archived_slug = entry.get("archived_slug") or (entry.get("original") or {}).get("name") or ""
    container_name = f"taos-agent-{archived_slug}" if archived_slug else ""
    data_dir = request.app.state.data_dir
    archive_base = data_dir / entry.get("archive_dir", "")

    # 1) Destroy container (destroys all snapshots too in incus).
    #    "not found" is fine — container may already be gone.
    if container_name:
        try:
            await destroy_container(container_name)
        except Exception:
            pass

    # 2) Wipe exported archive tarball path if one was recorded.
    export_path = entry.get("export_path")
    if export_path:
        import os as _os
        try:
            if _os.path.isdir(export_path):
                shutil.rmtree(export_path, ignore_errors=True)
            elif _os.path.isfile(export_path):
                _os.unlink(export_path)
        except Exception as exc:  # noqa: BLE001
            logger.warning("purge: export path cleanup failed %s: %s", export_path, exc)

    # 3) Wipe archive dir (chat-export + any other host-side state).
    if archive_base.exists():
        try:
            shutil.rmtree(archive_base, ignore_errors=True)
        except Exception:
            pass

    # 4) Delete messages + channels for every DM channel belonging to this agent.
    archive_id_for_purge = entry.get("id")
    try:
        ch_store = request.app.state.chat_channels
        msg_store = request.app.state.chat_messages
        channels_to_purge: list[str] = []
        channel_id = (entry.get("original") or {}).get("chat_channel_id")
        if channel_id:
            channels_to_purge.append(channel_id)
        try:
            archived_channels = await ch_store.list_channels(archived=True)
            for ch in archived_channels:
                ch_settings = ch.get("settings") or {}
                if (
                    ch_settings.get("archived_agent_id") == archive_id_for_purge
                    and ch["id"] not in channels_to_purge
                ):
                    channels_to_purge.append(ch["id"])
        except Exception as exc:  # noqa: BLE001
            logger.warning("purge: channel scan failed: %s", exc)

        for cid in channels_to_purge:
            try:
                await msg_store.delete_channel_messages(cid)
            except Exception as exc:  # noqa: BLE001
                logger.warning("purge: delete messages for channel %s failed: %s", cid, exc)
            try:
                await ch_store.delete_channel(cid)
            except Exception as exc:  # noqa: BLE001
                logger.warning("purge: delete channel %s failed: %s", cid, exc)
    except Exception as exc:  # noqa: BLE001
        logger.warning("purge: channel/message cleanup failed: %s", exc)

    # 5) Drop archived_agents entry.
    config.archived_agents = [a for a in config.archived_agents if a.get("id") != archive_id]
    await save_config_locked(config, config.config_path)

    return {"status": "purged", "id": archive_id}


@router.post("/api/agents/{name}/resume")
async def resume_agent(request: Request, name: str):
    """Clear the paused flag on an agent, allowing it to accept new calls."""
    config = request.app.state.config
    agent = find_agent(config, name)
    if not agent:
        return JSONResponse({"error": f"Agent '{name}' not found"}, status_code=404)
    if not agent.get("paused", False):
        return {"status": "ok", "name": name, "paused": False}
    agent["paused"] = False
    await save_config_locked(config, config.config_path)
    return {"status": "resumed", "name": name, "paused": False}


class PersonaPatch(BaseModel):
    soul_md: str | None = None
    agent_md: str | None = None
    source_persona_id: str | None = None


@router.patch("/api/agents/{slug}/persona")
async def patch_agent_persona(request: Request, slug: str, body: PersonaPatch):
    """Partially update an agent's persona fields (soul_md, agent_md, source_persona_id)."""
    config = request.app.state.config
    agent = find_agent(config, slug)
    if not agent:
        return JSONResponse({"error": "agent not found"}, status_code=404)
    if body.soul_md is not None:
        agent["soul_md"] = body.soul_md
    if body.agent_md is not None:
        agent["agent_md"] = body.agent_md
    if body.source_persona_id is not None:
        agent["source_persona_id"] = body.source_persona_id
    await save_config_locked(config, config.config_path)
    return {"status": "ok", "agent": agent}


class MemoryPatch(BaseModel):
    memory_plugin: str


_VALID_MEMORY_PLUGINS = {"taosmd", "none"}


@router.patch("/api/agents/{slug}/memory")
async def patch_agent_memory(request: Request, slug: str, body: MemoryPatch):
    """Set the memory_plugin for an agent. Valid values: 'taosmd', 'none'."""
    if body.memory_plugin not in _VALID_MEMORY_PLUGINS:
        return JSONResponse(
            {"error": f"Invalid memory_plugin '{body.memory_plugin}'. Must be one of: {sorted(_VALID_MEMORY_PLUGINS)}"},
            status_code=400,
        )
    config = request.app.state.config
    agent = find_agent(config, slug)
    if not agent:
        return JSONResponse({"error": "agent not found"}, status_code=404)
    agent["memory_plugin"] = body.memory_plugin
    await save_config_locked(config, config.config_path)
    return {"status": "ok", "agent": agent}


@router.post("/api/agents/{slug}/dismiss-migration-banner")
async def dismiss_migration_banner(request: Request, slug: str):
    """Flip migrated_to_v2_personas to True, hiding the migration banner."""
    config = request.app.state.config
    agent = find_agent(config, slug)
    if not agent:
        return JSONResponse({"error": f"Agent '{slug}' not found"}, status_code=404)
    agent["migrated_to_v2_personas"] = True
    await save_config_locked(config, config.config_path)
    return {"status": "ok"}


class AgentModelUpdate(BaseModel):
    model: str


@router.post("/api/agents/{name}/model")
async def update_agent_model(request: Request, name: str, body: AgentModelUpdate):
    """Update an agent's primary model and resume it if it was paused.

    Validates the requested model against currently-reachable cluster models
    (local backend catalog + online workers).  Returns 409 if the model is
    not reachable anywhere in the cluster right now.
    """
    config = request.app.state.config
    agent = find_agent(config, name)
    if not agent:
        return JSONResponse({"error": f"Agent '{name}' not found"}, status_code=404)

    model_id = body.model.strip()
    if not model_id:
        return JSONResponse({"error": "model must not be empty"}, status_code=400)

    # Validate reachability against the live cluster state.
    from tinyagentos.cluster.model_resolver import find_model_hosts

    cluster = getattr(request.app.state, "cluster_manager", None)
    catalog = getattr(request.app.state, "backend_catalog", None)
    local_models = catalog.all_models() if catalog is not None else []

    cloud_models: list[str] = []
    try:
        for b in config.backends or []:
            # All cloud provider types (see _CLOUD_PROVIDER_TYPES / #351), so a
            # model change to a kilocode/openrouter/openai-compatible model
            # resolves instead of 404ing.
            if b.get("type") in _CLOUD_PROVIDER_TYPES:
                for m in b.get("models") or []:
                    mid = (m.get("id") or m.get("name") or "") if isinstance(m, dict) else str(m)
                    if mid:
                        cloud_models.append(mid)
    except Exception:  # noqa: BLE001
        pass

    location = find_model_hosts(
        model_id,
        cluster_state=cluster,
        local_models=local_models,
        cloud_models=cloud_models,
    )

    if location.kind == "not_found":
        return JSONResponse(
            {
                "error": (
                    f"model '{model_id}' is not reachable anywhere in the cluster right now. "
                    f"Make sure the model is downloaded and the worker is online."
                ),
                "model": model_id,
            },
            status_code=409,
        )

    # Update the agent's model and clear the paused flag so it can
    # immediately start accepting calls on the new model.
    agent["model"] = model_id
    was_paused = agent.get("paused", False)
    agent["paused"] = False
    await save_config_locked(config, config.config_path)

    return {
        "status": "updated",
        "name": name,
        "model": model_id,
        "resumed": was_paused,
        "location": location.kind,
    }
