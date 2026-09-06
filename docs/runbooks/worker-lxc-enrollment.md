# Worker LXC Enrollment

**Goal:** connect a new worker's incus daemon to the controller so LXC-backed
services can be deployed to it without any manual incus configuration.

Since taOS `feat/worker-installer-auto-incus`, this happens **automatically**
when `install-worker.sh` runs on a Linux host. This runbook documents the
automatic flow, explains what each step does, and provides fallback commands
if something goes wrong.

## Automatic flow (normal case)

Running the worker installer on a Linux host does all of this without
requiring any additional user input:

1. **Installs incus** via the distro package manager (or the zabbly repo for
   older Debian/Ubuntu releases that don't ship incus yet).
2. **Adds the install user to `incus-admin`** so future incus commands don't
   require sudo.
3. **First-time `incus admin init --minimal`** — only runs if incus isn't
   already initialised.
4. **Enables the HTTPS listener** on `:8443` — skipped if already set.
5. **Generates a one-shot trust token** (`incus config trust add controller-enroll`).
6. **Detects the worker's LAN IP** using `hostname -I`.
7. **POSTs to the controller** at
   `POST /api/cluster/workers/<name>/incus-enroll`
   with `{"incus_url": "https://<LAN_IP>:8443", "token": "<TOKEN>"}`.

The controller calls `incus remote add <worker-name> <url> --token=<token>
--accept-certificate` on its side, completing the trust handshake.

## Skipping LXC enrollment

Set `TAOS_SKIP_INCUS=1` before running the installer:

```sh
TAOS_SKIP_INCUS=1 bash install-worker.sh http://controller:6969
```

macOS workers set this automatically (incus is Linux-only). Windows workers
also skip by default and log a warning.

## Manual enrollment (fallback)

If the automatic step failed (e.g. the controller was unreachable during
install, or the HTTP call returned a non-2xx response), enroll the worker
manually:

```sh
# On the worker
TOKEN=$(incus config trust add controller-enroll 2>&1 | tail -1)
LAN_IP=$(hostname -I | awk '{print $1}')

curl -X POST http://<controller>:6969/api/cluster/workers/<worker-name>/incus-enroll \
    -H "Content-Type: application/json" \
    -d "{\"incus_url\": \"https://${LAN_IP}:8443\", \"token\": \"${TOKEN}\"}"
```

The endpoint returns `{"ok": true}` on success or `{"ok": false, "error":
"..."}` on failure.

## Verifying enrollment

On the controller host:

```sh
incus remote list
```

The worker should appear as a named remote (e.g. `pi-worker`). You can then
list its containers:

```sh
incus list pi-worker:
```

## Re-enrollment after a controller reinstall

If the controller's incus state is wiped (e.g. a fresh controller install),
the existing worker trust certificate is no longer valid. Re-enroll by
running the manual steps above on each worker. The worker's incus daemon and
:8443 listener are still running and do not need to be reconfigured.
