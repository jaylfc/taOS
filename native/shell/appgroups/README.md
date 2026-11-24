# taos-appgroups: desktop apps in app groups

An app group is one Incus container plus one grant set. The default group is shown as "My apps"; its container and
profile are named `user-apps`. `taos-appgroups` installs desktop apps into a group from a declarative manifest, puts a
launcher entry on the host and runs the app's window in the person's own desktop session.

    taos-appgroups ensure-default | groups | group create <name> | catalog | list [--available]
    taos-appgroups install <app-id> [--target group:<name>|host|worker:<name>] [--accept-host-warning]
    taos-appgroups remove <app-id> [--purge-data] | run <app-id> [args] | status | launchers sync

Every command takes `--json`. Exit codes: 0 ok, 1 failed, 2 usage, 3 not found, 4 incus unavailable or not allowed,
5 refused (manifest, machine, policy), 6 an install step failed, 7 conflict, 8 no desktop session, 9 target not available
yet (`host` and `worker:` are designed but not built; they never fall back to a group).

## Manifest (`catalog/<id>.json`, kind `container-desktop-app`, schema 1)

Data only. `recipe` is a list of steps from a fixed allowlist: `apt-source` (https, allowlisted host, pinned key
fingerprint), `apt-install`, `download-tar` and `download-deb` (https, allowlisted host, sha256), `symlink`,
`suid-helper`. No shell. `grants` may name `network gpu audio documents downloads nesting camera microphone`; camera and
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
