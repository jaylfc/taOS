"""Container management package.

Provides backward-compatible module-level async functions matching the
original tinyagentos/containers.py API, plus the new backend abstraction.
"""
from __future__ import annotations

import asyncio
import json
import logging
import re
import time

from .backend import ContainerInfo, _parse_memory, detect_runtime, get_backend, set_backend
from .lxc import LXCBackend
from .docker import DockerBackend
from .native import NativeBackend

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Backward-compatible module-level _run so existing tests that patch
# ``tinyagentos.containers._run`` continue to work.
# ---------------------------------------------------------------------------

async def _run(cmd: list[str], timeout: int = 120) -> tuple[int, str]:
    """Run a command and return (returncode, output)."""
    proc = await asyncio.create_subprocess_exec(
        *cmd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.STDOUT,
    )
    stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=timeout)
    return proc.returncode, stdout.decode() if stdout else ""


# ---------------------------------------------------------------------------
# Backward-compatible module-level async functions (same signatures as the
# original containers.py).  These call the module-level _run above so that
# ``patch("tinyagentos.containers._run")`` correctly intercepts them.
# ---------------------------------------------------------------------------

async def _resolve_container_project(name: str) -> str | None:
    """Return the incus project a container lives in, or None if not found.

    Searches ALL projects the client can see (``--all-projects``), not just the
    ambient default. Multi-user installs place agent containers in a restricted
    project (e.g. ``user-999``) while other agents sit in ``default``; a lookup
    scoped to the ambient project alone misses the others, which is how undeploy
    left orphaned containers behind. Errors return None (treated as "unknown").
    """
    code, output = await _run(["incus", "list", "--all-projects", "-f", "json"])
    if code != 0:
        return None
    try:
        instances = json.loads(output)
    except (json.JSONDecodeError, TypeError):
        return None
    for inst in instances:
        if isinstance(inst, dict) and inst.get("name") == name:
            return inst.get("project") or "default"
    return None


async def container_exists(name: str) -> bool:
    """Return True iff a container with the given name is known to the runtime.

    Searches across ALL projects (see ``_resolve_container_project``) so a
    container in a restricted project (e.g. ``user-999``) is not mistaken for an
    absent one. Errors (incus not installed, daemon down, malformed output) are
    treated as "unknown" and return False so callers can take the safer
    no-container path rather than blocking on cleanup of an orphan config row.
    """
    return (await _resolve_container_project(name)) is not None


# Prefix shared by every taOS-managed agent container. The current convention
# is ``taos-agent-<slug>``; the legacy convention was ``taos-<slug>``. Both
# share this prefix, which is how reconcile distinguishes taOS containers from
# anything else on the host.
TAOS_CONTAINER_PREFIX = "taos-"


def candidate_agent_container_names(slug: str, display_name: str | None = None) -> list[str]:
    """Return the container names an agent's slug could map to, newest
    convention first.

    Current deploys name containers ``taos-agent-<slug>``; legacy deploys used
    ``taos-<slug>``. When the agent record lost its ``container_name`` link the
    only way to find its container is to probe both conventions. A
    ``display_name`` (if it slugifies differently from ``slug``) contributes its
    own pair of candidates so a record whose slug drifted from the original
    display name still resolves.
    """
    from tinyagentos.config import slugify_agent_name

    slugs: list[str] = []
    for s in (slug, slugify_agent_name(display_name) if display_name else None):
        if s and s not in slugs:
            slugs.append(s)
    names: list[str] = []
    for s in slugs:
        for name in (f"taos-agent-{s}", f"taos-{s}"):
            if name not in names:
                names.append(name)
    return names


async def list_all_taos_containers() -> list[dict]:
    """Return every taOS-managed container across ALL incus projects.

    Each item is ``{"name": str, "project": str}``. Filters to names beginning
    with :data:`TAOS_CONTAINER_PREFIX` so non-taOS containers are never
    included. Errors (incus absent, daemon down, malformed output) return an
    empty list.
    """
    try:
        code, output = await _run(["incus", "list", "--all-projects", "-f", "json"])
    except (FileNotFoundError, OSError):
        # incus binary not present (dev hosts, CI). Treat as "no containers".
        return []
    if code != 0:
        return []
    try:
        instances = json.loads(output)
    except (json.JSONDecodeError, TypeError):
        return []
    results: list[dict] = []
    for inst in instances:
        if not isinstance(inst, dict):
            continue
        name = inst.get("name", "")
        if not name.startswith(TAOS_CONTAINER_PREFIX):
            continue
        results.append({"name": name, "project": inst.get("project") or "default"})
    return results


