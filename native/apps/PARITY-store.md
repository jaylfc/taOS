# Parity: native Store, web apps engine, Settings > Apps

Source of truth: the web Store at `origin/dev` (`88fe9372f`). File:line references are into that tree.
Web: `desktop/src/apps/StoreApp/index.tsx` (`I`), `types.ts`, `filter.ts`, `PermissionConsent.tsx`,
`LicenseAcceptDialog.tsx`, `TaosAppsSection.tsx`, `StudiosView.tsx`, `ImportAppButton.tsx`.
Server: `tinyagentos/routes/store.py` (`S`), `store_install.py` (`SI`), `models.py` (`M`), `apps.py` (`A`),
`themes.py` (`T`), `userspace_apps.py` (`U`), `tinyagentos/registry.py` (`R`). Catalogue: `app-catalog/` (about 270
manifests: `agents/` 27, `models/` 119, `services/` 80, `plugins/` 47, `mcp-registry/` 6, `streaming/` 6, `templates/` 3).
Code: `native/apps/store/` (app and engine), `native/shell/webapp/` (web apps engine and `taos-webapp`),
`native/apps/settings/qml/AppsPage.qml` (Settings > Apps).

Status: **first cut** (this commit), **later**, **n/a** (nothing native to build, or no web feature behind it).
"Who" is what the handler checks: `admin` is `require_admin` (a member gets 403). The native Store never assumes an
account may act: every mutation can come back 403 and the window shows the controller's sentence plus what to do.

## What the web Store really has

Sections (`I:52-62`): Discover, Apps, Studios, Agents, Models, Services, MCP Servers, Dev Tools, Community, and the
Library group Installed, Updates. Cards install through `POST /api/store/install-v2` (`I:434`), uninstall through
`POST /api/store/uninstall` (`I:459`), and poll `GET /api/store/install-progress/by-app/{id}` every 1.5 s (`I:402`).
Consent steps in the web app: the **licence gate** (HTTP 412 `needs_license_acceptance`, `SI:1035,1046`, dialog
`LicenseAcceptDialog.tsx`) and, for userspace apps, **permission consent** (`PermissionConsent.tsx`,
`POST /api/userspace-apps/{id}/permissions`, `U:374`). The web Store has **no Docker consent step** and no
"web app" kind. The native sections follow the task brief (Discover, Apps, Web apps, Models, Themes, Plugins,
Installed, Updates); Agents, Services, Dev Tools and MCP map into Apps (by category chip) and Plugins.

## Catalogue, search, detail

| Web feature | Route / code | Who | Status | Notes |
|---|---|---|---|---|
| Catalogue list (id, name, type, category, version, description, icon, licence, weights licence and class, requires, hardware tiers, install method, installed, state, variants, repo, stars, popularity, update fields) | `GET /api/store/catalog` `S:224` (`?type=`) | any session | first cut | normalised in `store/src/storecatalog.cpp`; unknown fields ignored; an optional `runtime: lxc/docker/either` is read when present and tolerated when absent |
| Installed ids, runtime host and port | `GET /api/store/installed-v2` `SI:1312` | any session | first cut | joins the catalogue flags; Open uses `runtime_host:runtime_port` |
| Hardware profile (for fit) | `GET /api/hardware` `S:360` (`profile_id`) | any session | first cut | fit of an app is decided like `_build_app_items` (`S:172`): no tier = unsupported, full/optimal/dict = fits, other = degraded |
| App detail (homepage, requires, install, capabilities, config schema, state) | `GET /api/store/app/{id}` `S:341` | any session | first cut | "What it needs" rows: memory, storage, runs in (Docker, LXC, pip, script), this device, licence, weights licence, number of settings |
| Screenshots | no catalogue field | | n/a | the catalogue has `cover` only in the web file `I` (a hand-written map), no screenshots; the detail page has none until a manifest field exists |
| Type filter, category filter | `?type=` `S:225`, chips `I` | | first cut | category chips with counts per section |
| Search | client side `filter.ts` | | first cut | all words must match; name, id, category, description; best match first |
| Popularity / stars | `GET /api/store/popularity` `S:312` | any session | first cut (flag in list) | Discover orders "Popular apps" by `stars` |
| Device and backend filters (pill bars) | `DevicePillBar.tsx`, `BackendPillBar.tsx`, `GET /api/cluster/install-targets` `I:1114` | any session | later | native installs always target `local` for now |
| Incompatible toggle | `IncompatibleToggle.tsx` | | later | native marks fit on every row instead |
| Studios view | `StudiosView.tsx`, `/api/apps/optional/{id}/install` `A:300` | admin | later | optional apps |
| Community view, store submissions | `routes/store_submissions.py` | | later | |
| Mobile store | `MobileStore.tsx` | | n/a | native has a narrow layout instead |
| Import `.taosapp` package | `ImportAppButton.tsx`, `POST /api/userspace-apps/install` `U:139` | | later | |

