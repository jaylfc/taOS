# taos-appgroups: desktop apps in app groups

An app group is one Incus container plus one grant set. The default group is shown as "My apps"; its container and
profile are named `user-apps`. `taos-appgroups` installs desktop apps into a group from a declarative manifest, puts a
launcher entry on the host and runs the app's window in the person's own desktop session.

    taos-appgroups ensure-default | groups | group create <name> [--display-name TEXT] | catalog | list [--available]
    taos-appgroups install <app-id> [--target group:<name>|host|worker:<name>] [--accept-host-warning]
                           [--accept-licence <app-id>] [--accepted-by TEXT]
    taos-appgroups move <app-id> --to group:<name>
    taos-appgroups update <app-id> [--check] | backups | backups delete <app-id> <stamp>
    taos-appgroups remove <app-id> [--purge-data] | run <app-id> [args] | status | launchers sync

Every command takes `--json`. Exit codes: 0 ok, 1 failed, 2 usage, 3 not found, 4 incus unavailable or not allowed,
5 refused (manifest, machine, policy), 6 an install step failed, 7 conflict (also: the app is running), 8 no desktop session,
9 target not available yet (`host` and `worker:` are designed but not built; they never fall back to a group), 10 the
app's licence has not been accepted.

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
licence name and URL in the result) until the caller passes `--accept-licence <app-id>`; the acceptance is stored in
`installed.json` as `licence_accepted` {name, url, accepted_at, accepted_by}. The Store shows the licence and passes the
flag only after the person agrees. Schema 1 manifests keep working and need no acceptance.

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
installs it, never while the app runs. An apt app upgrades only its own package. A feed app downloads the feed's deb and
installs it only if its SHA-256 equals the one in the feed. The feed and the checksum come from the same publisher over
HTTPS; a publisher that does not sign them cannot be verified further.