async def resolve_agent_container(slug: str, display_name: str | None = None) -> str | None:
    """Resolve the live container backing an agent slug, or None.

    Probes both the current (``taos-agent-<slug>``) and legacy (``taos-<slug>``)
    naming conventions, plus the display-name variants, across ALL incus
    projects. Returns the first matching container name. Used by delete to
    rescue a container whose agent record lost its ``container_name`` link
    rather than orphaning a still-running container.
    """
    candidates = candidate_agent_container_names(slug, display_name)
    existing = {c["name"] for c in await list_all_taos_containers()}
    for name in candidates:
        if name in existing:
            return name
    return None


async def list_containers(prefix: str = "taos-agent-") -> list[ContainerInfo]:
    """List all agent containers."""
    code, output = await _run(["incus", "list", "-f", "json"])
    if code != 0:
        logger.error(f"incus list failed: {output}")
        return []
    try:
        containers = json.loads(output)
    except json.JSONDecodeError:
        return []
    results = []
    for c in containers:
        name = c.get("name", "")
        if not name.startswith(prefix):
            continue
        status = c.get("status", "Unknown")
        ip = None
        network = c.get("state", {}).get("network", {})
        for iface in network.values():
            for addr in iface.get("addresses", []):
                if addr.get("family") == "inet" and addr.get("scope") == "global":
                    ip = addr.get("address")
                    break
            if ip:
                break
        config = c.get("config", {})
        mem_str = config.get("limits.memory", "0")
        memory_mb = _parse_memory(mem_str)
        cpu_str = config.get("limits.cpu", "0")
        cpu_cores = int(cpu_str) if cpu_str.isdigit() else 0
        results.append(ContainerInfo(
            name=name, status=status, ip=ip,
            memory_mb=memory_mb, cpu_cores=cpu_cores,
        ))
    return results


async def set_root_quota(name: str, size_gib: int) -> dict:
    """Set per-container rootfs quota. On btrfs-backed LXC pools, the
    quota is immediately enforced. On ZFS, same. On dir-backed, this
    is accounting-only (soft) because dir pools don't enforce. Docker
    requires a supported storage driver (btrfs, ZFS, devicemapper); on
    overlay2 the call is a no-op and logged.

    Returns a dict with ``success`` (bool) and ``note`` (str).
    """
    # Override the profile-inherited root disk device on this instance,
    # then set the size. `override` creates a per-instance copy of the
    # device if it doesn't already have one.
    code, output = await _run([
        "incus", "config", "device", "override", name, "root",
        f"size={size_gib}GiB",
    ])
    # If override fails because a per-instance root device already exists,
    # fall back to plain `set` which works in that case.
    if code != 0 and "already exists" in output.lower():
        code, output = await _run([
            "incus", "config", "device", "set", name, "root",
            f"size={size_gib}GiB",
        ])
    if code != 0:
        logger.warning("set_root_quota: incus config device override/set failed for %s: %s", name, output)
        return {"success": False, "note": output}
    return {"success": True, "note": f"root quota set to {size_gib} GiB"}