## Install, remove, progress

| Web feature | Route | Who | Status | Notes |
|---|---|---|---|---|
| Install (any kind) | `POST /api/store/install-v2` `SI:818` `{app_id, target_remote, variant_id?, accepted?}` | admin | first cut, behind the Install button | 403 shown as "needs an administrator account", with the controller's words; 400/404/409/422/500 show the controller's `error` |
| Licence gate (non-commercial weights) | 412 `needs_license_acceptance` `SI:1035,1046`; re-POST with `accepted: true` | admin | first cut | native shows the same sentence the server composes (`SI:1039`) built from `weights_license` and the name; Accept re-sends with `accepted` |
| Docker needed (native addition) | manifest `install.method: docker` or `runtime: docker` (catalogue `install_method`) | | first cut (native addition) | web has no such step. Native marks **Needs Docker** and asks once before anything is sent ("Install with Docker"); `runtime: either` does not ask; a manifest with no `runtime` falls back to the install method |
| Install progress | `GET /api/store/install-progress/by-app/{id}` `SI:789` | any session | first cut | polled every 1.5 s while installing; if the POST times out client side (30 s default) the progress route decides the end |
| Variant choice for models | `variants[]` in the catalogue, `variant_id` in the POST | admin | first cut | "Best for this device (automatic)" or one variant with its size and fit |
| Uninstall | `POST /api/store/uninstall` `S:531` `{app_id}` | admin | first cut | 404 = "not installed" in words; also `uninstall-v2` `SI:1235` (not used, the web UI uses the old route) |
| Signature gate | 403 with `detail` `SI:930` | | first cut | shown as the controller's sentence |
| Agent framework image prefetch status | `GET /api/agent-image/status` `I:378` | | later | |
| Update an app | `update_available`, `upstream_*` fields `S:104`; the web Updates tab | | later | Updates lists installed apps with a newer version; no Update button yet |
| Open | `taos:open-app` event `I:489` / runtime host and port | | first cut | agent frameworks start `taos-agents`; services open `http://host:port`; others say there is no window |

## Models

| Web feature | Route | Who | Status | Notes |
|---|---|---|---|---|
| Models list, per variant compatibility and downloaded flag, profile id | `GET /api/models` `M:328` | any session | first cut | "fits this device": green "Runs well", yellow "May run slowly", red "Does not fit" from the controller's `compatibility` (list and per variant) |
| Download / install a model | the Store card uses `install-v2` (`I:434`); the Models app uses `POST /api/models/download` `M:369` | admin | first cut (install-v2, as the Store card does) | `download` + `downloads/{id}` `M:524,534` are for the Models app, not the Store |
| Recommended models, loaded models, search (Hugging Face, Ollama), pull, delete | `M:756,824,597,608,686,945` | | n/a for the Store | Models app territory |

## Themes

