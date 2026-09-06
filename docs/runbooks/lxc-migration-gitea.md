# LXC Migration — Gitea Pi ⇆ Fedora Worker

**Goal:** install Gitea as an LXC service on the Orange Pi, move it to the
Fedora worker, verify it still serves, then move it back. The round-trip
works across architectures (aarch64 ↔ x86_64) because the migration is
**state-path based** — only `/etc/gitea/` and `/home/git/` travel; the
Gitea binary is reinstalled fresh on the target host with the correct
architecture.

This is the end-to-end validation for the store's LXC install path and
`migrate_service()`.

## Pre-flight (one-time per host pair)

Both hosts need incus installed. The target host also needs incus listening
on the LAN so the controller can reach it.

On the **target** host (worker), enable the incus HTTPS listener:

```bash
incus config set core.https_address :8443
# Confirm
ss -ltn | grep 8443
```

Then generate an enrollment token for the controller (run on the worker):

```bash
incus config trust add <controller-hostname>
# Prints a one-time base64 token. Copy it.
```

Register the worker as an incus remote on the **controller**:

```bash
curl -X POST http://localhost:6969/api/cluster/remotes \
  -H "Authorization: Bearer $(cat data/.auth_local_token)" \
  -H 'Content-Type: application/json' \
  -d '{
    "name": "fedora-worker",
    "url": "https://<worker-host>:8443",
    "token": "<token-from-above>"
  }'

curl http://localhost:6969/api/cluster/remotes \
  -H "Authorization: Bearer $(cat data/.auth_local_token)"
```

For round-trip you also need the controller registered as an incus remote
on the worker — same flow in reverse.

## Procedure

### 1. Install Gitea on the controller (or a worker)

You can install directly to a registered worker by adding `"target_remote"` to
the request body. Omit it (or set it to `""` or `"local"`) to install on the
controller — the API normalizes all three to a local install.

**Install on the controller (default):**

```bash
curl -X POST http://localhost:6969/api/store/install-v2 \
  -H "Authorization: Bearer $(cat data/.auth_local_token)" \
  -H 'Content-Type: application/json' \
  -d '{
    "app_id": "gitea-lxc",
    "admin_password": "<pick-a-strong-password>",
    "taos_username": "jay",
    "taos_email": "user@example.com",
    "metadata": {"backend": "lxc"}
  }'
```

**Install directly on a worker** (skips the migrate step if you know where you
want it from the start):

```bash
curl -X POST http://localhost:6969/api/store/install-v2 \
  -H "Authorization: Bearer $(cat data/.auth_local_token)" \
  -H 'Content-Type: application/json' \
  -d '{
    "app_id": "gitea-lxc",
    "admin_password": "<pick-a-strong-password>",
    "taos_username": "jay",
    "taos_email": "jaylfc25@gmail.com",
    "metadata": {"backend": "lxc"},
    "target_remote": "fedora-worker"
  }'
```

`target_remote` must match a remote already registered via
`POST /api/cluster/remotes`. Use `GET /api/cluster/install-targets` to list
valid names. Omitting the field, passing `""`, or passing `"local"` all install
on the controller. In the taOS Store UI, an "Install on" dropdown appears
automatically on LXC apps when workers are registered.

Install takes 2-3 minutes (incus launch + apt install + Gitea binary
download). Response includes `host_port`. Verify:

```bash
curl -I http://localhost:<host_port>/
# expect: HTTP/1.1 200 OK
```

### 2. Migrate controller → worker (e.g. Pi → Fedora)

```bash
curl -X POST http://localhost:6969/api/cluster/migrate-service \
  -H "Authorization: Bearer $(cat data/.auth_local_token)" \
  -H 'Content-Type: application/json' \
  -d '{
    "app_id": "gitea-lxc",
    "target_remote": "fedora-worker",
    "keep_source": false
  }'
```

This does not copy the container image. It:

1. Stops Gitea on the source.
2. `tar`s `/etc/gitea/` + `/home/git/` inside the source container,
   streams the tarball to the controller host.
3. Launches a **fresh** container on the target from
   `images:debian/bookworm` (correct arch for the target host).
4. Installs the Gitea binary matching the target architecture.
5. Pushes the tarball into the target container, extracts over the state
   paths.
6. Starts the Gitea service.
7. Destroys the source container (unless `keep_source: true`).