async def create_container(
    name: str,
    image: str = "images:debian/bookworm",
    memory_limit: str | None = None,
    cpu_limit: int | None = None,
    mounts: list[tuple[str, str]] | None = None,
    env: dict[str, str] | None = None,
    host_uid: int | None = None,
    root_size_gib: int | None = None,
    remote: str | None = None,
) -> dict:
    """Create and start a new LXC container with mounts and env injected.

    ``mounts`` is a list of ``(host_path, container_path)`` bind mounts
    attached as incus disk devices. ``env`` is a dict of environment
    variables set via ``incus config set environment.KEY VALUE``.

    ``host_uid``: when provided, apply ``raw.idmap`` so container root
    (uid 0) is remapped to this UID on the host.

    ``root_size_gib``: when provided, apply a rootfs disk quota via
    ``set_root_quota`` after launch. Enforced on btrfs/ZFS pools;
    accounting-only on dir-backed pools.

    ``remote``: when set (e.g. ``"fedora-worker"``), the container is created
    on that enrolled incus remote's nested incus instead of locally. The base
    image must already exist on the remote (prefetched there); both the image
    ref and the instance name are qualified with ``<remote>:``. Bind mounts are
    skipped for a remote create — the host paths do not exist on the worker;
    the agent reaches the controller over the network (Tailscale) instead.
    """
    import asyncio as _asyncio
    # Qualify image + instance name for the target remote. The prefetched base
    # alias lives on the remote, so the image ref is also remote-qualified.
    target = f"{remote}:{name}" if remote else name
    # A bare local alias (e.g. "taos-hermes-base", prefetched onto the remote)
    # is remote-qualified; a remote-image-server ref (e.g.
    # "images:debian/bookworm", the cold fallback) already names its own source
    # and must NOT be prefixed, or incus rejects "<remote>:images:debian/...".
    if remote and ":" not in image:
        image_ref = f"{remote}:{image}"
    else:
        image_ref = image

    code, output = await _run(
        ["incus", "launch", image_ref, target], timeout=300,
    )
    if code != 0:
        return {"success": False, "error": output}

    # raw.idmap maps container-root to a host UID so the trace bind-mount is
    # writable. It is meaningless for a remote create (the host_uid is the
    # CONTROLLER's, not the worker's, and the bind-mount is skipped anyway); a
    # cross-host UID with no subuid mapping on the worker stops the container
    # from starting. Skip it for remote.
    if host_uid is not None and not remote:
        await _run([
            "incus", "config", "set", target, "raw.idmap",
            f"both {host_uid} 0",
        ])
        await _run(["incus", "stop", target, "--force"])
        await _run(["incus", "start", target])
        await _asyncio.sleep(3)

    # Root quota — set before mounts/env so subsequent writes are subject to limit.
    if root_size_gib is not None:
        quota_result = await set_root_quota(target, root_size_gib)
        if not quota_result["success"]:
            logger.warning(
                "create_container: root quota not applied for %s: %s",
                target, quota_result.get("note", ""),
            )

    if memory_limit is not None:
        await _run(["incus", "config", "set", target, "limits.memory", memory_limit])
    if cpu_limit is not None:
        await _run(["incus", "config", "set", target, "limits.cpu", str(cpu_limit)])
    # Bind mounts map host paths into the container; they only make sense for a
    # local create. A remote worker has no access to the controller's filesystem.
    if remote and mounts:
        logger.info("create_container: skipping %d bind mount(s) for remote %s", len(mounts), remote)
    elif not remote:
        for idx, (host_path, container_path) in enumerate(mounts or []):
            device_name = f"taos-mount-{idx}"
            mcode, mout = await _run([
                "incus", "config", "device", "add", target, device_name, "disk",
                f"source={host_path}", f"path={container_path}",
            ])
            if mcode != 0:
                logger.error(f"incus mount {host_path}->{container_path} failed: {mout}")
    for key, value in (env or {}).items():
        ecode, eout = await _run([
            "incus", "config", "set", target, f"environment.{key}={value}",
        ])
        if ecode != 0:
            logger.error(f"incus env set {key} failed: {eout}")
    return {"success": True, "name": name, "remote": remote}


async def exec_in_container(name: str, cmd: list[str], timeout: int = 300) -> tuple[int, str]:
    """Execute a command inside a container."""
    return await _run(["incus", "exec", name, "--"] + cmd, timeout=timeout)


async def push_file(name: str, local_path: str, remote_path: str) -> tuple[int, str]:
    """Push a file into a container."""
    return await _run(["incus", "file", "push", local_path, f"{name}{remote_path}"])


async def start_container(name: str) -> dict:
    code, output = await _run(["incus", "start", name])
    return {"success": code == 0, "output": output}


async def stop_container(name: str, force: bool = False) -> dict:
    cmd = ["incus", "stop", name]
    if force:
        cmd.append("--force")
    code, output = await _run(cmd)
    return {"success": code == 0, "output": output}


async def restart_container(name: str) -> dict:
    code, output = await _run(["incus", "restart", name])
    return {"success": code == 0, "output": output}


async def destroy_container(name: str) -> dict:
    """Stop and delete a container, in whatever project it lives in.

    Resolves the container's actual project first so an agent in a restricted
    project (e.g. ``user-999``) is destroyed rather than left orphaned when the
    ambient project is ``default``. Falls back to the ambient project when the
    lookup finds nothing (preserves the original behavior for fresh installs).
    """
    proj = await _resolve_container_project(name)
    proj_args = ["--project", proj] if proj else []
    await _run(["incus", "stop", *proj_args, name, "--force"])
    code, output = await _run(["incus", "delete", *proj_args, name, "--force"])
    return {"success": code == 0, "output": output}