| Web feature | Route | Status | Notes |
|---|---|---|---|
| Installed themes list | `GET /api/themes` `T:34` (server themes) | n/a | native themes are folders read by the theme engine (`native/shell/common/THEMES.md`): `~/.local/share/taos/themes`, `/usr/share/taos/themes`, the three shipped ones |
| Apply | the native theme engine (`ThemeManager::setTheme`, writes `appearance.json`) | first cut | same engine and file as Settings > Appearance; every taOS window recolours live |
| Install a theme package | `POST /api/themes/install` `T:38`, remove `T:58` | later | store themes (download a `.taostheme`) come later |

## Web apps (new: no web Store feature)

| Feature | Where | Status |
|---|---|---|
| Built-in web app catalogue (YouTube, YouTube Music, WhatsApp Web, Telegram Web, Discord, X, GitHub, Google Maps) | `native/apps/store/catalog/webapps.json`, merged with the controller's entries; a controller entry of type `webapp` with the same id wins | first cut |
| Add from the catalogue, "Add any website" dialog (live address check, optional name, icon) | Store > Web apps, `taos-webapp add` | first cut |
| Your web apps: Open, Rename, Clone (keeps the sign-in), Clear data, Remove, storage used, last used | Store > Web apps and Settings > Apps | first cut |
| Native WebEngine window backend | interface `BrowserBackend` only | later |
| Controller-side catalogue entries | see "Proposed server change" | proposal |

## Settings > Apps

Native apps (launcher entries that carry `X-taOS-AppId`), container apps (read only, from
`GET /api/store/installed-v2` `SI:1312` joined with `GET /api/store/catalog`; Manage opens `taos-store --app <id>`),
and web app instances with storage used, last used, Open, Rename, Clone, Clear data, Remove. Status: **first cut**.
Container app removal stays in the Store (an admin action).

## Web apps engine: facts to know

* Data per person, outside the OS, kept over updates: `~/.local/share/taos/webapps/index.json` (atomic, locked),
  `<id>/{manifest.json, icon.png|svg, env, profile/}`. Every instance has its own `profile/` (own storage and sign-in).
* One launcher entry per instance, `~/.local/share/applications/taos-webapp-<id>.desktop`: `Name`, `Icon` (absolute
  path), `Exec=taos-webapp launch <id>`, `StartupWMClass=taos-webapp-<id>`, `X-taOS-AppId`, `X-taOS-WebApp=<id>`, and
  `Actions=clone;clear-data;` with `[Desktop Action clone]` (`Exec=taos-webapp clone <id>`) and
  `[Desktop Action clear-data]` (`Exec=taos-webapp clear-data <id>`). The dock and launcher can offer both actions
  (freedesktop Desktop Entry spec 1.5 section "Additional applications actions"); `clear-data` refuses while the
  window is open and the CLI prints why.
* Launch line: `<browser> --app=<url> --user-data-dir=<profile> --class=<app id> --name=<app id>
  --ozone-platform=wayland --no-first-run --no-default-browser-check`, plus the instance's `env` file (`KEY=VALUE`
  lines; the lead pins the GPU there). `TAOS_WEBAPP_OZONE=x11|auto` overrides the platform flag.
