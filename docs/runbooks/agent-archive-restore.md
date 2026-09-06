# Runbook: Agent Archive, Restore, and Purge

**Audience:** taOS admins and power users operating via the local API.
**Auth:** all examples use the local token. Adapt if you have a browser session.

---

## Quick reference

| Operation | HTTP call | One-liner |
|---|---|---|
| Archive | `DELETE /api/agents/{slug}` | `curl -s -X DELETE http://127.0.0.1:6969/api/agents/<slug> -H "Authorization: Bearer $(cat data/.auth_local_token)"` |
| Restore | `POST /api/agents/archived/{id}/restore` | `curl -s -X POST http://127.0.0.1:6969/api/agents/archived/<id>/restore -H "Authorization: Bearer $(cat data/.auth_local_token)"` |
| Purge | `DELETE /api/agents/archived/{id}` | `curl -s -X DELETE http://127.0.0.1:6969/api/agents/archived/<id> -H "Authorization: Bearer $(cat data/.auth_local_token)"` |

---

## When to use this

- **Archive a misbehaving agent** — stop it cleanly and preserve history for
  post-mortem, without hard-deleting anything.
- **Free resources without losing history** — archive stops the container and
  frees RAM/CPU. The workspace, memory, and trace data are all preserved.
- **Clone an agent** — archive the original, restore it under a new name
  (slug collision handling gives you `foo-2` automatically), then restore the
  original too if you want both.
- **Roll back a bad deploy** — archive the broken agent and restore from an
  earlier snapshot.

---

## Archive flow

`DELETE /api/agents/{slug}` archives instead of hard-deleting.

What happens:

1. The container is force-stopped.
2. A named incus snapshot is created:
   `incus snapshot create taos-agent-{slug} taos-archive-<YYYYMMDDTHHMMSS>` (UTC).
3. Chat history is exported to
   `{data_dir}/archive/{slug}-<ts>/chat/chat-export.jsonl` on the host.
   This copy is host-owned and survives even if the snapshot is later purged.
4. The agent's LiteLLM key is revoked.
5. The DM channel is flagged archived in the chat store.
6. The config entry moves from `agents` to `archived_agents`, with
   `snapshot_name: taos-archive-<ts>` recorded.

The container (`taos-agent-{slug}`) remains in place and is left stopped.
The snapshot lives in the same incus storage pool as the container.

```bash
curl -s -X DELETE http://127.0.0.1:6969/api/agents/<slug> \
  -H "Authorization: Bearer $(cat data/.auth_local_token)"
```

Expected response on success:

```json
{
  "status": "archived",
  "name": "my-agent",
  "id": "a1b2c3d4e5f6",
  "archived_at": "20260416T143022",
  "snapshot_name": "taos-archive-20260416T143022"
}
```

**Trace data is not included in the snapshot.** Traces live in
`{data_dir}/trace/{slug}/` on the host, bind-mounted to `/root/.taos/trace/`
at run time. They remain on the host after archive and are accessible to the
trace API without entering the container. Pre-archive trace history is fully
preserved.

---

## archive.target options

The `archive.target` key in `data/config.yaml` controls where the snapshot
lives. Default is `pool:`, which keeps the snapshot on the same incus storage
pool as the container. Three forms are accepted:

| Value | Behaviour |
|---|---|
| `pool:` (default) | Snapshot stays on the local storage pool. Zero-copy on btrfs/ZFS. Fastest restore. |
| `path:/abs/path` | After snapshot creation, export a tarball to the given absolute path on the host. Chat export and config entry are still written. Useful for off-pool long-term storage. |
| `s3://bucket` | Logged and skipped today. The snapshot is created on the pool; the S3 export step is a no-op with a warning in the taOS log. Planned for a future release. |

Example override in `data/config.yaml`:

```yaml
archive:
  target: path:/mnt/backup/taos-archives
```

With `path:`, the tarball lands at `/mnt/backup/taos-archives/{slug}-<ts>.tar.gz`.
The in-pool snapshot remains until the agent is purged.

---

## Listing archives

```bash
curl -s http://127.0.0.1:6969/api/agents/archived \
  -H "Authorization: Bearer $(cat data/.auth_local_token)" | jq
```

Each entry contains `id`, `archived_at`, `archived_slug`, `snapshot_name`,
and an `original` snapshot of the agent config at archive time.

---

## Restore flow

```bash
curl -s -X POST \
  http://127.0.0.1:6969/api/agents/archived/<archive_id>/restore \
  -H "Authorization: Bearer $(cat data/.auth_local_token)"
```

Expected response on success:

```json
{
  "status": "restored",
  "id": "a1b2c3d4e5f6",
  "name": "my-agent",
  "display_name": "My Agent",
  "new_llm_key": true
}
```

**What restore does:**

1. Resolves slug collision — if `my-agent` already exists as a live agent,
   the restored agent gets `my-agent-2`, then `my-agent-3`, and so on.
   If a collision is found the container is renamed before the snapshot restore.
2. Restores the snapshot:
   `incus snapshot restore taos-agent-{slug} taos-archive-<ts>`.
   The container comes back with all its state exactly as it was at archive time.
3. Mints a new LiteLLM key (the old one was revoked at archive time).
4. Writes the new key into the container via
   `incus config set taos-agent-{slug} environment.OPENAI_API_KEY=<new_key>`.
5. Restarts openclaw inside the container:
   `incus exec taos-agent-{slug} -- systemctl restart openclaw`.
6. Unflags the DM channel.
7. If a chat export exists at `archive/{slug}-<ts>/chat/chat-export.jsonl`,
   it is re-imported into the chat store.
