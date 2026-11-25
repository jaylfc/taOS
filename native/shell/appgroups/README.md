# taos-appgroups: desktop apps in app groups

An app group is one Incus container plus one grant set. The default group is shown as "My apps"; its container and
profile are named `user-apps`. `taos-appgroups` installs desktop apps into a group from a declarative manifest, puts a
launcher entry on the host and runs the app's window in the person's own desktop session.

    taos-appgroups ensure-default | groups | group create <name> [--display-name TEXT] | catalog | list [--available]
    taos-appgroups install <app-id> [--target group:<name>|host|worker:<name>] [--accept-host-warning]
    taos-appgroups move <app-id> --to group:<name>
    taos-appgroups update <app-id> [--check] | backups | backups delete <app-id> <stamp>
    taos-appgroups remove <app-id> [--purge-data] | run <app-id> [args] | status | launchers sync

Every command takes `--json`. Exit codes: 0 ok, 1 failed, 2 usage, 3 not found, 4 incus unavailable or not allowed,
5 refused (manifest, machine, policy), 6 an install step failed, 7 conflict (also: the app is running), 8 no desktop session,
9 target not available yet (`host` and `worker:` are designed but not built; they never fall back to a group), 10 needs
the person's own confirmation in the Store (a licence to accept, or an update only the publisher's feed vouches for).

## Manifest (`catalog/<id>.json`, kind `container-desktop-app`, schema 1 or 2)

Data only. `recipe` is a list of steps from a fixed allowlist: `apt-source` (https, allowlisted host, pinned key
fingerprint), `apt-install`, `download-tar` and `download-deb` (https, allowlisted host, sha256; a deb also names its
`package`), `symlink`,
`etc-default` (writes `/etc/default/<name>`, plain tokens only: how a vendor package is told not to register its own
source), `suid-helper`. No shell. `grants` may name `network gpu audio documents downloads nesting camera microphone`; camera and
microphone are refused today. `launch.argv` starts with an absolute path; `window.app_id` is what the dock and window
buttons match. Unknown fields, ops or grants make the manifest invalid.

## Grants

Profile `user-apps`: unprivileged, no nesting, root disk, host fonts and system themes read only. Per group, from the
union of its apps: a network device, the render node of the GPU that is present (Intel, AMD or NVIDIA; the lowest `renderD*`, never a card node; no node, no GPU grant and a message), the two
shared folders (`~/Documents for apps`, `~/Downloads for apps`) when asked for, and per session a proxy to the Wayland
socket (and the pulse socket for apps with audio).

## Incus path

If `/var/lib/incus/unix.socket.user` is usable the engine uses it (the person's own restricted project, set up by
`incus-user`); otherwise it uses the admin socket and the default project, and `status` says isolation between people is
not enforced. Listings only show groups created by the calling account.

## Store hook

`native/apps/store/src/containerappsservice.*` runs `taos-appgroups catalog|list|install|remove|run` for catalogue
entries of kind `containerapp`. The Store UI calls `StoreEngine::install/remove/open(id)` as for any other app.

## Tests

`tst_appgroups` drives the real CLI against a fake `incus` (tests/fakeincus.sh): manifest rules, install, refusals
(checksum, key fingerprint, bad manifest), remove keeps data, listings, targets, exit codes. No container is touched.

## Schema 2: licence, data, update

Schema 2 changes `licence` from a string to `{name, url, accept: required|none}`. An engine that only knows schema 1
refuses a schema 2 manifest, so it can never skip the gate. With `accept: required`, `install` refuses (exit 10, the
licence name and URL in the result, and a sentence saying the person must accept it in the Store) until the person's
confirmation arrives; the acceptance is stored in `installed.json` as `licence_accepted` {name, url, accepted_at,
accepted_by}. Schema 1 manifests keep working and need no acceptance.

### The person's confirmation (owner nonce)

