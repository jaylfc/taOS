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
  backend_ref   incus name (taos-ac-<container id>) or docker compose project
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

### Userspace container apps stay separate

Userspace `.taosapp` apps with a backend run as `taos-app-<id>` through
`tinyagentos/userspace/container_deploy.py`, which by its own docstring is
"SEPARATE from the agent deployer". They are Docker only, bound to
`127.0.0.1`, capped at a fixed 512m / 1 CPU, tracked in `UserspaceAppStore`
(`tinyagentos/userspace/store.py`) and reached through
`/api/userspace-apps/<id>/proxy` under the bundle CSP and provenance badge,
not through `/apps/<id>/`.

They do **not** fold into `app_containers` in phases 1 to 7. They are
untrusted third-party code with a permissions broker and a provenance model,
one image per app, never shared and never placed on another node; catalog
app containers are admin-installed, shareable and movable. Merging them
would either loosen the userspace sandbox or drag its constraints onto
catalog apps. Two consequences for this design:

- App container names use `taos-ac-<container id>`, never `taos-app-`, so the
  two systems cannot collide on a container name. Keying on the container id
  (not an app id) also fits a container that holds several apps.
- The shared piece is the low-level `ContainerBackend` primitive, as today.
  Whether userspace apps later become `app_containers` rows with
  `created_by: "userspace"` (to gain moves) is left as an open question.

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

### Serving web builds: CSP, sandbox and origin

Static web builds such as ArtCraft hit three blockers in today's serving
paths:

- **No `wasm-unsafe-eval`.** The userspace bundle CSP (`_bundle_csp` in
  `tinyagentos/routes/userspace_apps.py`) allows
  `script-src 'self' 'unsafe-inline' blob:` only. `/apps/<id>/` responses
  that carry no CSP of their own get the controller default from
  `tinyagentos/middleware/security_headers.py` (`setdefault`,
  `script-src 'self'`). Neither lets a page compile WebAssembly.
- **No `allow-downloads`.** The userspace window
  (`computeSandboxAttr` in `desktop/src/apps/SandboxedAppWindow.tsx`)
  sandboxes to `allow-scripts` only, and the bundle CSP `sandbox` directive
  adds just `allow-forms allow-popups`. So an export or "save image" in a
  userspace build cannot download. The service window
  (`desktop/src/apps/ServiceAppWindow.tsx`) does grant `allow-downloads`,
  but only alongside the next problem.
- **`/apps/<id>/` is same-origin with the shell.** `ServiceAppWindow.tsx`
  frames it with `allow-same-origin`, and `tinyagentos/routes/service_proxy.py`
  serves it on the controller origin. The proxy strips taOS cookies on the
  way to the upstream, but in the browser the framed page is the shell's
  origin: its scripts can call `/api/...` with the user's session. Adding
  `wasm-unsafe-eval` there would loosen the shell's own policy.

The fix reuses the pattern already shipped for the Browser:
`tinyagentos/browser_proxy_origin.py` runs a second, API-free ASGI app on
its own port (`browser_proxy_port`, default 6970), authenticated by a
short-lived ticket redeemed for an origin-scoped cookie, and
`security_headers.py` adds that origin to `frame-src`.

**Phase 2: an apps origin.** Container-placed apps are served from a new
API-free apps origin (config `server.apps_origin_port`, a sibling of
`browser_proxy_port`) instead of the controller origin. It exposes only
the `/apps/<id>/` proxy and a ticket redeem endpoint, mirroring
`browser_proxy_origin.py`; the main app mints the ticket when a window
opens. `security_headers.py` adds the apps origin to `frame-src`. On the
apps origin the proxy passes the upstream app's own CSP through; when the
upstream sends none, it sets a baseline (`default-src 'self'`,
`script-src 'self'`, `frame-ancestors` the shell origin) instead of the
shell's policy. `allow-same-origin` stays on the iframe, which is now safe
because that origin holds no taOS API and no `taos_session` cookie.
Legacy host installs keep today's same-origin `/apps/<id>/` path until
they are migrated (phase 7).

**Phase 3: per-container origin, WebAssembly and downloads.**

- **Origin per app container.** Each app container gets its own origin:
  by default its own port on the apps listener, allocated from a reserved
  range like other host ports (`tinyagentos/installers/port_allocator.py`);
  a subdomain `<container>.<apps domain>` when the owner configures a
  wildcard name and certificate. The boundary is the container, not the
  app: apps that share a container already share a filesystem and process
  space, so separate origins between them would add nothing. "Own
  container" in the dialog therefore also means "own origin".
