# Gitea

taOS runs Gitea as a containerized service. This page names the environment
variables the Gitea integration actually reads, verified by grepping the source
for `GITEA_` -- not the names guessed on the bus.

## What the code reads today

A repo-wide grep for `GITEA_` shows that **no taOS code reads a Gitea client URL
or service-token variable**. `GITEA_URL`, `GITEA_TOKEN`, and `GITEA_BASE_URL` are
**not read by any code path**; they only surface in a design plan
(`docs/design/architecture-pivot-v2.md`, "Phase 3 -- Forgejo integration"), and
there they appear under a different prefix (`TAOS_GITEA_URL` /
`TAOS_GITEA_TOKEN`) as a future injection into agent containers. Setting
`GITEA_URL` / `GITEA_TOKEN` / `GITEA_BASE_URL` will have no effect on a taOS
deployment today.

The only Gitea-related environment the integration surfaces lives in the
app-store manifest
`app-catalog/services/gitea/manifest.yaml` (`install.env`), which the Docker
installer forwards verbatim (`tinyagentos/installers/docker_installer.py`
reads `install_config["env"]` and writes it to the compose `environment:`
block). These use Gitea's native `GITEA__<section>__<key>` convention and are
consumed by the Gitea process inside the container.

### `GITEA__server__ROOT_URL`

- **Purpose:** Base URL Gitea advertises for avatars, clone URLs, and redirect
  targets.
- **Example:** `http://localhost:3000`
- **When unset:** Gitea falls back to its compiled default
  (`http://localhost:3000`).

### `GITEA__server__SSH_PORT`

- **Purpose:** SSH daemon port exposed by the Gitea container.
- **Example:** `2222`
- **When unset:** Gitea falls back to its compiled default (`22`).

> The incus/LXC path (`tinyagentos/installers/lxc_installer.py`) does **not**
> read these env vars. It generates `/etc/gitea/app.ini` at install time --
> `ROOT_URL`, `SSH_PORT`, `SECRET_KEY`, `INTERNAL_TOKEN` (random per install),
> `DISABLE_REGISTRATION = true`. The only `GITEA_WORK_DIR` references in the
> codebase are set *inside* the container's systemd unit; taOS does not read it
> as input.

## Gitea client / agent account provisioning (planned, not implemented)

taOS is intended to provision one Gitea user per agent via
`POST /api/v1/admin/users`, authenticating as a service account. That
integration is not wired up: **no code reads `TAOS_GITEA_URL` or
`TAOS_GITEA_TOKEN`**. They appear only in the design plan
`docs/design/architecture-pivot-v2.md` ("Phase 3 -- Forgejo integration") as a
future env injection. Until it is implemented, there is no service-token
variable to set on the Pi host.

## Env / config surface consistency

- **No conflicting name is in use.** A grep for `GITEA_` across the code/config
  shows only `GITEA__server__ROOT_URL` / `GITEA__server__SSH_PORT` (the manifest
  above) and `GITEA_WORK_DIR` (set *inside* the container's systemd unit, never
  read by taOS). `GITEA_BASE_URL` is **not read anywhere** in the repo.
- **Deprecation path for the bus-guessed names.** `GITEA_URL`, `GITEA_TOKEN`, and
  `GITEA_BASE_URL` were never wired into any code path, so there is nothing to
  carry forward -- no deployment depends on them and no alias/shim is needed.
  When the planned agent-account provisioning ships (§"Gitea client / agent
  account provisioning"), it will use `TAOS_GITEA_URL` / `TAOS_GITEA_TOKEN`
  (per `docs/design/architecture-pivot-v2.md`). Until then these names remain
  unused and must not be set as if they are live.
- The app-store manifest is the repo's existing config mechanism for Gitea
  service env; no separate `.env.example` is used and none is introduced here.
