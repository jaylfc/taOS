"""taosctl cluster -- inspect and manage the worker cluster.

Wraps the user/admin-facing /api/cluster routes (tinyagentos/routes/cluster.py,
cluster_capability.py, cluster_map.py, cluster_migrate.py, cluster_ble.py).

Deliberately not wrapped:
  - worker-side, unauthenticated or worker-HMAC routes: POST
    /api/cluster/pairing/announce, /pairing/claim, /pairing/manual-claim,
    POST /api/cluster/workers (register), /heartbeat,
    /capability/heartbeat, /workers/{name}/incus-enroll (worker installer);
  - POST /api/cluster/route: a generic proxy with a nested free-form body.
"""
from __future__ import annotations

from urllib.parse import quote

# Note: options named like the global --url/--token flags use a distinct dest so
# they do not overwrite the server URL / API token the client is built from.

NOUN = "cluster"


def _q(value: str) -> str:
    return quote(value, safe="")


def register(subparsers) -> None:
    p = subparsers.add_parser(NOUN, help="Inspect and manage the worker cluster")
    verbs = p.add_subparsers(dest="verb", required=True, metavar="<verb>")

    # ---- workers ---------------------------------------------------------
    verbs.add_parser("workers", help="List cluster workers").set_defaults(func=_workers)

    for verb, fn, helptext in (
        ("remove", _remove, "Unregister a worker (admin)"),
        ("revoke", _revoke, "Revoke a worker's signing key (admin)"),
        ("block", _block, "Revoke and block a worker (admin)"),
        ("unblock", _unblock, "Unblock a worker (admin)"),
        ("cancel-drain", _cancel_drain, "Cancel a worker drain (admin)"),
        ("update-worker", _update_worker, "Update one worker (admin)"),
    ):
        vp = verbs.add_parser(verb, help=helptext)
        vp.add_argument("name", help="Worker name")
        vp.set_defaults(func=fn)

    dp = verbs.add_parser("drain", help="Drain a worker (admin)")
    dp.add_argument("name", help="Worker name")
    dp.add_argument("--force", action="store_true",
                    help="Force-release leases instead of draining gracefully")
    dp.set_defaults(func=_drain)

    verbs.add_parser("update-all", help="Rolling update of every online worker (admin)") \
        .set_defaults(func=_update_all)

    up = verbs.add_parser("update-status", help="Show progress of an update-all job")
    up.add_argument("job_id", help="Job id returned by update-all")
    up.set_defaults(func=_update_status)

    dep = verbs.add_parser("deploy", help="Run an allow-listed deploy command on a worker (admin)")
    dep.add_argument("name", help="Worker name")
    dep.add_argument("--command", required=True, help="Deploy command, e.g. install-ollama")
    dep.set_defaults(func=_deploy)

    rp = verbs.add_parser("remote-command", help="Run a remote command on a worker (admin)")
    rp.add_argument("name", help="Worker name")
    rp.add_argument("--command", required=True, help="Command to run")
    rp.add_argument("--timeout", type=int, default=None, help="Timeout in seconds")
    rp.set_defaults(func=_remote_command)

    # ---- read-only cluster views ----------------------------------------
    for verb, fn, helptext in (
        ("capabilities", _capabilities, "List capabilities across online workers"),
        ("kv-quant-options", _kv_quant_options, "List KV-cache quantisation options"),
        ("backends", _backends, "List backends across the cluster"),
        ("optimise", _optimise, "Show cluster optimisation suggestions"),
        ("install-targets", _install_targets, "List install targets"),
        ("map", _map, "Show the aggregated capability map"),
    ):
        verbs.add_parser(verb, help=helptext).set_defaults(func=fn)

    mp = verbs.add_parser("move", help="Move a model to another worker (admin)")
    mp.add_argument("--item", required=True, help="Model id")
    mp.add_argument("--to", dest="to_worker", required=True, help="Target worker")
    mp.add_argument("--from", dest="from_worker", default=None, help="Source worker")
    mp.set_defaults(func=_move)

    verbs.add_parser("promote-archived", help="Promote archived models to online workers (admin)") \
        .set_defaults(func=_promote_archived)

    # ---- pairing -----------------------------------------------------------
    verbs.add_parser("pairing-pending", help="List pending pairing requests (admin)") \
        .set_defaults(func=_pairing_pending)

    pc = verbs.add_parser("pairing-confirm", help="Confirm a pending pairing (admin)")
    pc.add_argument("--name", required=True, help="Worker name")
    pc.add_argument("--code", required=True, help="Pairing code shown on the worker")
    pc.set_defaults(func=_pairing_confirm)

    pm = verbs.add_parser("pairing-manual", help="Authorise a manual pairing code (admin)")
    pm.add_argument("--url", dest="worker_url", required=True, help="Worker URL")
    pm.add_argument("--code", required=True, help="Pairing code")
    pm.set_defaults(func=_pairing_manual)

    # ---- leases ------------------------------------------------------------
    verbs.add_parser("leases", help="List GPU leases").set_defaults(func=_leases)

    lc = verbs.add_parser("lease-claim", help="Claim a resource lease")
    lc.add_argument("--resource-id", required=True, help="Resource id")
    lc.add_argument("--ttl", type=float, default=None, help="TTL in seconds")
    lc.add_argument("--caller", default=None, help="Caller label")
    lc.add_argument("--vram-mb", type=int, default=None, help="Required VRAM in MB")
    lc.set_defaults(func=_lease_claim)

    lr = verbs.add_parser("lease-release", help="Release a lease")
    lr.add_argument("lease_id", help="Lease id")
    lr.set_defaults(func=_lease_release)

    ln = verbs.add_parser("lease-renew", help="Renew a lease")
    ln.add_argument("lease_id", help="Lease id")
    ln.add_argument("--ttl", type=float, default=None, help="TTL in seconds")
    ln.set_defaults(func=_lease_renew)

    # ---- capability registry ---------------------------------------------
    cl = verbs.add_parser("capability-list", help="List capability registry rows (admin)")
    cl.add_argument("--status", default=None, help="Filter by status")
    cl.set_defaults(func=_capability_list)

    cs = verbs.add_parser("capability-status", help="Set a node's capability status (admin)")
    cs.add_argument("node_id", help="Node id")
    cs.add_argument("status", help="New status, e.g. online or draining")
    cs.set_defaults(func=_capability_status)

    for verb, fn, helptext in (
        ("capability-prune", _capability_prune, "Prune stale capability rows (admin)"),
        ("capability-sweep", _capability_sweep, "Mark stale capability rows offline (admin)"),
    ):
        vp = verbs.add_parser(verb, help=helptext)
        vp.add_argument("--older-than", dest="older_than_s", type=int, default=None,
                        help="Staleness threshold in seconds (server default if omitted)")
        vp.set_defaults(func=fn)

    # ---- incus remotes + migration ---------------------------------------
    verbs.add_parser("remotes", help="List incus remotes (admin)").set_defaults(func=_remotes)

    ra = verbs.add_parser("remote-add", help="Add an incus remote (admin)")
    ra.add_argument("--name", required=True, help="Remote name")
    ra.add_argument("--url", dest="remote_url", required=True, help="Remote URL")
    ra.add_argument("--token", dest="remote_token", required=True, help="Trust token")
    ra.set_defaults(func=_remote_add)

    rt = verbs.add_parser("remote-token", help="Generate an incus trust token (admin)")
    rt.add_argument("--client-name", required=True, help="Client name")
    rt.add_argument("--projects", default=None, help="Comma-separated project list")
    rt.add_argument("--restricted", action="store_true", help="Restrict the token")
    rt.set_defaults(func=_remote_token)

    rr = verbs.add_parser("remote-remove", help="Remove an incus remote (admin)")
    rr.add_argument("name", help="Remote name")
    rr.set_defaults(func=_remote_remove)

    mg = verbs.add_parser("migrate", help="Migrate a container to a remote (admin)")
    mg.add_argument("--container", required=True, help="Container name")
    mg.add_argument("--target-remote", required=True, help="Target remote")
    mg.add_argument("--new-name", default=None, help="Name on the target")
    mg.add_argument("--keep-source", action="store_true", help="Keep the source container")
    mg.add_argument("--stateful", action="store_true", help="Live (stateful) migration")
    mg.add_argument("--timeout", type=int, default=None, help="Timeout in seconds")
    mg.set_defaults(func=_migrate)

    ms = verbs.add_parser("migrate-service", help="Migrate an installed service to a remote (admin)")
    ms.add_argument("--app-id", required=True, help="App id")
    ms.add_argument("--target-remote", required=True, help="Target remote")
    ms.add_argument("--source-remote", default=None, help="Source remote")
    ms.add_argument("--keep-source", action="store_true", help="Keep the source install")
    ms.set_defaults(func=_migrate_service)

    # ---- BLE pairing -------------------------------------------------------
    bs = verbs.add_parser("ble-scan", help="Scan for BLE workers (admin)")
    bs.add_argument("--seconds", type=float, default=None, help="Scan duration")
    bs.set_defaults(func=_ble_scan)

    bp = verbs.add_parser("ble-pair-start", help="Start BLE pairing (admin)")
    bp.add_argument("address", help="BLE device address")
    bp.set_defaults(func=_ble_pair_start)

    for verb, fn, helptext in (
        ("ble-pair-confirm", _ble_pair_confirm, "Confirm a BLE pairing session (admin)"),
        ("ble-pair-cancel", _ble_pair_cancel, "Cancel a BLE pairing session (admin)"),
    ):
        vp = verbs.add_parser(verb, help=helptext)
        vp.add_argument("session", help="Pairing session id")
        vp.set_defaults(func=fn)


