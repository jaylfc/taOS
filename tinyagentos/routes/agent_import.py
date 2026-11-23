"""Hermes profile-bundle import: deploy a Hermes agent then restore its
exported profile inside the container.

The user uploads a Hermes profile EXPORT bundle (produced by
``hermes profile export <name>``). taOS deploys a fresh Hermes-framework
agent reusing the normal deploy path, and as a post-provision step pushes
the bundle into the container and runs ``hermes profile import`` so the
agent's personality (SOUL.md, config.yaml, skills, cron, mcp.json) is
restored. Secrets/.env are excluded from the bundle by design; the user
supplies them and taOS injects them through the same agent-secrets path
deploy already uses.

Route decorators stay in agents.py; only the business logic lives here.
"""

from __future__ import annotations

import json
import logging
import os
import tarfile
import tempfile
from datetime import datetime, UTC
from pathlib import Path

from fastapi import Request, UploadFile
from fastapi.responses import JSONResponse

from tinyagentos.agent_db import find_agent
from tinyagentos.agent_controller_listener import agent_controller_live_port
from tinyagentos.llm_gateway.cutover import llm_gateway_live_port, llm_gateway_models_check
from tinyagentos.config import (
    normalize_agent,
    save_config_locked,
    unique_agent_slug,
    validate_agent_name,
    reserve_agent_slug,
    release_agent_slug,
)

logger = logging.getLogger(__name__)

# Default memory limit for MEMORY.md (chars). Profile config can override.
DEFAULT_MEMORY_CHAR_LIMIT = 2200

# Accept only Hermes for now; the frontend locks the framework picker to it.
SUPPORTED_IMPORT_FRAMEWORK = "hermes"

# Reject empty or oversize uploads up front. 200 MB mirrors the client-side
# cap in the ImportWizard so the user sees the same limit on both ends.
MAX_BUNDLE_BYTES = 200 * 1024 * 1024

# Accepted bundle extensions (Hermes exports a tarball; zip is NOT accepted
# because Hermes only accepts tar.gz and we must validate/strip members).
_ACCEPTED_EXTENSIONS = (".tar.gz", ".tgz")

# Where the bundle lands inside the container before `hermes profile import`.
_CONTAINER_BUNDLE_PATH = "/tmp/hermes-import-bundle"

# Probed binary locations for the hermes CLI inside the container, matching
# find_hermes() in scripts/install_hermes.sh.
_HERMES_BIN_CANDIDATES = (
    "/root/.local/bin/hermes",
    "/usr/local/bin/hermes",
    "/root/.hermes/hermes-agent/.venv/bin/hermes",
    "/root/.hermes/hermes-agent/venv/bin/hermes",
)

# Host-side bundle paths, keyed by agent slug, consumed by the push step.
_BUNDLE_PATHS: dict[str, str] = {}


def _has_accepted_extension(filename: str) -> bool:
    lower = (filename or "").lower()
    return any(lower.endswith(ext) for ext in _ACCEPTED_EXTENSIONS)


def _validate_bundle_members(host_bundle: str, original_filename: str = "") -> tuple[list[str], list[str], set[str]]:
    """Validate tar bundle members before handing to Hermes.

    Returns (dropped_files, errors, dropped_names). Errors cause import rejection.
    Dropped files are non-regular members (symlinks, devices, FIFOs, etc.)
    that are stripped from the bundle with a per-file note.
    Directories are NOT reported as drops (they are structural).
    dropped_names is a set of member names that were dropped, for reliable rewriting.
    """
    dropped = []
    dropped_names = set()
    errors = []

    is_tar_gz = (host_bundle.endswith(".tar.gz") or host_bundle.endswith(".tgz") or
                 original_filename.endswith(".tar.gz") or original_filename.endswith(".tgz"))
    if not is_tar_gz:
        errors.append("only .tar.gz / .tgz bundles are supported (zip is not accepted)")
        return dropped, errors, dropped_names

    try:
        with tarfile.open(host_bundle, "r:gz") as tar:
            for member in tar.getmembers():
                name = member.name

                # Reject absolute paths
                if os.path.isabs(name):
                    errors.append(f"absolute path not allowed: {name}")
                    continue

                # Reject paths with .. traversal
                if ".." in Path(name).parts:
                    errors.append(f"path traversal not allowed: {name}")
                    continue

                # Drop non-regular files (symlinks, devices, FIFOs, etc.)
                # Directories are kept (structural) and not reported as drops.
                if member.isdir():
                    continue
                if not member.isfile():
                    member_type = {
                        tarfile.SYMTYPE: "symlink",
                        tarfile.LNKTYPE: "hardlink",
                        tarfile.CHRTYPE: "character device",
                        tarfile.BLKTYPE: "block device",
                        tarfile.FIFOTYPE: "FIFO",
                    }.get(member.type, f"type {member.type}")
                    dropped.append(f"{name} ({member_type})")
                    dropped_names.add(name)
                    continue
    except tarfile.ReadError as e:
        errors.append(f"invalid tar archive: {e}")
    except Exception as e:
        errors.append(f"bundle validation failed: {e}")

    return dropped, errors, dropped_names