async def rename_container(old_name: str, new_name: str) -> dict:
    """Rename a stopped container.

    Container must already be stopped — incus/docker rename refuses to
    rename a running instance.
    """
    code, output = await _run(["incus", "rename", old_name, new_name])
    return {"success": code == 0, "output": output}


async def add_proxy_device(
    name: str, device_name: str, listen: str, connect: str,
    bind_mode: str | None = None,
) -> dict:
    """Attach an incus proxy device so the container can reach a host
    service via its own localhost.

    `listen` is the container-side bind (e.g. ``tcp:127.0.0.1:4000``);
    `connect` is where incus forwards to on the host (e.g. the same
    host-local address). Stable device names let the deployer upgrade
    the target port later without device-name collisions.

    `bind_mode`: when set to ``'instance'``, incus binds the listen
    address inside the container rather than on the host.  Use this
    when the host service already owns the port (e.g. litellm on 4000)
    so incus does not try to re-bind it on the host side.
    """
    cmd = [
        "incus", "config", "device", "add", name, device_name, "proxy",
        f"listen={listen}",
        f"connect={connect}",
    ]
    if bind_mode:
        cmd.append(f"bind={bind_mode}")
    code, output = await _run(cmd)
    if code != 0 and "Proxy devices are forbidden" in (output or ""):
        # Multi-user installs put agent containers in a restricted incus
        # project (e.g. user-999) which blocks proxy devices by default. taOS
        # needs proxy devices to wire the container's localhost to host
        # services (LiteLLM, the controller). Only the trusted controller adds
        # devices, never the agent inside the container, so relaxing this one
        # restriction is safe; the isolation that matters (idmap, disk paths,
        # network) stays. Self-heal: allow proxy devices on the project named
        # in the error and retry once, so deploys work regardless of how the
        # per-user project was provisioned (the provisioning is outside taOS).
        m = re.search(r'project "([^"]+)"', output or "")
        if m:
            project = m.group(1)
            allow_code, _ = await _run([
                "incus", "project", "set", project,
                "restricted.devices.proxy", "allow",
            ])
            if allow_code == 0:
                logger.warning(
                    "add_proxy_device: project %r forbade proxy devices; set "
                    "restricted.devices.proxy=allow and retried %s",
                    project, device_name,
                )
                code, output = await _run(cmd)
    return {"success": code == 0, "output": output}


async def get_container_logs(name: str, lines: int = 100) -> str:
    """Get recent logs from a container's journal."""
    code, output = await exec_in_container(
        name, ["journalctl", "--no-pager", "-n", str(lines)], timeout=30,
    )
    return output if code == 0 else f"Error getting logs: {output}"


def _is_snapshot_forbidden(output: str) -> bool:
    """True when an incus error means the container's project forbids snapshots.

    Detects the restriction robustly by its wording rather than by a hard-coded
    project name, so it fires for any per-user project (user-999, user-1001, …):
    incus 6.x phrases it as ``Project "user-999" doesn't allow for snapshot
    creation``. Matches both the curly and straight apostrophe and is
    case-insensitive.
    """
    low = (output or "").lower().replace("’", "'")
    return "snapshot creation" in low and (
        "doesn't allow" in low or "does not allow" in low or "not allowed" in low
    )