# ---- workers ---------------------------------------------------------------

def _workers(args, client):
    return client.get("/api/cluster/workers")


def _remove(args, client):
    return client.delete(f"/api/cluster/workers/{_q(args.name)}")


def _revoke(args, client):
    return client.post(f"/api/cluster/workers/{_q(args.name)}/revoke")


def _block(args, client):
    return client.post(f"/api/cluster/workers/{_q(args.name)}/block")


def _unblock(args, client):
    return client.post(f"/api/cluster/workers/{_q(args.name)}/unblock")


def _drain(args, client):
    return client.post(f"/api/cluster/workers/{_q(args.name)}/drain",
                       body={"graceful": not args.force})


def _cancel_drain(args, client):
    return client.post(f"/api/cluster/workers/{_q(args.name)}/cancel-drain")


def _update_worker(args, client):
    return client.post(f"/api/cluster/workers/{_q(args.name)}/update")


def _update_all(args, client):
    return client.post("/api/cluster/workers/update-all")


def _update_status(args, client):
    return client.get(f"/api/cluster/workers/update-all/{_q(args.job_id)}")


def _deploy(args, client):
    return client.post(f"/api/cluster/workers/{_q(args.name)}/deploy",
                       body={"command": args.command})


def _remote_command(args, client):
    body = {"command": args.command}
    if args.timeout is not None:
        body["timeout"] = args.timeout
    return client.post(f"/api/cluster/workers/{_q(args.name)}/remote", body=body)