def _rewrite_bundle_stripping_dropped(host_bundle: str, dropped_names: set[str]) -> str:
    """Create a new tar.gz containing only regular files and directories from
    the original bundle. Returns the path to the rewritten bundle.
    """
    new_path = host_bundle + ".stripped"
    try:
        with tarfile.open(host_bundle, "r:gz") as src:
            with tarfile.open(new_path, "w:gz") as dst:
                for member in src.getmembers():
                    name = member.name
                    if name in dropped_names:
                        continue
                    # Re-read the file data for regular files
                    if member.isfile():
                        file_data = src.extractfile(member)
                        if file_data:
                            dst.addfile(member, file_data)
                        else:
                            # Empty file
                            dst.addfile(member)
                    else:
                        # Directory or other kept type
                        dst.addfile(member)
    except Exception as e:
        _safe_unlink(new_path)
        raise RuntimeError(f"failed to rewrite bundle: {e}")

    return new_path


async def import_hermes_agent(
    request: Request,
    framework: str,
    bundle: UploadFile,
    name: str,
    model: str | None,
    secrets: str | None,
    pause_jobs: bool = False,
    source_host: str | None = None,
) -> JSONResponse:
    """Validate the upload, deploy a Hermes agent, and schedule the profile
    import as a post-provision step. Returns an ``importing`` status without
    blocking on the full import, mirroring the deploy endpoint.
    """
    import asyncio

    # --- Framework gate ---------------------------------------------------
    if framework != SUPPORTED_IMPORT_FRAMEWORK:
        return JSONResponse(
            {"error": f"only {SUPPORTED_IMPORT_FRAMEWORK} import is supported yet, got '{framework}'"},
            status_code=400,
        )

    # --- Bundle validation ------------------------------------------------
    if bundle is None or not bundle.filename:
        return JSONResponse({"error": "a profile bundle file is required"}, status_code=400)
    if not _has_accepted_extension(bundle.filename):
        return JSONResponse(
            {"error": f"bundle must be one of {', '.join(_ACCEPTED_EXTENSIONS)}"},
            status_code=400,
        )

    # Stream to a temp file with a hard size cap so a huge upload can't
    # exhaust disk. The bundle is NEVER extracted on the host, it is only
    # consumed inside the sandboxed container.
    tmp_fd, tmp_path = tempfile.mkstemp(prefix="taos-hermes-import-", suffix=".bundle")
    total = 0
    try:
        with os.fdopen(tmp_fd, "wb") as out:
            while True:
                chunk = await bundle.read(1024 * 1024)
                if not chunk:
                    break
                total += len(chunk)
                if total > MAX_BUNDLE_BYTES:
                    _safe_unlink(tmp_path)
                    return JSONResponse(
                        {"error": f"bundle exceeds the {MAX_BUNDLE_BYTES // (1024 * 1024)} MB limit"},
                        status_code=400,
                    )
                out.write(chunk)
    except Exception:
        _safe_unlink(tmp_path)
        raise
    if total == 0:
        _safe_unlink(tmp_path)
        return JSONResponse({"error": "bundle file is empty"}, status_code=400)

    # --- Name validation + slug reservation (before bundle validation) ---
    display_name = (name or "").strip()
    name_error = validate_agent_name(display_name)
    if name_error:
        _safe_unlink(tmp_path)
        return JSONResponse({"error": name_error}, status_code=400)

    config = request.app.state.config
    try:
        unique_slug = await reserve_agent_slug(config, display_name)
    except ValueError as e:
        _safe_unlink(tmp_path)
        return JSONResponse({"error": str(e)}, status_code=400)

    # --- Validate bundle members (tar.gz only) ---
    dropped_files, validation_errors, dropped_names = _validate_bundle_members(tmp_path, bundle.filename or "")
    if validation_errors:
        _safe_unlink(tmp_path)
        release_agent_slug(unique_slug)
        return JSONResponse(
            {"error": "bundle validation failed", "details": validation_errors},
            status_code=400,
        )

    try:
        # --- Parse secrets ----------------------------------------------------
        secrets_map: dict[str, str] = {}
        if secrets:
            try:
                parsed = json.loads(secrets)
            except (ValueError, TypeError):
                _safe_unlink(tmp_path)
                return JSONResponse({"error": "secrets must be a JSON object"}, status_code=400)
            if not isinstance(parsed, dict):
                _safe_unlink(tmp_path)
                return JSONResponse({"error": "secrets must be a JSON object"}, status_code=400)
            secrets_map = {str(k): str(v) for k, v in parsed.items()}

        # --- Register agent + create config record ----------------------------
        import taosmd.agents as tm_agents
        try:
            tm_agents.register_agent(unique_slug)
        except tm_agents.AgentExistsError:
            pass
        except Exception as e:
            logger.exception("register_agent(%s) failed", unique_slug)
            _safe_unlink(tmp_path)
            return JSONResponse(
                {"error": f"Could not register agent with taosmd: {e}"}, status_code=500
            )

        new_agent = normalize_agent({
            "name": unique_slug,
            "display_name": display_name,
            "host": "",
            "status": "importing",
            "model": model,
            "framework": SUPPORTED_IMPORT_FRAMEWORK,
        })
        new_agent["migrated_to_v2_personas"] = True
        if source_host:
            new_agent["import_source_host"] = source_host
        config.agents.append(new_agent)
        await save_config_locked(config, config.config_path)

        # --- Grant user-supplied secrets to the agent (same path as deploy) ---
        # deploy_agent reads these via secrets_store.get_agent_secrets(name) and
        # injects them into the container env, so this is the same mechanism.
        secrets_store = getattr(request.app.state, "secrets", None)
        if secrets_map and secrets_store is not None:
            for sname, sval in secrets_map.items():
                try:
                    await secrets_store.add(
                        name=f"{unique_slug}-{sname}",
                        value=sval,
                        category="agent-import",
                        description=f"Imported secret {sname} for {unique_slug}",
                        agents=[unique_slug],
                    )
                except Exception:
                    logger.exception("import %s: storing secret %r failed", unique_slug, sname)

        deploy_tasks = request.app.state.deploy_tasks
        deploy_tasks[unique_slug] = {
            "status": "importing",
            "name": unique_slug,
            "pause_jobs": pause_jobs,
            "dropped_files": dropped_files,
        }

        data_dir = request.app.state.data_dir
        llm_proxy = getattr(request.app.state, "llm_proxy", None)

        # Stash the host bundle path so the background task can push it.
        _BUNDLE_PATHS[unique_slug] = tmp_path

        async def _background_import():
            from tinyagentos.deployer import deploy_agent, DeployRequest
            try:
                result = await deploy_agent(DeployRequest(
                    name=unique_slug,
                    framework=SUPPORTED_IMPORT_FRAMEWORK,
                    model=model,
                    data_dir=data_dir,
                    extra_config={
                        "llm_proxy": llm_proxy,
                        "registry": request.app.state.registry,
                        "llm_gateway_port": llm_gateway_live_port(request.app.state),
                        "agent_controller_port": agent_controller_live_port(request.app.state),
                        "llm_gateway_models_problem": llm_gateway_models_check(request.app.state),
                    },
                    secrets_store=secrets_store,
                ))
                agent = find_agent(config, unique_slug)
                if not result.get("success"):
                    if agent is not None:
                        agent["status"] = "failed"
                        agent["failure_reason"] = result.get("error", "unknown error")
                    err_msg = result.get("error", "unknown error")
                    deploy_tasks[unique_slug] = {
                        "status": "failed",
                        "name": unique_slug,
                        "error": err_msg,
                        "dropped_files": dropped_files,
                    }
                    await _notify_failure(request, unique_slug, err_msg)
                    # Clean up agent record on deploy failure
                    if agent is not None:
                        config.agents.remove(agent)
                        await save_config_locked(config, config.config_path)
                    return

                if agent is not None:
                    agent["host"] = result.get("ip", "")
                    agent["llm_key"] = result.get("llm_key")
                    await save_config_locked(config, config.config_path)

                # Post-provision: push the bundle and run `hermes profile import`.
                jobs_paused = await _run_profile_import(request, unique_slug, agent, pause_jobs, dropped_files, dropped_names)

                if agent is not None:
                    agent["status"] = "running"
                deploy_tasks[unique_slug] = {
                    "status": "success",
                    "name": unique_slug,
                    "result": result,
                    "dropped_files": dropped_files,
                    "jobs_paused": jobs_paused,
                }
            except Exception as exc:  # noqa: BLE001
                import traceback
                logger.exception("import %s: background task failed: %s", unique_slug, exc)
                agent = find_agent(config, unique_slug)
                if agent is not None:
                    agent["status"] = "failed"
                    agent["failure_reason"] = str(exc)
                err_msg = str(exc)
                deploy_tasks[unique_slug] = {
                    "status": "failed",
                    "name": unique_slug,
                    "error": err_msg,
                    "dropped_files": dropped_files,
                }
                await _notify_failure(request, unique_slug, err_msg)
                # Keep agent record on import failure (marked as failed with reason)
                await save_config_locked(config, config.config_path)
            finally:
                _safe_unlink(_BUNDLE_PATHS.pop(unique_slug, None))
                await save_config_locked(config, config.config_path)

    finally:
        # Release the reserved slug once the entry is appended (or the
        # create path failed before config mutation) so a later
        # archive/restore or redeploy of the same name can reuse it.
        release_agent_slug(unique_slug)

    asyncio.create_task(_background_import())

    return JSONResponse(
        {"status": "importing", "name": unique_slug, "display_name": display_name},
        status_code=202,
    )


