# Recycle bin runbook

## Host-side Files trash (workspace / agent workspace / project files)

The per-container mechanism below only ever sees files removed *inside* an
agent's own container shell. The Files app also browses three folders that
live on the controller host, not in any container — the user's own
workspace, an agent's workspace (as mounted on the host), and a project's
files folder — and those used to hard-delete on "delete" (#1604).

They now go through a small move-to-trash helper instead
(`tinyagentos/workspace_trash.py`): deleting moves the item under
`<data_dir>/.taos-trash/<scope>/<item-id>/` alongside a JSON metadata
sidecar (original relative path, deleted-at, directory flag); nothing is
unlinked from disk until an explicit purge or empty-trash call. Routes:

- `GET/POST/DELETE /api/workspace/trash[...]` — user workspace
- `GET/POST/DELETE /api/agents/{name}/workspace/trash[...]` — agent workspace
- `GET/POST/DELETE /api/projects/{slug}/trash[...]` — project files

The Files app surfaces the user-workspace trash under Recycle Bin → "My
Files" (restore / delete permanently / empty all). The agent-workspace and
project-files trash routes exist and are tested, but aren't yet wired into
a Files UI section — restore/empty for those two scopes is API-only for now.

## How it works (per container)

Every taOS agent container has a soft-delete recycle bin at
`/var/recycle-bin/` backed by freedesktop.org trash-cli. The default
`/usr/bin/rm` is shadowed by `/usr/local/bin/rm`, which forwards to
`trash-put` — so `rm file.txt` moves `file.txt` into the recycle bin
rather than permanently deleting it.

Items in the recycle bin are automatically purged after 30 days via the
`tinyagentos-recycle-sweep.timer` systemd unit.

## Browsing / restoring / emptying

Inside a container:
- `trash-list` — list trashed items
- `trash-restore` — interactive restore
- `trash-empty` — purge now (bypass the 30-day sweep)

In the taOS UI: Files app → Recycle Bin tab (Phase 1.E — pending).

## Escape hatches

- `/usr/bin/rm file.txt` — permanent delete (no shadow applied)
- `TAOS_TRASH_DISABLE=1 rm file.txt` — single-command permanent delete
- `TAOS_TRASH_DISABLE=1 bash` — entire shell session uses real rm

## What this does NOT cover

- Binaries that call `unlink()` directly rather than shelling out (Layer 2,
  libtrash LD_PRELOAD, deferred to a later phase).
- Deletions via NFS/SMB/S3 from clients outside the container.
- The FS-level snapshot backstop (Layer 3) is configured on the host,
  not per-container — see `docs/design/architecture-pivot-v2.md` §6.3.

## Admin ops

- Force purge ALL agents' bins (host-side): loop over taos-agent-* and
  `incus exec <name> -- trash-empty -f`.
- Change retention (default 30d): edit
  `/usr/local/bin/taos-recycle-sweep` in the container (`-mtime +30`
  → `-mtime +N`) and reload the timer.
- Bypass entire container's recycle bin: `systemctl disable --now
  tinyagentos-recycle-sweep.timer` + `ln -sf /usr/bin/rm /usr/local/bin/rm`.