async def snapshot_create(name: str, snapshot_name: str) -> dict:
    """Create a named snapshot of a container.

    LXC: ``incus snapshot create <name> <snapshot_name>`` — zero-copy on
    btrfs/ZFS-backed pools; full rsync on dir-backed pools.

    Docker: ``docker commit <name> taos/<snapshot_name>:latest``.

    Self-heals project snapshot restrictions: multi-user installs put agent
    containers in a restricted incus project (e.g. user-999) provisioned
    outside taOS, which can forbid snapshot creation. A snapshot is taOS's
    point-in-time restore guarantee, so rather than fail the archive we set
    ``restricted.snapshots=allow`` on the offending project (mirroring the
    ``add_proxy_device`` self-heal for ``restricted.devices.proxy``) and retry
    once. Only the trusted controller snapshots, never the agent inside the
    container, so relaxing this one restriction is safe.

    Returns ``{"success": bool, "output": str}``.
    """
    code, output = await _run(["incus", "snapshot", "create", name, snapshot_name])
    if code != 0 and _is_snapshot_forbidden(output):
        # Find the project named in the error; fall back to resolving it from
        # the container itself if the message did not quote one.
        m = re.search(r'[Pp]roject "([^"]+)"', output or "")
        project = m.group(1) if m else await _resolve_container_project(name)
        if project:
            allow_code, allow_out = await _run([
                "incus", "project", "set", project,
                "restricted.snapshots", "allow",
            ])
            if allow_code == 0:
                logger.warning(
                    "snapshot_create: project %r forbade snapshots; set "
                    "restricted.snapshots=allow and retried %s/%s",
                    project, name, snapshot_name,
                )
                code, output = await _run(
                    ["incus", "snapshot", "create", name, snapshot_name]
                )
            else:
                logger.warning(
                    "snapshot_create: could not relax restricted.snapshots on "
                    "project %r: %s",
                    project, allow_out,
                )
    return {"success": code == 0, "output": output}


async def snapshot_restore(name: str, snapshot_name: str) -> dict:
    """Restore a container to a previously-created snapshot.

    LXC: ``incus snapshot restore <name> <snapshot_name>``.  The container
    must be stopped beforehand; the running filesystem is replaced in-place.

    Docker: not supported natively; returns
    ``{"success": False, "note": "docker snapshot restore not supported"}``.

    Returns ``{"success": bool, "output": str}``.
    """
    code, output = await _run(["incus", "snapshot", "restore", name, snapshot_name])
    return {"success": code == 0, "output": output}


async def snapshot_list(name: str, *, prefix: str | None = None) -> list[dict]:
    """Return snapshots for the container, newest first. Optionally filter
    by name prefix. Each dict has 'name' and 'created_at'."""
    rc, out = await _run(
        ["incus", "snapshot", "list", name, "--format", "csv"], timeout=30,
    )
    if rc != 0:
        return []
    snaps: list[dict] = []
    for line in out.strip().splitlines():
        parts = [p.strip() for p in line.split(",")]
        if len(parts) < 2 or not parts[0]:
            continue
        if prefix and not parts[0].startswith(prefix):
            continue
        snaps.append({"name": parts[0], "created_at": parts[1]})
    return snaps


async def snapshot_delete(name: str, snapshot_name: str) -> None:
    """Delete one snapshot. Best-effort — errors are logged, not raised."""
    rc, out = await _run(
        ["incus", "snapshot", "delete", name, snapshot_name], timeout=60,
    )
    if rc != 0:
        logger.warning(
            "snapshot_delete failed for %s/%s: %s",
            name, snapshot_name, out[:200],
        )


async def remote_add(
    name: str,
    url: str,
    *,
    token: str,
    accept_certificate: bool = True,
) -> dict:
    """Register an incus remote host using a one-time TLS token (incus 6.x).

    Wraps ``incus remote add <name> <url> --token <token>`` (plus
    ``--accept-certificate`` when *accept_certificate* is True).

    The token is generated on the target host via ``remote_generate_token``
    and must be passed by the caller.  ``core.trust_password`` was removed
    in incus 6.x; this is the replacement enrollment flow.

    Returns ``{"success": bool, "output": str}``.

    Idempotent: if a remote with *name* already exists and points to *url*,
    returns success without calling incus again.  Fails only if the existing
    remote points to a different URL.
    """
    # Check whether the remote already exists before attempting to add it.
    list_code, list_out = await _run(["incus", "remote", "list", "--format=json"])
    if list_code == 0:
        import json as _json
        try:
            remotes = _json.loads(list_out)
            if name in remotes:
                existing_url = (remotes[name].get("Addr") or "").rstrip("/")
                if existing_url == url.rstrip("/"):
                    return {"success": True, "output": "remote already enrolled"}
                return {
                    "success": False,
                    "output": (
                        f"remote '{name}' already exists pointing to {existing_url!r}, "
                        f"expected {url!r}"
                    ),
                }
        except Exception:
            pass  # If we can't parse the list, fall through and let remote add fail naturally

    cmd = ["incus", "remote", "add", name, url, "--token", token]
    if accept_certificate:
        cmd.append("--accept-certificate")
    code, output = await _run(cmd)
    return {"success": code == 0, "output": output}


