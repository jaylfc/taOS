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
import tempfile

from fastapi import Request, UploadFile
from fastapi.responses import JSONResponse

from tinyagentos.agent_db import find_agent
from tinyagentos.config import (
    normalize_agent,
    save_config_locked,
    unique_agent_slug,
    validate_agent_name,
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


async def import_hermes_agent(
    request: Request,
    framework: str,
    bundle: UploadFile,
    name: str,
    model: str | None,
    secrets: str | None,
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

    # --- Name validation + slug ------------------------------------------
    display_name = (name or "").strip()
    name_error = validate_agent_name(display_name)
    if name_error:
        _safe_unlink(tmp_path)
        return JSONResponse({"error": name_error}, status_code=400)

    config = request.app.state.config
    try:
        unique_slug = unique_agent_slug(config, display_name)
    except ValueError as e:
        _safe_unlink(tmp_path)
        return JSONResponse({"error": str(e)}, status_code=400)

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
    deploy_tasks[unique_slug] = {"status": "importing", "name": unique_slug}

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
                },
                secrets_store=secrets_store,
            ))
            agent = find_agent(config, unique_slug)
            if not result.get("success"):
                if agent is not None:
                    agent["status"] = "failed"
                err_msg = result.get("error", "unknown error")
                deploy_tasks[unique_slug] = {
                    "status": "failed", "name": unique_slug, "error": err_msg,
                }
                await _notify_failure(request, unique_slug, err_msg)
                return

            if agent is not None:
                agent["host"] = result.get("ip", "")
                agent["llm_key"] = result.get("llm_key")
                await save_config_locked(config, config.config_path)

            # Post-provision: push the bundle and run `hermes profile import`.
            await _run_profile_import(request, unique_slug, agent)

            if agent is not None:
                agent["status"] = "running"
            deploy_tasks[unique_slug] = {
                "status": "success", "name": unique_slug, "result": result,
            }
        except Exception as exc:  # noqa: BLE001
            agent = find_agent(config, unique_slug)
            if agent is not None:
                agent["status"] = "failed"
            err_msg = str(exc)
            deploy_tasks[unique_slug] = {
                "status": "failed", "name": unique_slug, "error": err_msg,
            }
            await _notify_failure(request, unique_slug, err_msg)
        finally:
            _safe_unlink(_BUNDLE_PATHS.pop(unique_slug, None))
            await save_config_locked(config, config.config_path)

    asyncio.create_task(_background_import())

    return JSONResponse(
        {"status": "importing", "name": unique_slug, "display_name": display_name},
        status_code=202,
    )


async def _run_profile_import(request: Request, slug: str, agent: dict | None) -> None:
    """Push the bundle into the container and run `hermes profile import`,
    then best-effort read back persona. Raises on import failure so the
    background task records it; readback failures are swallowed.
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
    code, output = await exec_in_container(
        container_name,
        [hermes_bin, "profile", "import", _CONTAINER_BUNDLE_PATH],
        timeout=300,
    )
    if code != 0:
        raise RuntimeError(f"hermes profile import failed ({code}): {output[-1000:]}")

    # Best-effort persona readback so the imported personality shows in taOS.
    if agent is not None:
        await _readback_persona(container_name, agent)


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
            for path in ("/root/.hermes/SOUL.md", "/root/.hermes/profile/SOUL.md"):
                code, out = await exec_in_container(container_name, ["cat", path])
                if code == 0 and out.strip():
                    agent["soul_md"] = out
                    break
    except Exception:
        logger.exception("import readback SOUL.md failed for %s", container_name)

    try:
        if not agent.get("model"):
            code, out = await exec_in_container(
                container_name, ["cat", "/root/.hermes/config.yaml"]
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
