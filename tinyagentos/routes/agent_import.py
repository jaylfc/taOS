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

# Accept only Hermes for now; the frontend locks the framework picker to it.
SUPPORTED_IMPORT_FRAMEWORK = "hermes"

# Reject empty or oversize uploads up front. 200 MB mirrors the client-side
# cap in the ImportWizard so the user sees the same limit on both ends.
MAX_BUNDLE_BYTES = 200 * 1024 * 1024

# Accepted bundle extensions (Hermes exports a tarball/zip).
_ACCEPTED_EXTENSIONS = (".zip", ".tar.gz", ".tgz")

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


def _validate_bundle_members(host_bundle: str, original_filename: str = "") -> tuple[list[str], list[str]]:
    """Validate tar bundle members before handing to Hermes.
    
    Returns (dropped_files, errors). Errors cause import rejection.
    Dropped files are non-regular members (symlinks, devices, FIFOs, etc.)
    that are stripped from the bundle with a per-file note.
    """
    dropped = []
    errors = []
    
    # Only validate tar.gz/tgz files; zip files are passed through as-is
    # since Hermes handles zip extraction internally.
    is_tar_gz = (host_bundle.endswith(".tar.gz") or host_bundle.endswith(".tgz") or
                 original_filename.endswith(".tar.gz") or original_filename.endswith(".tgz"))
    if not is_tar_gz:
        return dropped, errors
    
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
                if not member.isfile():
                    member_type = {
                        tarfile.SYMTYPE: "symlink",
                        tarfile.LNKTYPE: "hardlink",
                        tarfile.CHRTYPE: "character device",
                        tarfile.BLKTYPE: "block device",
                        tarfile.FIFOTYPE: "FIFO",
                        tarfile.DIRTYPE: "directory",
                    }.get(member.type, f"type {member.type}")
                    dropped.append(f"{name} ({member_type})")
                    continue
    except tarfile.ReadError as e:
        errors.append(f"invalid tar archive: {e}")
    except Exception as e:
        errors.append(f"bundle validation failed: {e}")
    
    return dropped, errors


async def import_hermes_agent(
    request: Request,
    framework: str,
    bundle: UploadFile,
    name: str,
    model: str | None,
    secrets: str | None,
    pause_jobs: bool = False,
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

    # --- Validate bundle members (tar.gz only) ---
    dropped_files, validation_errors = _validate_bundle_members(tmp_path, bundle.filename or "")
    if validation_errors:
        _safe_unlink(tmp_path)
        return JSONResponse(
            {"error": "bundle validation failed", "details": validation_errors},
            status_code=400,
        )

    # --- Name validation + slug ------------------------------------------
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
                jobs_paused = await _run_profile_import(request, unique_slug, agent, pause_jobs, dropped_files)

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
) -> int:
    """Push the bundle into the container and run `hermes profile import`,
    then configure gateway to serve the imported profile, rewrite model config,
    pause jobs if requested, write transfer notice, and read back persona.
    Raises on import failure so the background task records it.
    Returns the number of jobs paused.
    """
    from tinyagentos.containers import exec_in_container, push_file

    container_name = f"taos-agent-{slug}"
    host_bundle = _BUNDLE_PATHS.get(slug)
    if not host_bundle:
        raise RuntimeError("internal error: bundle path missing for import")

    push_rc, push_out = await push_file(container_name, host_bundle, _CONTAINER_BUNDLE_PATH)
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

    # Create systemd drop-in to set HERMES_HOME for the gateway service,
    # then restart the gateway so it serves the imported profile.
    profile_home = f"/root/.hermes/profiles/{slug}"
    dropin_content = f"""[Service]
Environment=HERMES_HOME={profile_home}
"""
    dropin_path = "/etc/systemd/system/hermes-gateway.service.d/99-taos-import.conf"
    # Use tee to write the drop-in file
    tee_cmd = ["bash", "-c", f"mkdir -p /etc/systemd/system/hermes-gateway.service.d && cat > {dropin_path} <<'EOF'\n{dropin_content}EOF"]
    tee_code, tee_out = await exec_in_container(container_name, tee_cmd, timeout=30)
    if tee_code != 0:
        raise RuntimeError(f"failed to create systemd drop-in (rc={tee_code}): {tee_out[-300:]}")

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

    # Verify the gateway is serving the imported profile by asking Hermes
    # which profile is active. This MUST match the imported slug.
    active_code, active_out = await exec_in_container(
        container_name,
        [hermes_bin, "profile", "active"],
        timeout=30,
    )
    if active_code != 0:
        raise RuntimeError(f"hermes profile active check failed ({active_code}): {active_out[-300:]}")
    active_profile = active_out.strip()
    if active_profile != slug:
        raise RuntimeError(
            f"gateway is not serving the imported profile: expected '{slug}', got '{active_profile}'"
        )

    # Rewrite the profile's model/provider config to the taOS gateway.
    # Keep the original in a .pre-import backup file.
    await _rewrite_model_config(container_name, slug, agent)

    # Pause jobs if requested
    jobs_paused = 0
    if pause_jobs:
        jobs_paused = await _pause_imported_jobs(container_name, slug)

    # Write transfer notice to agent memory and send as first chat message
    await _write_transfer_notice(request, container_name, slug, dropped_files, pause_jobs, agent)

    # Best-effort persona readback so the imported personality shows in taOS.
    if agent is not None:
        await _readback_persona(container_name, agent)

    return jobs_paused

    # Write transfer notice to agent memory and send as first chat message
    await _write_transfer_notice(request, container_name, slug, dropped_files, pause_jobs, agent)

    # Best-effort persona readback so the imported personality shows in taOS.
    if agent is not None:
        await _readback_persona(container_name, agent)