async def remote_generate_token(
    client_name: str,
    *,
    projects: list[str] | None = None,
    restricted: bool = False,
) -> dict:
    """Run ``incus config trust add`` on the LOCAL incus; return ``{"token": "..."}``.

    This must be called on the **target host** (the one being enrolled INTO),
    not on the source.  In cluster context, invoke this via the worker registry
    to ask the target worker to generate a token for the source host.

    The command output ends with a base64-encoded token on its own line.

    Returns ``{"success": bool, "token": str, "output": str}``.
    """
    cmd = ["incus", "config", "trust", "add", client_name]
    if restricted:
        cmd.append("--restricted")
    if projects:
        cmd.extend(["--projects", ",".join(projects)])
    code, output = await _run(cmd)
    if code != 0:
        return {"success": False, "token": "", "output": output}
    # The token is the last non-empty line of the output. incus formats trust
    # tokens as base64url-encoded JSON; reject anything that clearly isn't one
    # so a broken / changed incus output format doesn't silently return "".
    token = ""
    for line in reversed(output.strip().splitlines()):
        line = line.strip()
        if line:
            token = line
            break
    if not token:
        return {
            "success": False,
            "token": "",
            "output": (
                "`incus config trust add` succeeded but produced no token. "
                f"Raw output: {output!r}"
            ),
        }
    # Real incus trust tokens are long base64url-encoded blobs with no
    # whitespace. Embedded spaces would mean we grabbed a help/status line
    # instead of the token payload.
    if " " in token or "\t" in token:
        return {
            "success": False,
            "token": "",
            "output": (
                f"`incus config trust add` returned unexpected output; last "
                f"line {token!r} does not look like a trust token. "
                f"Full output: {output!r}"
            ),
        }
    return {"success": True, "token": token, "output": output}


async def remote_list() -> list[dict]:
    """Return registered incus remotes as a list of dicts with name/addr/protocol.

    Wraps ``incus remote list --format=csv``.  Returns an empty list on error.
    """
    code, output = await _run(["incus", "remote", "list", "--format=csv"])
    if code != 0:
        logger.error("incus remote list failed: %s", output)
        return []
    remotes: list[dict] = []
    for line in output.strip().splitlines():
        parts = [p.strip() for p in line.split(",")]
        # CSV columns: NAME,URL,PROTOCOL,AUTH_TYPE,PUBLIC,STATIC,GLOBAL
        if len(parts) < 3 or not parts[0]:
            continue
        remotes.append({"name": parts[0], "addr": parts[1], "protocol": parts[2]})
    return remotes


async def remote_remove(name: str) -> dict:
    """Remove a registered incus remote.

    Returns ``{"success": bool, "output": str}``.
    """
    code, output = await _run(["incus", "remote", "remove", name])
    return {"success": code == 0, "output": output}