# ---- read-only views -------------------------------------------------------

def _capabilities(args, client):
    return client.get("/api/cluster/capabilities")


def _kv_quant_options(args, client):
    return client.get("/api/cluster/kv-quant-options")


def _backends(args, client):
    return client.get("/api/cluster/backends")


def _optimise(args, client):
    return client.get("/api/cluster/optimise")


def _install_targets(args, client):
    return client.get("/api/cluster/install-targets")


def _map(args, client):
    return client.get("/api/cluster/map")


def _move(args, client):
    body = {"item": args.item, "to_worker": args.to_worker}
    if args.from_worker:
        body["from_worker"] = args.from_worker
    return client.post("/api/cluster/move", body=body)


def _promote_archived(args, client):
    return client.post("/api/cluster/promote-archived")


# ---- pairing ---------------------------------------------------------------

def _pairing_pending(args, client):
    return client.get("/api/cluster/pairing/pending")


def _pairing_confirm(args, client):
    return client.post("/api/cluster/pairing/confirm",
                       body={"name": args.name, "code": args.code})


def _pairing_manual(args, client):
    return client.post("/api/cluster/pairing/manual",
                       body={"url": args.worker_url, "code": args.code})


# ---- leases ----------------------------------------------------------------

def _leases(args, client):
    return client.get("/api/cluster/leases")