async def _run_profile_import(
    request: Request,
    slug: str,
    agent: dict | None,
    pause_jobs: bool,
    dropped_files: list[str],
    dropped_names: set[str],
) -> int:
    """Push the bundle into the container and run `hermes profile import`,
    then configure gateway to serve the imported profile, rewrite model config,
    pause jobs if requested, write transfer notice, and read back persona.
    Raises on import failure so the background task records it.
    Returns the number of jobs paused.
    """
    import asyncio
    from tinyagentos.containers import exec_in_container, push_file

    container_name = f"taos-agent-{slug}"
    host_bundle = _BUNDLE_PATHS.get(slug)
    if not host_bundle:
        raise RuntimeError("internal error: bundle path missing for import")

    # Rewrite the bundle to strip dropped members (symlinks, devices, etc.)
    rewritten_bundle = _rewrite_bundle_stripping_dropped(host_bundle, dropped_names)
    try:
        push_rc, push_out = await push_file(container_name, rewritten_bundle, _CONTAINER_BUNDLE_PATH)
    finally:
        _safe_unlink(rewritten_bundle)
    if push_rc != 0:
        raise RuntimeError(f"failed to push bundle into container (rc={push_rc}): {push_out[-300:]}")

    hermes_bin = await _find_hermes_bin(container_name)
    # Import under a unique per-agent profile name (the slug), never the bundle's
    # own name. A profile exported from a user's main agent is named `default`,
    # and `hermes profile import` REFUSES to import as `default` because that is
    # the built-in root profile (~/.hermes): it errors with "Specify a different
    # name". Passing --name <slug> both avoids that and prevents collisions with
    # any existing profile in the fresh container.
    code, output = await exec_in_container(
        container_name,
        [hermes_bin, "profile", "import", _CONTAINER_BUNDLE_PATH, "--name", slug],
        timeout=300,
    )
    if code != 0:
        raise RuntimeError(f"hermes profile import failed ({code}): {output[-1000:]}")

    # Make the imported profile the sticky default so the agent runs it rather
    # than the empty built-in `default`. The import never overwrites `default`,
    # so without this the agent would deploy with none of the restored persona.
    use_code, use_out = await exec_in_container(
        container_name,
        [hermes_bin, "profile", "use", slug],
        timeout=60,
    )
    if use_code != 0:
        raise RuntimeError(f"hermes profile use failed ({use_code}): {use_out[-500:]}")

    # Rewrite the profile's model/provider config to the taOS gateway FIRST,
    # before restarting the gateway. This ensures the gateway starts with the
    # taOS gateway config, not the bundle's original provider.
    # Also set gateway.standalone: true (required by Hermes 0.23 for profiles/
    # profiles) and disable the gadget platform/plugin if present.
    await _rewrite_model_config(container_name, slug, agent)

    # Create systemd drop-in to set HERMES_HOME for the gateway service,
    # then restart the gateway so it serves the imported profile.
    profile_home = f"/root/.hermes/profiles/{slug}"
    dropin_content = f"""[Service]
Environment=HERMES_HOME={profile_home}
"""
    dropin_path = "/etc/systemd/system/hermes-gateway.service.d/99-taos-import.conf"
    # Write drop-in using push_file (no shell heredoc with untrusted content)
    dropin_fd, dropin_tmp = tempfile.mkstemp(prefix="taos-dropin-", suffix=".conf")
    try:
        with os.fdopen(dropin_fd, "w") as f:
            f.write(dropin_content)
        push_rc, push_out = await push_file(container_name, dropin_tmp, dropin_path)
    finally:
        _safe_unlink(dropin_tmp)
    if push_rc != 0:
        raise RuntimeError(f"failed to create systemd drop-in (rc={push_rc}): {push_out[-300:]}")

    # Reload systemd and restart the gateway
    reload_code, reload_out = await exec_in_container(
        container_name, ["systemctl", "daemon-reload"], timeout=30
    )
    if reload_code != 0:
        raise RuntimeError(f"systemctl daemon-reload failed ({reload_code}): {reload_out[-300:]}")

    restart_code, restart_out = await exec_in_container(
        container_name, ["systemctl", "restart", "hermes-gateway"], timeout=120
    )
    if restart_code != 0:
        raise RuntimeError(f"hermes gateway restart failed ({restart_code}): {restart_out[-500:]}")

    # Wait for the gateway unit to be active
    for _ in range(30):
        await asyncio.sleep(1)
        is_active_code, is_active_out = await exec_in_container(
            container_name, ["systemctl", "is-active", "hermes-gateway"], timeout=10
        )
        if is_active_code == 0 and is_active_out.strip() == "active":
            break
    else:
        raise RuntimeError("hermes-gateway did not become active after restart")

    # Verify the gateway process environment has HERMES_HOME=<profile dir>
    # by checking the systemd unit's Environment property
    env_code, env_out = await exec_in_container(
        container_name, ["systemctl", "show", "hermes-gateway", "-p", "Environment"], timeout=10
    )
    if env_code != 0:
        raise RuntimeError(f"failed to read gateway environment: {env_out[-300:]}")
    if f"HERMES_HOME={profile_home}" not in env_out:
        raise RuntimeError(
            f"gateway environment does not contain HERMES_HOME={profile_home}: {env_out.strip()}"
        )

    # Pause jobs if requested
    jobs_paused = 0
    if pause_jobs:
        jobs_paused = await _pause_imported_jobs(container_name, slug)

    # Write transfer notice to agent memory and send as first chat message
    await _write_transfer_notice(request, container_name, slug, dropped_files, pause_jobs, agent)

    # Create DM channel for the agent so it appears in Messages
    await _ensure_dm_channel(request, slug, agent)

    # Best-effort persona readback so the imported personality shows in taOS.
    if agent is not None:
        await _readback_persona(container_name, agent)

    return jobs_paused


