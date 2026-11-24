# taos-webapp: web apps

A web app is a website in its own browser window with its own private storage and sign-in. Add the same site three
times for three accounts; clone an instance to copy its sign-in. The engine is `taos_webapp_core` (no UI); the Store,
Settings > Apps and this command line are faces over it, so an agent can do everything the windows do.

```text
taos-webapp list [--json]
taos-webapp add --url URL [--name NAME] [--icon PATH|auto] [--from-store ID] [--allow-http] [--browser NAME] [--json]
taos-webapp launch ID
taos-webapp clone ID [--name NAME] [--json]
taos-webapp rename ID NAME
taos-webapp remove ID [--keep-data] [--force]
taos-webapp clear-data ID [--force]
taos-webapp info ID [--json]       size on disk, last used, running, permissions when readable
taos-webapp doctor [--json]        which browser engine is installed
```

Exit status: 0 done, 1 refused or failed (the sentence is on stderr, or `{"error","code"}` with `--json`), 2 usage,
3 no browser engine installed. Addresses must be https (`--allow-http` for http); `file:`, `javascript:`, `data:` and
the rest are refused. Instance ids are generated, never made from typed text.

## On disk (per person, survives updates)

```text
$XDG_DATA_HOME/taos/webapps/index.json            registry, written atomically under a lock
$XDG_DATA_HOME/taos/webapps/<id>/manifest.json    id, name, url, icon, source, browser, created, last_used
$XDG_DATA_HOME/taos/webapps/<id>/icon.png|svg     the site's icon, or a generated monogram
$XDG_DATA_HOME/taos/webapps/<id>/env              optional KEY=VALUE lines added to the browser's environment
$XDG_DATA_HOME/taos/webapps/<id>/profile/         the browser profile: this instance's storage and sign-in
$XDG_DATA_HOME/applications/taos-webapp-<id>.desktop
```

The `.desktop` entry carries `StartupWMClass=chrome-<host>__<path>-<id>` (the id Chromium gives the window, see `expectedAppId`), `Exec=taos-webapp launch <id>` and
`Actions=clone;clear-data;` (desktop actions `Exec=taos-webapp clone <id>` and `taos-webapp clear-data <id>`), so the
dock, launcher and Settings see it like any app and can offer the two actions.

## Browser

One backend today: a Chromium-family browser on `PATH` (`chromium`, `chromium-browser`, `google-chrome`,
`google-chrome-stable`, `brave-browser`, `brave`), launched with `--app=URL --user-data-dir=PROFILE --profile-directory=ID --class=APPID
--name=APPID --ozone-platform=wayland --no-first-run --no-default-browser-check`. `TAOS_WEBAPP_BROWSER` pins one binary,
`TAOS_WEBAPP_OZONE` replaces the platform flag, `TAOS_WEBAPP_NO_NETWORK` skips icon fetching (a monogram is made).
With none installed, `doctor` and `launch` say "No browser engine installed" and name the package (`chromium`).
`BrowserBackend` is the interface for a later native Qt WebEngine window.

Tests: `tests/tst_webapp.cpp` with `tests/fakebrowser.sh` (no network, temporary data folders).