- **WebAssembly is opt-in.** The proxy adds `'wasm-unsafe-eval'` to
  `script-src` only when the app's manifest declares it and the app is on
  an isolated per-container origin. It is never added to the shell CSP,
  the userspace bundle CSP, or a shared apps origin.
- **Downloads are opt-in.** The iframe gets `allow-downloads` only when the
  manifest declares it; the install dialog lists both grants ("Runs
  WebAssembly", "Can save files to your computer") next to the install
  button.

```yaml
install:
  container:
    serve:
      port: 8080
      path_prefix: /paint/
      web: {wasm: true, downloads: true}   # both default false
```

Out of scope here: userspace `.taosapp` bundles keep their current CSP and
sandbox (see "Userspace container apps stay separate").

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
 "placement": {"mode": "new" | "existing" | "host", "container_id": "ac-...",
               "runtime": "lxc" | "docker"},
 "inputs": {"admin_password": "..."}}
```

New read route: `GET /api/store/containers?compatible_with=<app_id>&node=...`.
Suites: `POST /api/store/suites/install {suite_id, mode, apps?, placement}`.
Omitting `placement` is transitional: until phase 4 it means a host (legacy)
install exactly as today, so old clients keep working and nothing changes
for them while phases 2 and 3 land. From phase 4 (default flip) an omitted
`placement` means `{"mode": "new"}`; a client that still wants a host
install must then send `{"mode": "host"}`.

`target_remote` with a `placement` creates the container **on** that node,
it never relabels. Today only the `lxc` path does this
(`tinyagentos/installers/lxc_installer.py` addresses `<remote>:<name>` on an
Incus remote); for `docker` / `pip` / `script`, `_legacy_install` in
`tinyagentos/routes/store_install.py` runs the installer on the controller
and only records `_resolve_host(target_remote)` as `runtime_host`. Remote
creation lands as follows:

- **Phase 2, LXC:** `ContainerPlacement` creates and execs in the container
  through the Incus remote (`<remote>:taos-ac-<id>`), the same pattern
  `lxc_installer.py` and `tinyagentos/cluster/service_migrator.py` already
  use. Remote LXC placement works from the first container phase.
- **Phase 6, Docker:** the worker has no generic container API (its deploy
  endpoint, `tinyagentos/worker/deploy.py`, is a fixed command allowlist), so
  remote Docker needs a new authenticated worker endpoint that applies a
  compose project from a pinned spec. It lands with Move, which needs the
  same "recreate this project on node X" step. Until then a Docker
  `placement` with a non-local `target_remote` is refused with a 400 and the
  dialog only offers nodes where the chosen runtime can be created.
- The legacy relabel stays for omitted `placement` until phase 4, and is
  then reached only by an explicit host install.

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
   inside it, locally or on an Incus remote. `install-v2` uses it when
   `placement` is sent; an omitted `placement` stays a host install until
   phase 4. Proxy gets `path_prefix`. The API-free apps origin serves
   container-placed apps.
3. **Install dialog.** The modal, `requires.inputs`, the compatible
   containers route, keyboard and ARIA tests. Per-container origins, and the
   manifest-gated `wasm-unsafe-eval` and `allow-downloads` grants shown in
   the dialog.
4. **Default flip.** An omitted `placement` now means `new`; host installs
   need `{"mode": "host"}`. The docker path moves to a compose project per
   app container (local node only). Existing host
   installs keep working and are labelled "host".
5. **Suites.** `suite` catalog type, loader, suite install route, whole and
   per-app modes, ArtCraft manifests.
6. **Move.** Container move for LXC (wrapping `migrate_container`) and
   Docker (volume tar), with the UI and target filtering. Adds the worker
   endpoint that creates a compose project on a remote node, which also
   enables Docker placement on a remote at install time.
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
7. Should legacy host installs on `/apps/<id>/` lose `allow-same-origin`
   before phase 7 migrates them, at the cost of breaking apps that rely on
   cookies or storage?
8. Should userspace `.taosapp` container apps later become `app_containers`
   rows (`created_by: "userspace"`) so they can move, or stay separate for
   good?
9. Per-container origins by port by default: acceptable port usage on a
   box with many containers, or should the apps origin require a wildcard
   subdomain?