async def _rewrite_model_config(container_name: str, slug: str, agent: dict | None) -> None:
    """Rewrite the profile's model/provider config to the taOS gateway.
    Backup the original config to config.yaml.pre-import atomically.
    Parse YAML and replace ONLY the model/provider keys, keeping everything else.
    Also set gateway.standalone: true (required by Hermes 0.23 for profiles/
    profiles) and disable the gadget platform/plugin if present.
    A failed backup must FAIL the import.
    """
    from tinyagentos.containers import exec_in_container, push_file
    import yaml
    import tempfile

    config_path = f"/root/.hermes/profiles/{slug}/config.yaml"
    backup_path = f"{config_path}.pre-import"

    code, current_config = await exec_in_container(
        container_name, ["cat", config_path], timeout=30
    )
    if code != 0:
        # Config might not exist yet (fresh profile), skip rewrite
        logger.warning("import %s: config.yaml not found at %s, skipping model rewrite", slug, config_path)
        return

    # Backup original config atomically
    backup_code, backup_out = await exec_in_container(
        container_name, ["cp", config_path, backup_path], timeout=10
    )
    if backup_code != 0:
        raise RuntimeError(f"failed to backup config to {backup_path}: {backup_out[-200:]}")

    # Parse the existing YAML
    try:
        data = yaml.safe_load(current_config) or {}
    except yaml.YAMLError as e:
        raise RuntimeError(f"failed to parse config.yaml: {e}")

    # Get the taOS gateway config from the agent record or environment
    llm_key = agent.get("llm_key", "") if agent else ""
    model = agent.get("model", "kilo-auto/free") if agent else "kilo-auto/free"

    # Replace ONLY the model/provider keys, keep everything else
    m = data.setdefault("model", {})
    m["default"] = model
    m["provider"] = "custom"
    m["base_url"] = "http://127.0.0.1:4000/v1"
    m["api_key"] = llm_key

    # Hermes 0.23 requires gateway.standalone: true for profiles/ profiles
    gw = data.setdefault("gateway", {})
    gw["standalone"] = True

    # Disable the gadget platform/plugin if present (needs host device not in container)
    # Check both "platforms" and "plugins" keys as Hermes may use either
    for key in ("platforms", "plugins"):
        if key in data and isinstance(data[key], dict):
            gadget = data[key].get("gadget")
            if isinstance(gadget, dict):
                gadget["enabled"] = False
                logger.info("import %s: disabled gadget %s", slug, key)

    # Write the new config atomically via push_file (no shell heredoc)
    new_config_yaml = yaml.safe_dump(data, default_flow_style=False, sort_keys=False)
    config_fd, config_tmp = tempfile.mkstemp(prefix="taos-config-", suffix=".yaml")
    try:
        with os.fdopen(config_fd, "w") as f:
            f.write(new_config_yaml)
        push_rc, push_out = await push_file(container_name, config_tmp, config_path)
    finally:
        _safe_unlink(config_tmp)
    if push_rc != 0:
        raise RuntimeError(f"failed to rewrite config.yaml (rc={push_rc}): {push_out[-300:]}")