8. Adds the agent back to `config.agents` with `status: stopped`.

The agent is **not** auto-started. Start it manually after verifying state:

```bash
curl -s -X POST http://127.0.0.1:6969/api/agents/<slug>/start \
  -H "Authorization: Bearer $(cat data/.auth_local_token)"
```

---

## Purge flow

Purge is permanent. There is no undo.

```bash
curl -s -X DELETE \
  http://127.0.0.1:6969/api/agents/archived/<archive_id> \
  -H "Authorization: Bearer $(cat data/.auth_local_token)"
```

Expected response:

```json
{"status": "purged", "id": "a1b2c3d4e5f6"}
```

**What purge destroys:**

- The container and all its snapshots via `incus delete --force taos-agent-{slug}`.
  `incus delete --force` removes the container image and every snapshot
  atomically; no separate snapshot cleanup step is needed.
- The archive directory `data/archive/{slug}-<ts>/` and its contents (chat
  export, any exported tarballs).
- DM channel and its message history.
- The `archived_agents` config entry.

**What purge does not destroy:**

- Trace data at `{data_dir}/trace/{slug}/`. Traces are host-owned and not
  part of the container snapshot. They remain on disk after purge for forensic
  use and must be removed manually if needed.

---

## Legacy archived agents (pre-Phase-2 format)

Agents archived before Phase 2.B was deployed have no `snapshot_name` field
in their `archived_agents` config entry. Attempting to restore them returns:

```
HTTP 500: "This agent was created with a legacy archive path and cannot be
restored via the snapshot path. See the runbook for recovery options."
```

This is a one-time concern — all newly archived agents use the snapshot path.
For existing legacy entries, two options:

**Option A — purge.** If the agent's state is no longer needed:

```bash
curl -s -X DELETE \
  http://127.0.0.1:6969/api/agents/archived/<archive_id> \
  -H "Authorization: Bearer $(cat data/.auth_local_token)"
```

Legacy purge still works: it destroys the old renamed container
(`taos-archived-{slug}-{ts}`) and removes whatever archive directories exist.

**Option B — manual re-snapshot.** If you want to migrate the agent to the
snapshot format, contact the development team for a one-off migration procedure.
The general approach is to re-attach the legacy `agent-home/`, `agent-workspaces/`,
and `agent-memory/` directories as bind mounts on the renamed container, start
it, take a new snapshot, archive under the new format, and clean up. This is
not scripted because the volume of legacy entries is expected to be small.

To identify all legacy entries:

```bash
curl -s http://127.0.0.1:6969/api/agents/archived \
  -H "Authorization: Bearer $(cat data/.auth_local_token)" \
  | jq '.[] | select(.snapshot_name == null) | {id, archived_slug, archived_at}'
```

---

## Troubleshooting

**Archive returns 500 "snapshot create failed".**
The container may still be running or in an error state. Check:

```bash
incus list taos-agent-<slug>
incus stop taos-agent-<slug> --force
```

Then retry the archive call. If the container does not exist, the archive
still proceeds (snapshot step is skipped; chat export and config move still
happen).

**Archive returns 500 "snapshot name conflict".**
A snapshot named `taos-archive-<ts>` already exists on this container from a
prior run within the same UTC second. This is rare. List existing snapshots:

```bash
incus info taos-agent-<slug>
```

If a stale `taos-archive-*` snapshot is present from a failed prior attempt,
delete it with `incus snapshot delete taos-agent-<slug> <snapshot-name>` and
retry.

**Restore returns 500 "snapshot not found".**
The `snapshot_name` recorded in `config.yaml` does not exist on the container.
Possible causes: the snapshot was deleted manually, or the container was
destroyed and recreated outside taOS. Check:

```bash
incus info taos-agent-<slug>
```

If the snapshot is absent and no tarball export exists, the agent state is
unrecoverable. Purge the archive entry to clean up config.

**Restore returns 500 "rename collision".**
A container named `taos-agent-{final_slug}` already exists. The slug
resolution loop should prevent this; if it happens it indicates a race or
a manually-created container. Identify and remove the conflicting container
with `incus list`, then retry.

**Restore succeeds but agent stays at `status: stopped`.**
Expected. Always start the agent explicitly after restore; auto-start would
surprise users who are inspecting state before trusting the restored container.

**Env rewrite failed (set_env returns error).**
The container must be stopped but present for `incus config set environment.*`
to succeed. If the container is in an unexpected state after snapshot restore,
check `incus list` and force-stop if needed, then re-run the restore call.
The restore endpoint is idempotent on the env-rewrite step if the snapshot is
already restored.

**systemctl restart openclaw failed inside container.**
The openclaw service may not be installed (e.g. a non-openclaw framework agent).
This is a soft failure — the env var is set correctly via `incus config`; the
service unit will pick it up on next start. For openclaw agents, run:

```bash
incus exec taos-agent-<slug> -- systemctl status openclaw
```

If the unit is failed, inspect `incus exec taos-agent-<slug> -- journalctl -u openclaw -n 50`.

---

## Related

- `docs/design/framework-agnostic-runtime.md` — "Agent archive / restore"
  section for the design rationale; "Why the pivot" section for the full
  reasoning behind snapshot-based archives
- `docs/design/architecture-pivot-v2.md` — full decision record; §3 covers the
  snapshot archive and restore flows; §8 covers the evolved thesis
- `tinyagentos/routes/agents.py` — `_archive_agent_fully`,
  `restore_archived_agent`, `purge_archived_agent`
- `tinyagentos/containers/__init__.py` — `snapshot_create`, `snapshot_restore`,
  `set_env`
