# Container-first app installs

**Status:** Proposal, for owner review. No code in this PR.
**Ruling it implements (owner, 2026-10-25):** store apps install INTO A
CONTAINER. By default an app gets its own container; the user may instead add
it to an existing container, chosen at install time in the install dialog.
Suites install whole (one container) or per app. Containers can move across the
cluster. Host-native installs are a later, optional tier whose window is
streamed over WebRTC. The container layer supports both LXC/Incus and Docker.

## 1. Current install path

### Routes

- `POST /api/store/install-v2` in `tinyagentos/routes/store_install.py` is the
  route the desktop uses. It validates `target_remote`, starts progress
  tracking (`tinyagentos/install_progress.py`), verifies the manifest
  signature (twice, to close a swap race), applies the weights-licence gate
  (412), then hands everything that is not a model to `_legacy_install`.
- `_legacy_install` picks `install.backend`, else `install.method`, default
  `docker`, and branches:
  - `docker` / `pip`: `tinyagentos/installers/docker_installer.py` writes
    `apps/<id>/docker-compose.yaml` on the controller and runs compose;
    `pip_installer.py` builds a host venv. Both run **on the host**. A
    `target_remote` on a docker install only changes the recorded host.
  - `script`: `ScriptInstaller`, host bash.
  - `lxc`: `tinyagentos/installers/lxc_installer.py`, container
    `taos-svc-<id>`, the only catalog path that installs into a container.
    Its steps are hard-coded to Gitea (one manifest, `gitea-lxc`).
  - agent frameworks: only marked installed here; the real install runs inside
    the agent's LXC at deploy time (`tinyagentos/deployer.py`).
  - Anything else is rejected, so `npm`, `uvx`, `apt` and `source` manifests
    (about 30 in the catalog) have no working installer.
- The older `POST /api/store/install` in `tinyagentos/routes/store.py` uses
  the same installers.

### State

Installed state lives in four places: `installed.json`
(`AppRegistry.mark_installed`), `installed_apps.db` with an `app_runtime`
table (`runtime_host`, `runtime_port`, `ui_path`) that the reverse proxy
reads, the live-probe `InstallationState` view, and `install_registry.db`
(`tinyagentos/install_registry.py`) with `location_kind` / `location_ref`
columns that nothing in the install path writes yet.

### Catalog

`app-catalog/{agents,models,services,plugins}/<id>/manifest.yaml`, loaded by
`AppRegistry._load_catalog` into the pydantic `AppManifest`
(`tinyagentos/registry.py`) with `extra="ignore"`, so unknown keys are dropped
silently. The JSON schema is `scripts/manifest.schema.json`. The closest thing
to a suite is `app-catalog/templates/*.yaml` (an `apps:` id list), listed by
`GET /api/store/templates` but never batch-installed or used by the desktop.
`app-catalog/streaming/` is not loaded at all.

### Container runtime

`tinyagentos/containers/backend.py` defines the `ContainerBackend` ABC with
LXC, Docker, native and Apple backends (create, exec, push_file, start/stop,
destroy, proxy device, snapshot). The module API in
`tinyagentos/containers/__init__.py` adds Incus remotes and
`migrate_container` (`incus move/copy`, optional live, snapshot plus
rollback). Docker has no real snapshot, export or migrate.
`tinyagentos/userspace/container_deploy.py` runs userspace `.taosapp`
container apps as `taos-app-<id>` on Docker directly.

### Cluster

`tinyagentos/routes/cluster_migrate.py` exposes remote registration,
`POST /api/cluster/migrate` (container move) and `migrate-service`, which
`tinyagentos/cluster/service_migrator.py` implements by tarring the
manifest's `state_paths`, recreating the container on the target and
updating the runtime location. `GET /api/cluster/install-targets` returns the
controller plus Incus remotes with hardware tier ids. Placement helpers live in
`tinyagentos/cluster/capabilities.py` and `tinyagentos/app_orchestrator.py`
(a stub that picks the GPU worker with most VRAM).

### Serving

`tinyagentos/routes/service_proxy.py` proxies `/apps/<id>/...` to the
recorded `runtime_host:runtime_port`. The desktop opens a `service:<id>`
window (`ServiceAppWindow.tsx`, sandboxed iframe). Userspace static apps are
served from `/api/userspace-apps/<id>/bundle/`. The unified Browser streams
Neko containers over WebRTC (`tinyagentos/worker/browser_container.py`,
`LiveBrowserView.tsx`).

### Desktop

`desktop/src/apps/StoreApp/index.tsx` installs inline on the card: an
"Install on" select filled from `/api/cluster/install-targets`, a variant
picker, then `POST /api/store/install-v2`. There is no modal. The UI never
sends `requires.inputs` (so `gitea-lxc` cannot install from the UI today).

## 2. Gap map