def _lease_claim(args, client):
    body = {"resource_id": args.resource_id}
    if args.ttl is not None:
        body["ttl_seconds"] = args.ttl
    if args.caller is not None:
        body["caller"] = args.caller
    if args.vram_mb is not None:
        body["required_vram_mb"] = args.vram_mb
    return client.post("/api/cluster/leases/claim", body=body)


def _lease_release(args, client):
    return client.post("/api/cluster/leases/release", body={"lease_id": args.lease_id})


def _lease_renew(args, client):
    body = {"lease_id": args.lease_id}
    if args.ttl is not None:
        body["ttl_seconds"] = args.ttl
    return client.post("/api/cluster/leases/renew", body=body)


# ---- capability registry ---------------------------------------------------

def _capability_list(args, client):
    return client.get("/api/cluster/capability", params={"status": args.status})


def _capability_status(args, client):
    return client.post(f"/api/cluster/capability/{_q(args.node_id)}/status",
                       body={"status": args.status})


def _prune_body(args) -> dict:
    return {} if args.older_than_s is None else {"older_than_s": args.older_than_s}


def _capability_prune(args, client):
    return client.post("/api/cluster/capability/prune", body=_prune_body(args))


def _capability_sweep(args, client):
    return client.post("/api/cluster/capability/sweep", body=_prune_body(args))


# ---- incus remotes + migration ---------------------------------------------

def _remotes(args, client):
    return client.get("/api/cluster/remotes")


def _remote_add(args, client):
    return client.post("/api/cluster/remotes",
                       body={"name": args.name, "url": args.remote_url, "token": args.remote_token})


def _remote_token(args, client):
    body = {"client_name": args.client_name, "restricted": args.restricted}
    if args.projects:
        body["projects"] = [s.strip() for s in args.projects.split(",") if s.strip()]
    return client.post("/api/cluster/remotes/token", body=body)


def _remote_remove(args, client):
    return client.delete(f"/api/cluster/remotes/{_q(args.name)}")


def _migrate(args, client):
    body = {
        "container": args.container,
        "target_remote": args.target_remote,
        "keep_source": args.keep_source,
        "stateless": not args.stateful,
    }
    if args.new_name:
        body["new_name"] = args.new_name
    if args.timeout is not None:
        body["timeout"] = args.timeout
    return client.post("/api/cluster/migrate", body=body)


def _migrate_service(args, client):
    body = {
        "app_id": args.app_id,
        "target_remote": args.target_remote,
        "keep_source": args.keep_source,
    }
    if args.source_remote:
        body["source_remote"] = args.source_remote
    return client.post("/api/cluster/migrate-service", body=body)


# ---- BLE pairing -----------------------------------------------------------

def _ble_scan(args, client):
    return client.get("/api/cluster/ble/scan", params={"seconds": args.seconds})


def _ble_pair_start(args, client):
    return client.post("/api/cluster/ble/pair/start", body={"address": args.address})


def _ble_pair_confirm(args, client):
    return client.post("/api/cluster/ble/pair/confirm", body={"session": args.session})


def _ble_pair_cancel(args, client):
    return client.post("/api/cluster/ble/pair/cancel", body={"session": args.session})