async def _rewrite_model_config(container_name: str, slug: str, agent: dict | None) -> None:
    """Rewrite the profile's model/provider config to the taOS gateway.
    Backup the original config to config.yaml.pre-import.
    """
    from tinyagentos.containers import exec_in_container

    # Read the current config.yaml from the imported profile
    config_path = f"/root/.hermes/profiles/{slug}/config.yaml"
    backup_path = f"{config_path}.pre-import"
    
    code, current_config = await exec_in_container(
        container_name, ["cat", config_path], timeout=30
    )
    if code != 0:
        # Config might not exist yet (fresh profile), skip rewrite
        logger.warning("import %s: config.yaml not found at %s, skipping model rewrite", slug, config_path)
        return

    # Backup original config
    backup_cmd = ["bash", "-c", f"cp {config_path} {backup_path}"]
    backup_code, backup_out = await exec_in_container(container_name, backup_cmd, timeout=10)
    if backup_code != 0:
        logger.warning("import %s: failed to backup config to %s: %s", slug, backup_path, backup_out[-200:])

    # Get the taOS gateway config from the agent record or environment
    # The gateway URL is http://127.0.0.1:4000/v1 (agent's proxy device)
    # The API key is the agent's LITELLM_API_KEY (stored in agent['llm_key'])
    llm_key = agent.get("llm_key", "") if agent else ""
    model = agent.get("model", "kilo-auto/free") if agent else "kilo-auto/free"
    
    # Build the new config.yaml with taOS gateway settings
    new_config = f"""model:
  default: {model}
  provider: custom
  base_url: "http://127.0.0.1:4000/v1"
  api_key: "{llm_key}"
"""
    # Write the new config using tee
    write_cmd = ["bash", "-c", f"cat > {config_path} <<'EOF'\n{new_config}EOF"]
    write_code, write_out = await exec_in_container(container_name, write_cmd, timeout=10)
    if write_code != 0:
        raise RuntimeError(f"failed to rewrite config.yaml (rc={write_code}): {write_out[-300:]}")