| Need | Today | Gap |
|---|---|---|
| Every app in a container | Only `gitea-lxc`; docker/pip/script run on the host | A generic "install inside container X" executor per method |
| New vs existing container | No concept | A container record, a compatibility check, a dialog choice |
| Suites | None (templates are an unused id list) | A `suite` catalog type and a batch install |
| Both runtimes | LXC path hard-coded; Docker via compose on host | One placement layer over `ContainerBackend` |
| Move across cluster | LXC `migrate_container`; `migrate-service` for `state_paths` | Docker move; a move for a multi-app container; UI |
| Static web apps (ArtCraft) | Only userspace `.taosapp` bundles | Catalog `static` method served from a container |
| Install-time inputs | Not sent by the UI | Dialog renders `requires.inputs` |
| Single source of placement truth | Four state stores, none hold "which container" | `app_containers` plus `install_registry.location_*` |

## 3. Model

An **app container** is the unit of placement and movement:

```
app_containers
  id            "ac-xxxxxx"
  name          user-facing, e.g. "ArtCraft"
  runtime       "lxc" | "docker"
  node          "local" or an Incus remote / worker id
  backend_ref   incus name (taos-app-<id>) or docker compose project
  base_image    e.g. images:debian/bookworm
  limits        {memory, cpu, disk, gpu}
  created_by    "install" | "suite" | "user"
  created_at, updated_at
```

Each installed app gets an `install_registry` row with
`location_kind="container"` and `location_ref=<app container id>`, which puts
the existing unused columns to work. `app_runtime` keeps the proxy target
(container address and port) and is rewritten on move.

**LXC** containers are system containers, so several apps can share one:
each app installs inside with its own method (`pip`, `npm`, `script`,
`static`, `apt`) via `exec_in_container` / `push_file`, under
`/opt/taos-apps/<app_id>` with its own systemd unit.

**Docker** containers are one process tree per image, so "add to an existing
container" maps to adding a service to an existing **compose project** that
shares a network and moves as one. The user still sees one "container". This
is the only honest mapping; installing into a running Docker container's
filesystem would be lost on recreate.

**Runtime choice:** LXC where available (the existing coexistence policy in
`docs/design/lxc-docker-coexistence.md`), Docker where the manifest only ships
an OCI image, or where LXC is not available (macOS, Docker-only hosts).

## 4. Manifest fields

All optional, so existing manifests keep working. Added to `AppManifest` and
`scripts/manifest.schema.json`:

```yaml
install:
  method: static            # new method: a prebuilt web build
  source: https://.../artcraft-paint-1.4.0.tar.gz
  sha256: "..."
  container:
    runtimes: [lxc, docker]   # preference order; omitted = both
    sharing: shareable        # shareable | own (must have its own) | suite-only
    base_image: images:debian/bookworm
    gpu: none                 # none | optional | required
    limits: {memory: 256MiB, cpu: 1}
    state_paths: [/var/lib/artcraft-paint]
    serve: {port: 8080, path_prefix: /paint/}
```

- `sharing: own` covers apps that bind fixed ports, need a different base, or
  run privileged things. The dialog then hides "add to existing".
- `gpu: required` restricts the node list to GPU-capable targets and is the
  hook for "move Blender to the worker with a better GPU".
- `state_paths` already exists for `gitea-lxc` and drives moves.

**Suite manifest**, a new catalog dir `app-catalog/suites/<id>/manifest.yaml`:

```yaml
id: artcraft
type: suite
name: ArtCraft
apps: [artcraft-paint, artcraft-vector, ...]   # each is a normal app manifest
install_modes: [whole, per_app]
default_mode: whole
shared:
  runtime: lxc
  base_image: images:debian/bookworm
  static_server: caddy        # one server, one port, a path per app
```

`_load_catalog` adds `suites` to its directory list. A suite's apps remain
individually installable; the suite is a grouping and a default placement.

### First consumer: ArtCraft

ArtCraft is not in the catalog yet. It is about 17 apps that each ship a
static web build, which is the cleanest case for this design:

- **Whole:** one LXC container (Docker fallback: one `caddy` image with a
  volume) runs one static server; each app is a directory under `/srv/apps/`
  and a `path_prefix`. 17 apps cost one container and one port.
- **Per app:** each selected app gets its own small container, or is added to
  an existing one the user picks.
- The proxy maps `/apps/<app_id>/` to the container address plus the app's
  `path_prefix`, so every app still opens as its own desktop window.
- Updating one app replaces one directory and reloads the server.

## 5. Install dialog UX

Install becomes a modal (`role="dialog"`, `aria-modal`, focus trapped,
Esc closes, focus returns to the Install button). Steps on one screen,
progressive disclosure:

```
Install ArtCraft Paint                                     [x]
------------------------------------------------------------
Where       [ This box (local)            v ]   tier: cpu-only
Container   (o) New container  "paint"            Recommended
            ( ) Add to existing  [ ArtCraft (lxc, local) v ]
                Only compatible containers are listed.
Settings    Admin password   [............]    (requires.inputs)
> Advanced  Runtime [LXC v]  Memory [256 MiB]  CPU [1]
------------------------------------------------------------
                                   [ Cancel ]  [ Install ]
```

For a suite:

```
Install ArtCraft (17 apps)
(o) Whole suite in one container            Recommended
( ) Choose apps   [x] Paint [x] Vector [ ] Pixel ...
      each into: (o) its own container ( ) one "ArtCraft" container
```

Rules:

- "Add to existing" lists only containers on the chosen node whose runtime is
  in the app's `runtimes`, whose sharing allows it, and whose base matches.
  Incompatible ones are shown disabled with the reason, not hidden, so the
  user learns why.
- `gpu: required` filters "Where" to capable nodes, with the GPU named.
- Radio groups use `fieldset`/`legend`; every input has a label; progress
  after Install reuses the existing progress polling with `aria-live`.
- Mobile Store (`MobileStore.tsx`) gets the same steps as a full-screen sheet.

API change: `install-v2` gains an optional `placement` object:

```json
{"app_id": "...", "target_remote": "local",
 "placement": {"mode": "new" | "existing", "container_id": "ac-...",
               "runtime": "lxc" | "docker"},
 "inputs": {"admin_password": "..."}}
```

New read route: `GET /api/store/containers?compatible_with=<app_id>&node=...`.
Suites: `POST /api/store/suites/install {suite_id, mode, apps?, placement}`.
Omitting `placement` means `new`, so old clients keep working.

## 6. Moving containers across the cluster

The container is what moves, with every app inside it.

1. User picks "Move to..." on a container (Store Installed view and the
   Cluster app). Targets are filtered by runtime availability and by the
   strictest `gpu` / tier need of the apps inside, and show why one is better
   (for example "RTX 4090, 24 GB").
2. **LXC:** `migrate_container` (`incus move`, stateless by default, live
   when both ends support CRIU). Already implemented and covered by
   `cluster_migrate.py`.
3. **Docker:** stop the compose project, tar each app's `state_paths` volumes
   (the `service_migrator.py` pattern), recreate the project on the target
   from the same pinned images, restore volumes, start, health-check. On
   failure, restart the source and report; the source is only destroyed
   after the target is healthy.
4. Rewrite `app_runtime` (host, port) and `app_containers.node`; the proxy
   path `/apps/<id>/` does not change, so open windows just reconnect.
5. Moves take a lease on the container so installs into it are refused
   while it moves.

## 7. Host-native tier (later)

An opt-in `install.host_native` block for apps that cannot run in a
container (deep GPU or device access). The app runs on a chosen node as a
normal process inside a Neko-style streaming session, and its window is
streamed over WebRTC exactly as the unified Browser does today. Out of scope
for the phases below except that the data model leaves room for
`location_kind="host"`.

## 8. Phased plan

Each phase is one PR, mergeable alone, with tests and its changelog fragment.

1. **Data model.** `app_containers` store (new SQLite file, wired in
   `app.py`), manifest fields parsed into `AppManifest` and the JSON schema,
   `install_registry` written on install. No behaviour change.
2. **LXC placement executor.** A `ContainerPlacement` service that creates or
   reuses an app container and runs `pip` / `script` / `npm` / `static`
   inside it. `install-v2` uses it when `placement` is sent; host installs
   remain the default until phase 4. Proxy gets `path_prefix`.
3. **Install dialog.** The modal, `requires.inputs`, the compatible
   containers route, keyboard and ARIA tests.
4. **Default flip.** New installs default to container placement; the
   docker path moves to a compose project per app container. Existing host
   installs keep working and are labelled "host".
5. **Suites.** `suite` catalog type, loader, suite install route, whole and
   per-app modes, ArtCraft manifests.
6. **Move.** Container move for LXC (wrapping `migrate_container`) and
   Docker (volume tar), with the UI and target filtering.
7. **Migrate old host installs** into containers, opt-in per app.
8. **Host-native streamed tier** (separate design when picked up).

Hardware checks per phase: Incus on a real host for 2, 4 and 6; a
two-node cluster with a GPU worker for 6.

## 9. Open questions

1. Docker "add to existing" as a shared compose project: acceptable, or
   should Docker apps always get their own container?
2. Default base image for shared LXC app containers: Debian bookworm (as
   `gitea-lxc`) or the agent base image?
3. Should uninstalling the last app in a container destroy it, or keep it
   empty for reuse? Proposal: destroy, with an undo window.
4. Does ArtCraft ship as one upstream archive (whole) or 17 archives? This
   decides whether per-app updates are possible.
5. Who may create shared containers: admins only (as today for installs), or
   any user for their own apps?
6. Should `npm` / `uvx` / `apt` manifests, which have no installer today, be
   container-only from day one?