async def migrate_container(
    container_name: str,
    target_remote: str,
    *,
    new_name: str | None = None,
    stateless: bool = True,
    keep_source: bool = False,
    timeout: int = 600,
) -> dict:
    """Move or copy a container to a remote incus host.

    Steps:
    1. Verify source container exists; fail with clear error if not.
    2. Verify target remote is registered; fail with clear error including the
       ``incus remote add`` command the user needs to run if not.
    3. If stateless=True and container is running: create a pre-stop snapshot,
       stop the container, run move/copy, then start the copy on the target.
    4. If stateless=False: pass ``--live`` to incus (requires CRIU on both hosts).
    5. On failure after stop: restart the source container (rollback).

    ``keep_source=True`` uses ``incus copy`` (both hosts keep the container);
    ``keep_source=False`` uses ``incus move`` (source is destroyed on success).

    Returns ``{"success": True, "source": "local:<name>", "target": "<remote>:<dest>",
    "duration_s": N}`` on success.
    """
    dest_name = new_name or container_name
    source_ref = f"local:{container_name}"
    target_ref = f"{target_remote}:{dest_name}"

    t0 = time.monotonic()

    # 1. Verify source exists.
    info_code, info_out = await _run(["incus", "info", container_name])
    if info_code != 0:
        return {
            "success": False,
            "error": f"Container '{container_name}' not found on local host: {info_out.strip()}",
        }

    # 2. Verify target remote is registered.
    remotes = await remote_list()
    remote_names = {r["name"] for r in remotes}
    if target_remote not in remote_names:
        return {
            "success": False,
            "error": (
                f"Remote '{target_remote}' is not registered. "
                f"Register it first with: incus remote add {target_remote} <url> --accept-certificate"
            ),
        }

    # Determine if the container is currently running.
    was_running = False
    for line in info_out.splitlines():
        if line.strip().lower().startswith("status:") and "running" in line.lower():
            was_running = True
            break

    pre_stop_snapshot: str | None = None

    if stateless and was_running:
        # Take a pre-stop snapshot so we can recover if the move fails.
        pre_stop_snapshot = f"taos-pre-migrate-{int(time.time())}"
        snap_result = await snapshot_create(container_name, pre_stop_snapshot)
        if not snap_result["success"]:
            logger.warning(
                "migrate_container: pre-stop snapshot failed for %s: %s",
                container_name, snap_result.get("output", ""),
            )
            pre_stop_snapshot = None  # proceed without it

        stop_result = await stop_container(container_name)
        if not stop_result["success"]:
            return {
                "success": False,
                "error": f"Failed to stop container '{container_name}': {stop_result['output']}",
            }

    # 3. Run move or copy.
    incus_verb = "copy" if keep_source else "move"
    cmd = ["incus", incus_verb, source_ref, target_ref, "--mode=push"]
    if not stateless:
        cmd.append("--live")

    code, output = await _run(cmd, timeout=timeout)

    if code != 0:
        # Rollback: restart source if we stopped it.
        # Source is stopped for all stateless migrations regardless of keep_source.
        if stateless and was_running:
            restart_result = await start_container(container_name)
            if not restart_result["success"]:
                logger.error(
                    "migrate_container: rollback start failed for %s: %s",
                    container_name, restart_result.get("output", ""),
                )
        return {
            "success": False,
            "error": f"incus {incus_verb} failed: {output.strip()}",
        }

    # 4. Start on target if it was running and we did a stateless move.
    if stateless and was_running:
        start_code, start_out = await _run(
            ["incus", "start", target_ref], timeout=120,
        )
        if start_code != 0:
            logger.warning(
                "migrate_container: container moved but failed to start on %s: %s",
                target_ref, start_out,
            )

    # For keep_source copy flows, restore source running state after successful copy.
    if keep_source and stateless and was_running:
        restart_result = await start_container(container_name)
        if not restart_result["success"]:
            logger.warning(
                "migrate_container: source restart failed after copy for %s: %s",
                container_name, restart_result.get("output", ""),
            )

    # Clean up pre-stop snapshot on source (only relevant for copy, since move destroys source).
    if keep_source and pre_stop_snapshot:
        await snapshot_delete(container_name, pre_stop_snapshot)

    duration = round(time.monotonic() - t0, 1)
    return {
        "success": True,
        "source": source_ref,
        "target": target_ref,
        "duration_s": duration,
    }


async def set_env(name: str, key: str, value: str) -> dict:
    """Set an environment variable on a container via incus config set.

    LXC: ``incus config set <name> environment.<key> <value>``.  Persisted
    in incus config; picked up by the container on next start or on restart
    of individual systemd services inside the container.

    Docker: requires container recreation; returns
    ``{"success": False, "note": "docker env change requires recreate"}``.

    Returns ``{"success": bool, "output": str}``.
    """
    code, output = await _run([
        "incus", "config", "set", name, f"environment.{key}={value}",
    ])
    return {"success": code == 0, "output": output}


__all__ = [
    "ContainerInfo",
    "_parse_memory",
    "_run",
    "detect_runtime",
    "get_backend",
    "set_backend",
    "LXCBackend",
    "DockerBackend",
    "container_exists",
    "TAOS_CONTAINER_PREFIX",
    "candidate_agent_container_names",
    "list_all_taos_containers",
    "resolve_agent_container",
    "list_containers",
    "set_root_quota",
    "create_container",
    "exec_in_container",
    "push_file",
    "add_proxy_device",
    "start_container",
    "stop_container",
    "restart_container",
    "destroy_container",
    "rename_container",
    "get_container_logs",
    "snapshot_create",
    "snapshot_restore",
    "snapshot_list",
    "snapshot_delete",
    "set_env",
    "remote_add",
    "remote_generate_token",
    "remote_list",
    "remote_remove",
    "migrate_container",
]
