"""Agent-archive lifecycle helpers extracted from agents.py.

Contains the shared archive/restore/purge logic used by the agent route
handlers in agents.py. Route decorators and thin wrappers remain in
agents.py; only the business logic lives here.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone

from fastapi import Request

from tinyagentos.agent_db import find_agent
from tinyagentos.config import save_config_locked, unique_agent_slug

logger = logging.getLogger(__name__)


def _archive_timestamp() -> str:
    """UTC timestamp as YYYYMMDDTHHMMSS for archive naming."""
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S")


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


async def restore_archived(request: Request, archive_id: str):
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

    # The container name is derived from the original slug (unchanged since
    # archive; we snapshot in-place, not rename).
    container = f"taos-agent-{desired_slug}"

    # Resolve slug collisions with currently-live agents.
    try:
        final_slug = unique_agent_slug(config, desired_slug)
    except ValueError:
        from fastapi.responses import JSONResponse
        return JSONResponse({"error": "Could not resolve restore slug"}, status_code=500)

    data_dir = request.app.state.data_dir
    archive_base = data_dir / entry.get("archive_dir", "")

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