async def _pause_imported_jobs(container_name: str, slug: str) -> int:
    """Pause imported jobs by setting enabled=false and state=paused in jobs.json.
    Returns the number of jobs paused.
    The jobs.json schema matches the Hermes source pinned at
    scripts/install_hermes.sh (HERMES_TAG=v2026.8.31). Hermes v0.23.2 keeps
    jobs.json at <profile>/cron/jobs.json (cron/jobs.py JOBS_FILE).
    """
    from tinyagentos.containers import exec_in_container, push_file
    import tempfile

    jobs_path = f"/root/.hermes/profiles/{slug}/cron/jobs.json"
    code, jobs_content = await exec_in_container(
        container_name, ["cat", jobs_path], timeout=30
    )
    if code != 0:
        # No jobs.json, nothing to pause
        return 0

    try:
        jobs_data = json.loads(jobs_content)
    except json.JSONDecodeError:
        # File exists but cannot be parsed — fail the import
        raise RuntimeError(f"import {slug}: failed to parse jobs.json at {jobs_path}")

    jobs = jobs_data.get("jobs", [])
    paused_count = 0
    for job in jobs:
        if job.get("enabled", False):
            job["enabled"] = False
            job["state"] = "paused"
            paused_count += 1

    # Write back the modified jobs.json atomically via push_file
    new_jobs_content = json.dumps(jobs_data, indent=2)
    jobs_fd, jobs_tmp = tempfile.mkstemp(prefix="taos-jobs-", suffix=".json")
    try:
        with os.fdopen(jobs_fd, "w") as f:
            f.write(new_jobs_content)
        write_code, write_out = await push_file(container_name, jobs_tmp, jobs_path)
    finally:
        _safe_unlink(jobs_tmp)
    if write_code != 0:
        raise RuntimeError(f"failed to write paused jobs.json (rc={write_code}): {write_out[-300:]}")

    return paused_count