* Window id: MEASURED on the real machine, Chromium ignores `--class` on Wayland and names a `--app=` window
  `chrome-<host>__<path with / as _>-<profile directory name>` (`https://www.youtube.com` in Default gives
  `chrome-www.youtube.com__-Default`). The engine therefore launches with `--profile-directory=<instance id>` (new
  instances and clones; legacy instances record `Default`), computes the id with `expectedAppId(url, profile)` and
  writes it as `StartupWMClass` (and `X-taOS-AppId`). Launchers of older instances are rewritten by
  `Engine::syncLaunchers()` (on launch, `list`, `doctor`, `info` and the Store's scan). Two legacy instances of one
  site share an id: the second refuses to start while the first is open and says to use Clone.
  Clone renames the copied profile directory to the new id and patches `Local State` (`info_cache`, `last_used`,
  `last_active_profiles`, `profiles_order`); that Chromium accepts it is not yet verified with a real browser.
  Only the host, path (trailing slash kept), and the characters Chromium replaces take part; a path with unusual
  characters (percent-escapes) is as `GURL` holds it and is unverified.
* Title bar: the compositor draws only a 1 px border, so an `--app` window has none. Each instance's profile gets
  `browser.custom_chrome_frame: true` (Chromium's "Use system title bar and borders" off; pref name verified in
  `chrome/common/pref_names.h`, registered per profile in `browser_view_prefs.cc`, so the file is
  `<user-data-dir>/<profile dir>/Preferences`). There is no command-line switch for it (the old flag was removed in
  favour of the pref). `Engine::ensureOwnTitleBar` merges the key without losing others; it runs on add, on clone
  and before every launch (repairing legacy `Default` instances), and refuses while the instance is running.
  Button side and theme follow the GTK settings the session hook writes; that Chromium's custom frame reads
  `org.gnome.desktop.wm.preferences button-layout` is NOT verified (no source found), nor that this window honours
  the pref under swayfx. Minimise: sway ignores a client's minimise request, so that button does nothing until the
  compositor side is handled; the engine never depends on it.
* The browser is started with a cleaned environment: `LD_LIBRARY_PATH`, `QT_*` and `QML*` are dropped; the GPU
  variables (EGL, Mesa) are kept.
* Running state: Chromium's `SingletonLock` symlink (`host-pid`, pid alive) or the launcher's pid file. Remove, Clear data
  and Clone refuse while an instance runs (Remove and Clear data accept `--force`, which sends SIGTERM first).

## Proposed server catalogue change (for the lead to card; not made on this branch)

The registry loads manifests from `agents/ models/ services/ plugins/` only (`R:311`) and `AppManifest` ignores
unknown fields (`R:86`). Minimal change to publish web apps from the server catalogue:

1. New directory `app-catalog/webapps/<id>/manifest.yaml`; add `"webapps"` to the directory tuple at `R:311`.
2. `AppManifest` gets `url: str = ""` and `categories: list[str] = []`; `type` documents the new value `webapp`
   (today: agent-framework | model | service | plugin).
3. `GET /api/store/catalog` (`S:224`) adds `"url": a.url` and `"categories": a.categories` to each entry (`_entry`, `S:283`).
4. `store_install.install_app` must reject `type: webapp` with a 400 "web apps are added on the client" (a web app has
   no server side install; each client creates an instance).
5. Schema:

```yaml
id: youtube
name: YouTube
type: webapp
category: media
version: "1"
description: "Watch and upload videos"
homepage: https://www.youtube.com
url: https://www.youtube.com
icon: youtube
categories: [media, video]
install:
  method: webapp
```

The native Store already reads `type: webapp` entries (with `url`, `categories`) and lets them replace the built-in
entry of the same id. The web Store can ignore the type until it learns it.

## What a human must check on a real display

1. Install `chromium` (Debian trixie: `apt install chromium`; the engine also finds `chromium-browser`,
   `google-chrome`, `google-chrome-stable`, `brave-browser`, `brave`), add YouTube from the Store, press Open: a
   chromeless window opens, signed out, and after you sign in, closing and reopening keeps the sign-in.
2. In sway: `swaymsg -t get_tree` shows `app_id: taos-webapp-<id>` for that window (the `--class` question above).
   If it shows another id, the dock cannot match the window to its launcher entry; tell me and the
   fix is a window rule or a different flag.
3. Add YouTube twice, sign in with two accounts, Clone one: the clone opens already signed in (Chromium encrypts
   cookies with a key from the keyring; the clone shares the browser's key, so it should work. It is the one thing
   the fake browser cannot prove).
4. The dock shows each instance with its own name and icon, and right-click offers Clone and Clear data (the dock
   agent's part; the entries are ready).
5. Fonts (Inter), 150 to 250 ms motion, reduce-motion, keyboard use, screen reader names, on a real compositor.