Typical timing for Gitea: ~30s, ~2 MB state.

Verify on the worker:

```bash
incus list fedora-worker:taos-svc-gitea-lxc
# Status: RUNNING
incus config device show fedora-worker:taos-svc-gitea-lxc | grep listen
# find the host port, then
curl -I http://<worker-host>:<host_port>/
```

Data check — all accounts + repos came across:

```bash
curl -u "<user>:<admin_password>" \
  http://<worker-host>:<host_port>/api/v1/user/repos
```

### 3. Migrate worker → controller (reverse)

```bash
curl -X POST http://localhost:6969/api/cluster/migrate-service \
  -H "Authorization: Bearer $(cat data/.auth_local_token)" \
  -H 'Content-Type: application/json' \
  -d '{
    "app_id": "gitea-lxc",
    "source_remote": "fedora-worker",
    "target_remote": "local",
    "keep_source": false
  }'
```

Same flow, opposite direction. `target_remote: "local"` means the
controller host. Verify Gitea serves on the controller again and any repos
created while on the worker are still present.

## Why not `incus copy` / `incus move`?

`incus move` copies the entire rootfs across hosts. It works only for
**same-architecture** moves — a container built from an aarch64 base image
cannot boot on an x86_64 host (incus rejects with `Requested architecture
isn't supported by this host`).

`migrate_service` sidesteps that by transferring only the state (SQLite
DBs, on-disk repos, config). The service binary is installed fresh from
the correct-arch release on the target, so a mixed-arch cluster
(Pi + desktop + laptop) migrates cleanly.

For same-arch migrations, `POST /api/cluster/migrate` still exists and
uses `incus move` for speed — useful for homogeneous clusters where
rootfs-level moves avoid the reinstall cost.

## Recovery

If a migration fails mid-way:

- The source service is restarted automatically so the user doesn't lose
  access.
- A half-created target container is destroyed on failure (rolled back by
  the installer's existing rollback path).
- The state tarball on the controller host is always cleaned up, success
  or failure.

## Stable service URLs via `/apps/{app_id}/`

After install or migration, every service has a stable proxy URL on the
controller:

```text
http://localhost:6969/apps/gitea-lxc/
```

The controller reverse-proxies this URL to whatever host:port currently runs
the service. The runtime location is stored in `InstalledAppsStore`
(`app_runtime` table) and is updated automatically on install and migration.

### How it works

- **Install**: after the LXC installer returns `host_port`, the install route
  calls `installed_apps.update_runtime_location(app_id, host, port)`.
  - Local install → host is `127.0.0.1` (the proxy device listens on 0.0.0.0
    locally, so 127.0.0.1 always reaches it from the controller).
  - Remote install → host is parsed from the registered incus remote URL
    (`https://<host>:8443`).
- **Migration**: the migrate-service route calls `update_runtime_location`
  after a successful `migrate_service()`, pointing the entry at the new
  target host. The URL `/apps/gitea-lxc/` immediately routes to the
  new location without any manual intervention.

### Checking the current runtime location

```bash
# After install, inspect the running proxy:
curl -I http://localhost:6969/apps/gitea-lxc/
# Should return Gitea's HTTP response regardless of which host it lives on.

# After migration to fedora-worker, the same URL continues to work;
# the controller routes transparently to the worker's host:port.
```

### Location header rewriting

If Gitea (or any other service) redirects internally with an absolute
`Location: http://<host>:<port>/some-path` header, the proxy rewrites it to
`/apps/gitea-lxc/some-path` so the browser stays within the stable URL
namespace. Relative `Location` headers pass through unchanged.

### Generality

The `/apps/{app_id}/` proxy is fully generic — it reads `runtime_host` and
`runtime_port` from the store and proxies any HTTP method. Gitea is the first
service to use it but every future LXC/Docker service installs into the same
slot. The `ui_path` field in the manifest's `install` block (`default: "/"`)
can be set to the sub-path the service actually serves from if needed.

## What this proves

- The LXC install path works end-to-end (container creation → Gitea
  install → admin seeding → proxy device → real HTTP traffic).
- State-path migration preserves all user data across architectures:
  accounts, repos, commits, Gitea config, server keys.
- Round-trip migration (Pi → Fedora → Pi) leaves a service in the same
  logical state it started in, with any new data added mid-trip intact.