async def _write_transfer_notice(
    request: Request,
    container_name: str,
    slug: str,
    dropped_files: list[str],
    pause_jobs: bool,
    agent: dict | None,
) -> None:
    """Write a transfer notice into the agent's memory (memories/MEMORY.md)
    AND deliver it as the first chat message via the controller's chat store
    on the agent's DM channel.
    """
    from tinyagentos.containers import exec_in_container, push_file
    import tempfile

    # Read jobs for the notice (Hermes v0.23.2: cron/jobs.json)
    jobs_path = f"/root/.hermes/profiles/{slug}/cron/jobs.json"
    code, jobs_content = await exec_in_container(
        container_name, ["cat", jobs_path], timeout=30
    )
    jobs_list = []
    if code == 0:
        try:
            jobs_data = json.loads(jobs_content)
            for job in jobs_data.get("jobs", []):
                status = "paused" if job.get("state") == "paused" else ("enabled" if job.get("enabled") else "disabled")
                jobs_list.append(f"- {job.get('name', 'unnamed')} ({status})")
        except json.JSONDecodeError:
            jobs_list.append("- (could not parse jobs.json)")

    # Read original model config from backup for the notice
    backup_path = f"/root/.hermes/profiles/{slug}/config.yaml.pre-import"
    code, original_config = await exec_in_container(
        container_name, ["cat", backup_path], timeout=10
    )
    original_model_info = "unknown"
    if code == 0:
        for line in original_config.splitlines():
            if "base_url:" in line:
                original_model_info = line.strip()
                break

    # Source host name: from bundle metadata if present, else optional source_host form field, else fallback
    source_host = "another machine"
    if agent:
        source_host = agent.get("import_source_host", "another machine")

    # Build the transfer notice (full version for chat)
    notice = f"""## Transfer Notice

You have been moved to a new environment managed by taOS.

**What moved:** Your Hermes profile (personality, skills, memory, scheduled jobs, and configuration)
**When:** {datetime.now(UTC).isoformat().replace('+00:00', 'Z')}
**Source:** {source_host}
**New home:** /root/.hermes/profiles/{slug} (inside container taos-agent-{slug})

### Important Changes

1. **Host paths are no longer accessible** -- Any file paths, directories, or mounts from your previous machine are NOT available unless explicitly configured by the taOS administrator.

2. **Localhost services and ports are NOT reachable** -- Services that ran on 127.0.0.1 or localhost on your old machine (databases, APIs, local LLMs, etc.) are no longer accessible. The only localhost service available is the taOS LLM gateway at http://127.0.0.1:4000/v1.

3. **Your model provider has been redirected** -- Your previous model configuration ({original_model_info}) has been backed up to config.yaml.pre-import and replaced with the taOS gateway. All LLM requests now route through the taOS gateway.

4. **Scheduled jobs:** The following jobs were imported. {'They have been PAUSED for your review.' if pause_jobs else 'They remain in their original enabled/disabled state.'}
{chr(10).join(jobs_list) if jobs_list else "  (no jobs found)"}

5. **Credentials NOT imported** -- The following were NOT transferred and must be re-supplied:
   - API keys (OpenRouter, Anthropic, OpenAI, etc.)
   - Database passwords
   - SSH keys
   - Any secrets stored in .env or environment variables
   Please ask your taOS administrator to add required secrets via the agent secrets UI.

6. **Files dropped during import** -- The following archive members were not restored (symlinks, devices, FIFOs, and other non-regular files):
{chr(10).join(f"  - {f}" for f in dropped_files) if dropped_files else "  (none)"}

### How to Get Access

- Ask your taOS administrator to grant secrets via the agent settings.
- Request additional host paths or network access through the taOS admin interface.
- Use the `taos` CLI inside the container to inspect your environment: `taos --help`

Welcome to your new home on taOS!
"""

    # 1. APPEND short form to the profile's memories/MEMORY.md (do not replace)
    memory_path = f"/root/.hermes/profiles/{slug}/memories/MEMORY.md"
    short_notice = f"\n\n---\n\n### Transfer Notice ({datetime.now(UTC).isoformat().replace('+00:00', 'Z')})\n\nYou have been moved to taOS from {source_host}. Your profile, skills, memory, and jobs have been imported. Your model provider has been redirected to the taOS gateway. {'Jobs have been paused for review.' if pause_jobs else 'Jobs remain in their original state.'} Credentials were NOT imported -- request them from your administrator.\n"

    # Read existing memory file
    code, existing_memory = await exec_in_container(
        container_name, ["cat", memory_path], timeout=10
    )
    if code != 0:
        existing_memory = ""

    # Check memory limit from profile config
    memory_limit = DEFAULT_MEMORY_CHAR_LIMIT
    config_path = f"/root/.hermes/profiles/{slug}/config.yaml"
    code, config_content = await exec_in_container(
        container_name, ["cat", config_path], timeout=10
    )
    if code == 0:
        try:
            import yaml
            config_data = yaml.safe_load(config_content) or {}
            memory_cfg = config_data.get("memory", {})
            if isinstance(memory_cfg, dict) and "char_limit" in memory_cfg:
                memory_limit = int(memory_cfg["char_limit"])
        except Exception:
            pass

    new_memory = existing_memory + short_notice
    if len(new_memory) > memory_limit:
        # Raise the limit in the profile config by the notice length
        try:
            import yaml
            config_data = yaml.safe_load(config_content) or {}
            memory_cfg = config_data.setdefault("memory", {})
            new_limit = len(new_memory) + 100
            memory_cfg["char_limit"] = new_limit
            new_config_yaml = yaml.safe_dump(config_data, default_flow_style=False, sort_keys=False)
            config_fd, config_tmp = tempfile.mkstemp(prefix="taos-config-", suffix=".yaml")
            try:
                with os.fdopen(config_fd, "w") as f:
                    f.write(new_config_yaml)
                push_rc, push_out = await push_file(container_name, config_tmp, config_path)
            finally:
                _safe_unlink(config_tmp)
            if push_rc != 0:
                logger.warning("import %s: failed to raise memory limit (rc=%s): %s", slug, push_rc, push_out[-200:])
            else:
                logger.info("import %s: raised memory char_limit from %d to %d", slug, memory_limit, new_limit)
        except Exception as e:
            logger.warning("import %s: failed to update memory limit: %s", slug, e)

    # Write the appended memory
    memory_fd, memory_tmp = tempfile.mkstemp(prefix="taos-memory-", suffix=".md")
    try:
        with os.fdopen(memory_fd, "w") as f:
            f.write(new_memory)
        push_rc, push_out = await push_file(container_name, memory_tmp, memory_path)
    finally:
        _safe_unlink(memory_tmp)
    if push_rc != 0:
        logger.warning("import %s: failed to append to MEMORY.md (rc=%s): %s", slug, push_rc, push_out[-200:])

    # 2. Deliver the full notice as a chat message from the owner to the agent
    # on the agent's DM channel (same way POST /api/chat/messages does)
    await _send_notice_as_chat_message(request, slug, notice, agent)


