"""Agent-archive lifecycle helpers extracted from agents.py.

Contains the shared archive/restore/purge logic used by the agent route
handlers in agents.py. Route decorators and thin wrappers remain in
agents.py; only the business logic lives here.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone
from pathlib import Path

from fastapi import Request

from tinyagentos.agent_db import find_agent
from tinyagentos.config import save_config_locked, unique_agent_slug
from tinyagentos.containers import SNAPSHOT_TIMEOUT_S

logger = logging.getLogger(__name__)

# Serializes restores from the archive-entry lookup through the config
# publication. Without it two restores (of one archive, or of two archives
# wanting the same slug) both pass the lookup and unique_agent_slug, both mint
# a local token for the same name (the second revoking the first) and both
# append the agent. Per-process: it does not span controller workers.
_restore_lock = asyncio.Lock()


def _safe_archive_base(data_dir: Path, entry: dict) -> Path | None:
    """Return resolved archive base path only if it is strictly inside data_dir/archive.

    Returns None and logs a warning when archive_dir is missing, empty, absolute,
    contains path traversal (..), or resolves outside the archive root.
    """
    archive_dir = entry.get("archive_dir", "")
    archive_id = entry.get("id", "unknown")
    if not archive_dir or not isinstance(archive_dir, str):
        logger.warning("purge/restore: archive entry %s has missing or empty archive_dir", archive_id)
        return None
    # Reject absolute paths (pathlib drops left side for absolute right side)
    if Path(archive_dir).is_absolute():
        logger.warning("purge/restore: archive entry %s has absolute archive_dir: %s", archive_id, archive_dir)
        return None
    archive_root = (data_dir / "archive").resolve()
    candidate = (data_dir / archive_dir).resolve()
    # Must be strictly inside archive_root (not equal, and is_relative_to)
    try:
        if not candidate.is_relative_to(archive_root) or candidate == archive_root:
            logger.warning(
                "purge/restore: archive entry %s archive_dir %s escapes archive root",
                archive_id, archive_dir,
            )
            return None
    except Exception:
        logger.warning(
            "purge/restore: archive entry %s archive_dir %s caused resolution error",
            archive_id, archive_dir,
        )
        return None
    return candidate


def _archive_timestamp() -> str:
    """UTC timestamp as YYYYMMDDTHHMMSS for archive naming."""
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S")


def _revoke_gateway_keys(request: Request, slug: str, agent_id: str | None) -> None:
    """Revoke the agent's local token, then LLM gateway keys bound to it (by slug and by id).

    Legacy per-agent keys are keyed by the agent NAME (slug); gateway keys may
    be bound to either, so both are revoked.

    Raises when the local token cannot be revoked: an archive that reported
    success while the token still authenticates would leave a live credential
    for an agent the config no longer lists. Callers must stop before
    changing the config.
    """
    from tinyagentos.auth import AuthManager
    from tinyagentos.llm_gateway.auth import revoke_keys_for

    data_dir = request.app.state.data_dir
    # The per-agent local token first: it is bound by name, and a later deploy
    # may take the name. Restore mints a fresh one.
    AuthManager(data_dir).unbind_local_token_agent(slug)
    for principal in {slug, agent_id} - {None, ""}:
        revoke_keys_for(principal, data_dir=data_dir)


async def archive_agent_fully(request: Request, name: str) -> dict:
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
    from tinyagentos.containers import (
        container_exists,
        resolve_agent_container,
        stop_container,
        snapshot_create,
    )

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

    # 0a) Record lost its container link, or the standard name is gone: probe
    #     the legacy ``taos-<slug>`` convention and display-name variants across
    #     ALL projects before treating this as an orphan row. A record whose
    #     container_name drifted (e.g. an old "test" agent backed by a live
    #     ``taos-test`` container) must NOT be hard-deleted while its container
    #     keeps running -- resolve it and archive it through the snapshot path.
    if not has_container:
        resolved = await resolve_agent_container(slug, agent.get("display_name"))
        if resolved:
            container = resolved
            has_container = True

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

        # Always revoke the agent's credentials first; stop if the local token
        # cannot be revoked (see _revoke_gateway_keys).
        try:
            _revoke_gateway_keys(request, slug, agent_id)
        except Exception as exc:  # noqa: BLE001
            logger.warning("archive(orphan): credential revoke failed for %s: %s", slug, exc)
            return {"error": f"archive failed: credential revoke failed: {exc}", "status_code": 500}
        llm_key = agent.get("llm_key")
        llm_proxy = getattr(request.app.state, "llm_proxy", None)
        if llm_key and llm_proxy:
            try:
                await llm_proxy.delete_agent_key(llm_key)
            except Exception:
                pass

        if not has_chat_history and not has_trace_history:
            # Hard-delete: drop the config row and return. No tombstone for
            # orphan rows from a never-used failed deploy. A DM channel may
            # still exist (created on a prior deploy) with no messages -- archive
            # it with the agent linkage so it is never left as a live orphan.
            if channel_id:
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
                    logger.warning(
                        "archive(orphan): channel flag failed for %s: %s",
                        channel_id, exc,
                    )
            config.agents = [a for a in config.agents if a["name"] != name]
            await save_config_locked(config, config.config_path)
            _deploy_tasks = getattr(request.app.state, "deploy_tasks", None)
            if _deploy_tasks is not None:
                _deploy_tasks.pop(slug, None)
            try:
                request.app.state.auth.unbind_local_token_agent(slug)
            except Exception as exc:  # noqa: BLE001 - best-effort revocation
                logger.warning("archive(orphan): unbind local token for %s failed: %s", slug, exc)
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
        _deploy_tasks = getattr(request.app.state, "deploy_tasks", None)
        if _deploy_tasks is not None:
            _deploy_tasks.pop(slug, None)
        try:
            request.app.state.auth.unbind_local_token_agent(slug)
        except Exception as exc:  # noqa: BLE001 - best-effort revocation
            logger.warning("archive(orphan): unbind local token for %s failed: %s", slug, exc)
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
    try:
        snap_result = await snapshot_create(container, snapshot_name)
    except asyncio.TimeoutError:
        return {
            "error": (
                f"archive failed: snapshot {snapshot_name} on {container} "
                f"did not finish within {SNAPSHOT_TIMEOUT_S}s (dir-backed pools "
                f"copy the full rootfs). Agent left in live list; check "
                f"`incus snapshot list {container}` for a partial taos-archive "
                f"snapshot before retrying."
            ),
            "status_code": 504,
        }

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
            tarball_dir = Path(dest_dir) / archive_subdir
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

    # 5) Revoke the agent's key-store key (best effort) and every LLM gateway
    #    key bound to the agent. Both stores are local; nothing is gated on a
    #    process. A failed local-token revoke stops here, before the config
    #    moves the agent out of agents, so the archive can be retried.
    try:
        _revoke_gateway_keys(request, slug, agent_id)
    except Exception as exc:  # noqa: BLE001
        logger.warning("archive: credential revoke failed for %s: %s", slug, exc)
        return {"error": f"archive failed: credential revoke failed: {exc}", "status_code": 500}
    llm_key = agent.get("llm_key")
    llm_proxy = getattr(request.app.state, "llm_proxy", None)
    if llm_key and llm_proxy:
        try:
            await llm_proxy.delete_agent_key(llm_key)
        except Exception:
            pass

    # 6) Move config entry out of agents into archived_agents. Record the
    #    actual container name (which may be the legacy ``taos-<slug>`` form when
    #    resolved in step 0a) so restore/purge act on the real container rather
    #    than the derived ``taos-agent-<slug>`` name.
    original_snapshot = dict(agent)
    archive_entry = {
        "id": agent_id,
        "archived_at": ts,
        "archived_slug": slug,
        "container_name": container,
        "snapshot_name": snapshot_name,
        "export_path": export_path,
        "archive_dir": f"archive/{archive_subdir}",
        "original": original_snapshot,
    }
    config.agents = [a for a in config.agents if a["name"] != name]
    config.archived_agents.append(archive_entry)
    await save_config_locked(config, config.config_path)

    _deploy_tasks = getattr(request.app.state, "deploy_tasks", None)
    if _deploy_tasks is not None:
        _deploy_tasks.pop(slug, None)

    return {
        "status": "archived",
        "name": slug,
        "id": agent_id,
        "archived_at": ts,
        "snapshot_name": snapshot_name,
        "export_path": export_path,
    }


async def restore_archived(request: Request, archive_id: str):
    async with _restore_lock:
        return await _restore_archived(request, archive_id)


async def _restore_archived(request: Request, archive_id: str):
    import json as _json
    from tinyagentos.containers import (
        snapshot_restore, rename_container, start_container, set_env, exec_in_container,
    )

    config = request.app.state.config
    entry = next((a for a in config.archived_agents if a.get("id") == archive_id), None)
    if entry is None:
        from fastapi.responses import JSONResponse
        return JSONResponse({"error": f"Archived agent '{archive_id}' not found"}, status_code=404)

    original = entry.get("original", {}) or {}
    desired_slug = entry.get("archived_slug") or original.get("name")
    if not desired_slug:
        from fastapi.responses import JSONResponse
        return JSONResponse({"error": "Archive entry is corrupted (no slug)"}, status_code=500)

    snapshot_name = entry.get("snapshot_name")
    if not snapshot_name:
        from fastapi.responses import JSONResponse
        return JSONResponse(
            {"error": "Archive entry has no snapshot_name — created with legacy archive path"},
            status_code=500,
        )

    # The container name is whatever was archived. Prefer the recorded
    # ``container_name`` (set by the archive path, and may be the legacy
    # ``taos-<slug>`` form); fall back to the derived name for entries written
    # before that field existed.
    container = entry.get("container_name") or f"taos-agent-{desired_slug}"

    # Resolve slug collisions with currently-live agents.
    try:
        final_slug = unique_agent_slug(config, desired_slug)
    except ValueError:
        from fastapi.responses import JSONResponse
        return JSONResponse({"error": "Could not resolve restore slug"}, status_code=500)

    data_dir = request.app.state.data_dir
    archive_base = _safe_archive_base(data_dir, entry)

    # 1) Restore snapshot. Container must be stopped (archive left it stopped).
    snap_result = await snapshot_restore(container, snapshot_name)
    if not snap_result.get("success"):
        out = (snap_result.get("output") or "").strip()
        from fastapi.responses import JSONResponse
        return JSONResponse(
            {
                "error": (
                    f"restore failed: snapshot_restore {container}/{snapshot_name} "
                    f"failed: {out or 'unknown error'}"
                ),
            },
            status_code=500,
        )

    # 1b) Re-mint the per-agent local token under the restored name before the
    #     container starts. The archived token is bound to the old name, which
    #     a later deploy may now hold (and whose mint revoked it). Done before
    #     the rename (the env is instance config, which a rename keeps) so a
    #     failure leaves the container stopped under the name the archive entry
    #     records, and the restore can be retried.
    try:
        from tinyagentos.auth import AuthManager
        local_token = AuthManager(request.app.state.data_dir).mint_agent_local_token(final_slug)
        env_result = await set_env(container, "TAOS_LOCAL_TOKEN", local_token)
        if not env_result.get("success"):
            raise RuntimeError(
                f"set_env TAOS_LOCAL_TOKEN failed: {env_result.get('output') or 'unknown error'}"
            )
    except Exception as exc:  # noqa: BLE001
        logger.warning("restore: local token re-mint failed for %s: %s", container, exc)
        from fastapi.responses import JSONResponse
        return JSONResponse(
            {"error": f"restore failed: local token re-mint failed: {exc}"},
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

    # 4) Mint a new key in the local key store.
    llm_proxy = getattr(request.app.state, "llm_proxy", None)
    new_key = None
    if llm_proxy:
        # Scoped to the models the agent had when archived (the default alias
        # when it had none), as a deploy would scope it.
        restore_models = [
            m for m in [original.get("model"), *(original.get("fallback_models") or [])] if m
        ]
        try:
            new_key = await llm_proxy.create_agent_key(final_slug, models=restore_models or None)
        except Exception:
            pass

    # 5) Update openclaw env in the restored container with the new key.
    #    Uses incus config set environment.OPENAI_API_KEY=<new> so the
    #    value persists in the container's config and does not require a
    #    bind-mounted file. Also restart openclaw.service if present.
    if new_key is not None:
        try:
            # LITELLM_API_KEY first: the framework units read that name.
            await set_env(target_container, "LITELLM_API_KEY", new_key)
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
        if archive_base is not None:
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

    _deploy_tasks = getattr(request.app.state, "deploy_tasks", None)
    if _deploy_tasks is not None:
        _deploy_tasks.pop(final_slug, None)

    return {
        "status": "restored",
        "id": archive_id,
        "name": final_slug,
        "display_name": restored.get("display_name", final_slug),
        "container_renamed": rename_ok,
        "new_llm_key": new_key is not None,
    }


async def purge_archived(request: Request, archive_id: str):
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
        from fastapi.responses import JSONResponse
        return JSONResponse({"error": f"Archived agent '{archive_id}' not found"}, status_code=404)

    archived_slug = entry.get("archived_slug") or (entry.get("original") or {}).get("name") or ""
    container_name = entry.get("container_name") or (
        f"taos-agent-{archived_slug}" if archived_slug else ""
    )
    data_dir = request.app.state.data_dir
    archive_base = _safe_archive_base(data_dir, entry)

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
    if archive_base is not None and archive_base.exists():
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

    # 5) Revoke the purged agent's local token, unless a live agent has since
    #    taken the name (its deploy already revoked the old token).
    #    A failed revoke keeps the archived entry so the purge can be retried
    #    (destroy and the archive-dir wipe both tolerate a second run).
    original_name = (entry.get("original") or {}).get("name") or ""
    if original_name and not any(a.get("name") == original_name for a in config.agents):
        try:
            from tinyagentos.auth import AuthManager
            AuthManager(data_dir).unbind_local_token_agent(original_name)
        except Exception as exc:  # noqa: BLE001
            logger.warning("purge: local token revoke failed for %s: %s", original_name, exc)
            from fastapi.responses import JSONResponse
            return JSONResponse(
                {"error": f"purge failed: local token revoke failed: {exc}"},
                status_code=500,
            )

    # 6) Drop archived_agents entry.
    config.archived_agents = [a for a in config.archived_agents if a.get("id") != archive_id]
    await save_config_locked(config, config.config_path)

    # 5a) Revoke the agent's local-token binding so the old token no longer
    # validates as an admin credential. Skip when a live agent holds the
    # slug: it is free again once the agent is archived, so a redeployed
    # agent may have taken it, and that live agent's token must survive.
    if archived_slug:
        if any(a.get("name") == archived_slug for a in config.agents):
            logger.info(
                "purge: slug %s in use by a live agent, "
                "skipping local token unbind",
                archived_slug,
            )
        else:
            try:
                request.app.state.auth.unbind_local_token_agent(archived_slug)
            except Exception as exc:  # noqa: BLE001 - best-effort revocation
                logger.warning(
                    "purge: unbind local token for %s failed: %s",
                    archived_slug, exc,
                )

    _deploy_tasks = getattr(request.app.state, "deploy_tasks", None)
    if _deploy_tasks is not None:
        _deploy_tasks.pop(original_name, None)

    return {"status": "purged", "id": archive_id}