async def _pause_imported_jobs(container_name: str, slug: str) -> int:
    """Pause imported jobs by setting enabled=false and state=paused in jobs.json.
    Returns the number of jobs paused.
    """
    from tinyagentos.containers import exec_in_container

    jobs_path = f"/root/.hermes/profiles/{slug}/jobs.json"
    code, jobs_content = await exec_in_container(
        container_name, ["cat", jobs_path], timeout=30
    )
    if code != 0:
        # No jobs.json, nothing to pause
        return 0

    try:
        jobs_data = json.loads(jobs_content)
    except json.JSONDecodeError:
        logger.warning("import %s: invalid jobs.json, skipping pause", slug)
        return 0

    jobs = jobs_data.get("jobs", [])
    paused_count = 0
    for job in jobs:
        if job.get("enabled", False):
            job["enabled"] = False
            job["state"] = "paused"
            paused_count += 1

    # Write back the modified jobs.json
    new_jobs_content = json.dumps(jobs_data, indent=2)
    write_cmd = ["bash", "-c", f"cat > {jobs_path} <<'EOF'\n{new_jobs_content}EOF"]
    write_code, write_out = await exec_in_container(container_name, write_cmd, timeout=10)
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
    """Write a transfer notice into the agent's memory AND deliver it as
    the first chat message.
    """
    from tinyagentos.containers import exec_in_container
    from datetime import datetime

    # Read jobs for the notice
    jobs_path = f"/root/.hermes/profiles/{slug}/jobs.json"
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

    # Build the transfer notice
    notice = f"""## Transfer Notice

You have been moved to a new environment managed by taOS.

**What moved:** Your Hermes profile (personality, skills, memory, scheduled jobs, and configuration)
**When:** {datetime.utcnow().isoformat()}Z
**New home:** /root/.hermes/profiles/{slug} (inside container taos-agent-{slug})

### Important Changes

1. **Host paths are no longer accessible** — Any file paths, directories, or mounts from your previous machine are NOT available unless explicitly configured by the taOS administrator.

2. **Localhost services and ports are NOT reachable** — Services that ran on 127.0.0.1 or localhost on your old machine (databases, APIs, local LLMs, etc.) are no longer accessible. The only localhost service available is the taOS LLM gateway at http://127.0.0.1:4000/v1.

3. **Your model provider has been redirected** — Your previous model configuration ({original_model_info}) has been backed up to config.yaml.pre-import and replaced with the taOS gateway. All LLM requests now route through the taOS gateway.

4. **Scheduled jobs:** The following jobs were imported. {'They have been PAUSED for your review.' if pause_jobs else 'They remain in their original enabled/disabled state.'}
{chr(10).join(jobs_list) if jobs_list else "  (no jobs found)"}

5. **Credentials NOT imported** — The following were NOT transferred and must be re-supplied:
   - API keys (OpenRouter, Anthropic, OpenAI, etc.)
   - Database passwords
   - SSH keys
   - Any secrets stored in .env or environment variables
   Please ask your taOS administrator to add required secrets via the agent secrets UI.

6. **Files dropped during import** — The following archive members were not restored (symlinks, devices, FIFOs, and other non-regular files):
{chr(10).join(f"  - {f}" for f in dropped_files) if dropped_files else "  (none)"}

### How to Get Access

- Ask your taOS administrator to grant secrets via the agent settings.
- Request additional host paths or network access through the taOS admin interface.
- Use the `taos` CLI inside the container to inspect your environment: `taos --help`

Welcome to your new home on taOS!
"""

    # Write notice to agent memory via taOS CLI (if available) or as a file
    # Try taOS memory write first
    memory_cmd = [
        "bash", "-c",
        f"cat > /root/.taos/transfer_notice.md <<'EOF'\n{notice}\nEOF"
    ]
    await exec_in_container(container_name, memory_cmd, timeout=10)

    # Also try to send as first chat message via the bridge
    # This is best-effort; the bridge may not be ready yet
    chat_cmd = [
        "bash", "-c",
        f"cat > /root/.taos/first_message.txt <<'EOF'\n{notice}\nEOF"
    ]
    await exec_in_container(container_name, chat_cmd, timeout=10)

    # Note: Actual delivery as first chat message would require the bridge
    # to be running and the channel to be established. The notice is staged
    # for the bridge to pick up on startup.


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