async def _send_notice_as_chat_message(
    request: Request,
    slug: str,
    notice: str,
    agent: dict | None,
) -> None:
    """Deliver the transfer notice as a chat message from the owner to the agent
    on the agent's DM channel. Uses the same chat store as POST /api/chat/messages.
    """
    chat_channels = getattr(request.app.state, "chat_channels", None)
    chat_messages = getattr(request.app.state, "chat_messages", None)
    if not chat_channels or not chat_messages:
        logger.warning("import %s: chat stores not available, skipping notice delivery", slug)
        return

    # Get or create the DM channel for this agent
    channel_id = agent.get("chat_channel_id") if agent else None
    if not channel_id:
        # Find existing DM channel between user and agent
        channels = await chat_channels.list_channels(member_id="user")
        for ch in channels:
            if ch.get("type") == "dm" and slug in ch.get("members", []):
                channel_id = ch["id"]
                break

    if not channel_id:
        logger.warning("import %s: no DM channel found, skipping notice delivery", slug)
        return

    try:
        await chat_messages.send_message(
            channel_id=channel_id,
            author_id="user",
            author_type="user",
            content=notice,
            content_type="text",
        )
        logger.info("import %s: transfer notice delivered as chat message", slug)
    except Exception as e:
        logger.exception("import %s: failed to send notice as chat message: %s", slug, e)