No flag, environment value or text on the command line can accept a licence. The Store (the person's UI) shows the
licence, and when the person presses "I agree" it mints a single-use nonce scoped to that action and subject
(`licence.accept` for the app id, `update.unverified` for `<id>@<version>#<sha256>`), valid for two minutes, never cached
(one per press). It hands the nonce to the command in the environment variable `TAOS_OWNER_NONCE`. The engine asks the
Store over a 0600 socket in a 0700 per-user directory (`$XDG_RUNTIME_DIR/taos/`) to use the nonce up; the Store checks the
peer uid with SO_PEERCRED, the engine checks the Store's uid the same way, and the socket has no request that mints.
`accepted_by` is the account the Store runs as, taken from that answer, never from text. Without a valid nonce the
command exits 10 and prints what the person must accept. The same pattern as `native/firmware` (`owner_nonce`). Limit,
as there: a process running as the same user can still reach the socket (or serve its own); keeping an agent away from it
takes a separate uid or a sandbox that hides the directory. `--accept-licence` and `--accepted-by` are accepted on the
command line only so old callers fail with the exit 10 sentence instead of a usage error; they do nothing.

Optional fields: `package` (the apt package left installed; its version is recorded as `installed_version`), `data`
(paths in the app's home that hold its state; absent means the whole home, which is what `move` copies), `update_feed`
(`{format: "vendor-downloads-v1", url}`: the publisher's JSON of current downloads with a SHA-256 each).

## Moving an app between groups

`move` refuses a running app (it looks for processes that run with the app's home) and a destination that already holds
data for the app. It installs the app in the destination without listing it, copies the data container to container with
numeric owners and compares file count and bytes, checks the program is there with all its libraries (`ldd`; whether the
window opens is checked by whoever drives the desktop, not by this Qt Core only engine), and only then switches the index
and the launcher (`X-taOS-AppGroup`). Last, the old data is renamed to `/home/taos/apps/.backup/<id>-<stamp>` in the old
group and the old copy of the app is removed. Any failure before the switch leaves the old group untouched. Backups are
listed by `backups` and removed only by `backups delete`.

## Updates

`update <id> --check` asks the app's apt source (`apt-cache policy`) or the publisher's feed what is current; `update <id>`
installs it, never while the app runs. An apt app upgrades only its own package; the stored signing key is checked again
on every update (exactly one primary key, the pinned fingerprint), as it is when it is added. A feed app downloads the
feed's deb and installs it only if its SHA-256 equals the one in the feed, and: for the version the catalogue pins, the
feed's checksum must equal the catalogue's (a feed that disagrees is refused). For a newer version the checksum comes only
from the same publisher feed as the download, which proves nothing about who wrote the feed: `--check` reports that in
`verification` (what was and was not verified), the Store shows it and the install needs the person's own press
(`update.unverified` confirmation, exit 10 without it). It is recorded in `update_history` with who confirmed.

## Icons

The launcher's icon is the first that exists of: `icons/<id>.override.png|svg` (set with `icon set <id> <file>`, cleared with
`icon clear <id>`), `icons/<id>.png` (a file a person put there), `icons/<id>.pkg.png|svg` (taken from the app's own package
at install, on a move, and on every update: the package's `.desktop` file names it, resolved through hicolor at the largest
size then pixmaps, or the manifest's `icon.container_path`), and last the generic glyph `icons/<id>.svg`. This program only
ever writes the last two. An icon a launcher already points at (a person edited it in by hand) is adopted as the override
the first time the launcher is rewritten, so install, move, update and `launchers sync` never replace it with a worse one,
and removing an app keeps anything a person made. `list` reports the icon in use as `icon_path`; the Store shows that file.

## Launching two apps of one group

Device changes of one container (the Wayland and audio sockets that `run` adds, the grants of `install`) take a per
container lock and retry when Incus answers "ETag doesn't match", so two launches at once both succeed.

## Licence accepted later

`licence accept <id>` records the acceptance of the licence the catalogue now names, for an app installed before licences
were recorded. It installs nothing, and needs the Store's confirmation like `install`.

## Links and sign-in (the URL broker, engine part)

`browser set <app-id>|host [--group G]`, `browser get`, `browser clear [--group G]` choose where web links open: one browser
for everything, or one per group (a work group with its own Chromium keeps work logins apart). `open-url [--from <group>] <url>`
opens a link the way the desktop should: only `http(s)` links (in the chosen browser, in whatever group it lives) and the
custom schemes an installed app registers in its manifest `mime_types` (`antigravity://`, `claude://`: the link goes back to
that app, in its group). Everything else (`file:`, `javascript:`, `data:`, an unregistered scheme, control characters) is
refused with exit 5. When a web link carries `redirect_uri=http://127.0.0.1:PORT/...` and the browser lives in another
group, that one port is forwarded through a listener of this command on the host loopback: the browser's group reaches it, and it
passes on only an HTTP GET for the exact path of the `redirect_uri` (anything else gets a 404 and does not count); the app's
group is reached over a unix socket that only the person's uid can open (the proxy device sets uid and mode 0600). The
browser-side connection comes from the container runtime's proxy under another uid, so it cannot be told from another
local user: what a local user could do there is limited to that one path, once, with the app's own state/PKCE check still
to pass. Everything is removed after the callback was relayed or after `TAOS_APPGROUPS_FORWARD_SECONDS` (default 180).
A move also compares the copy by content (file count, bytes and a SHA-256 over every file's path and content) and holds a
per-app lock from before its running check until the switch, so a launch cannot slip in between. Not built yet: the shim inside each group that sends `xdg-open`,
`$BROWSER` and the portal's OpenURI to `open-url`, and the Store/Settings screens for the choices.