async def _ensure_dm_channel(request: Request, slug: str, agent: dict | None) -> None:
    """Create a 1:1 DM channel for the agent if it doesn't exist, and set
    chat_channel_id on the agent record. Mirrors the logic in the deploy path.
    """
    chat_channels = getattr(request.app.state, "chat_channels", None)
    if not chat_channels:
        return

    if agent and agent.get("chat_channel_id"):
        return

    try:
        display_name = agent.get("display_name", slug) if agent else slug
        channel = await chat_channels.create_channel(
            name=display_name,
            type="dm",
            created_by="user",
            members=["user", slug],
            description=f"Direct messages with {display_name}",
        )
        if channel and channel.get("id") and agent is not None:
            agent["chat_channel_id"] = channel["id"]
            logger.info("import %s: created DM channel %s", slug, channel["id"])
    except Exception as e:
        logger.warning("import %s: DM channel creation failed: %s", slug, e)


async def _find_hermes_bin(container_name: str) -> str:
    """Probe for the hermes binary inside the container, matching the
    candidate list in install_hermes.sh. Falls back to ``hermes`` on PATH.
    """
    from tinyagentos.containers import exec_in_container

    for cand in _HERMES_BIN_CANDIDATES:
        try:
            code, _ = await exec_in_container(container_name, ["test", "-x", cand])
        except Exception:
            continue
        if code == 0:
            return cand
    return "hermes"


async def _readback_persona(container_name: str, agent: dict) -> None:
    """Best-effort: cat SOUL.md / config.yaml from the container to populate
    the agent record's persona and model where empty. Never raises.
    """
    from tinyagentos.containers import exec_in_container

    try:
        if not agent.get("soul_md"):
            for path in (f"/root/.hermes/profiles/{agent['name']}/SOUL.md", "/root/.hermes/SOUL.md", "/root/.hermes/profile/SOUL.md"):
                code, out = await exec_in_container(container_name, ["cat", path])
                if code == 0 and out.strip():
                    agent["soul_md"] = out
                    break
    except Exception:
        logger.exception("import readback SOUL.md failed for %s", container_name)

    try:
        if not agent.get("model"):
            code, out = await exec_in_container(
                container_name, ["cat", f"/root/.hermes/profiles/{agent['name']}/config.yaml"]
            )
            if code == 0 and out.strip():
                model = _parse_model_from_config(out)
                if model:
                    agent["model"] = model
    except Exception:
        logger.exception("import readback config.yaml failed for %s", container_name)


def _parse_model_from_config(text: str) -> str | None:
    """Pull a model identifier out of a Hermes config.yaml without depending
    on the exact schema. Looks for a ``default_model:`` or ``model:`` key.
    """
    import yaml
    try:
        data = yaml.safe_load(text)
        if isinstance(data, dict):
            model = data.get("model", {})
            if isinstance(model, dict):
                return model.get("default")
    except Exception:
        pass
    # Fallback to line parsing
    for line in text.splitlines():
        stripped = line.strip()
        for key in ("default_model:", "model:"):
            if stripped.startswith(key):
                value = stripped[len(key):].strip().strip('"').strip("'")
                if value:
                    return value
    return None


async def _notify_failure(request: Request, slug: str, err_msg: str) -> None:
    notif = getattr(request.app.state, "notifications", None)
    if notif:
        try:
            await notif.add(
                title=f"Import failed: {slug}",
                message=f"Import failed for {slug}: {err_msg}",
                level="error",
                source="agents.import",
            )
        except Exception:
            logger.exception("import failure notification failed for %s", slug)


def _safe_unlink(path: str | None) -> None:
    if not path:
        return
    try:
        os.unlink(path)
    except OSError:
        pass
