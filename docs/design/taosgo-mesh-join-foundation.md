# taOSgo mesh-join foundation (design spec)

Status: DRAFT for Jay review. Unblocks Web Studio publish (#167) and, more
broadly, off-LAN remote access (taOSgo). Author: taOS-dev.

## Why this exists

Web Studio v1 is a complete static-site builder (generate -> edit -> preview ->
install/export). Its one missing feature, publish to `<subdomain>.taos.my`
is blocked, and so is off-LAN remote access to the desktop. The block is not a
small wiring gap: the taOS **controller has no mesh-join code at all**. There is
no `tailscale up`, no preauth-key consumption, no Headscale connection, and no
persistence of the per-host service credentials taos.my hands back at join time.

taos.my's side is DONE and deployed: the cluster-join ready payload emits a
`headscale_preauth_key`, a `controller_token` (scope `taosnet:passkey`), and a
`sites_token` (scope `sites:publish`) -- each single-scope and host-bound -- and
the routing endpoints (`/api/relay/authorize-site`, `/api/sites/*`) are live and
fail-closed. What is missing is entirely on the taOS controller side.

## Current state (verified in code)

- `routes/account_proxy.py` is a thin cookie-passthrough proxy: it forwards the
  browser's join request / approve / deny / poll to taos.my `/api/cluster/join/*`.
  The **ready payload lands in the browser**, not on the controller.
- `taosnet/passkey_client.py::get_controller_token()` is a READER seam: it reads
  `TAOS_CONTROLLER_TOKEN` from the env and its own docstring says it is "the seam
  the cluster-join client writes to once that client lands." That writer does not
  exist.
- No code anywhere consumes the `headscale_preauth_key` or brings up a tailnet
  interface. The Pi's current LAN clustering uses a different, non-Headscale path.

So three things are missing, in dependency order: (1) a server-side way to obtain
+ persist the ready-payload credentials; (2) an actual headless mesh-join; (3)
the publish caller + a server for the published static site.

## Design

### Unit 1 - credential store + persistence (the `get_controller_token` writer)

A small host-local credential store, `taosnet/mesh_credentials.py`, persisting the
ready-payload service credentials to a single file under the data dir
(`<data_dir>/mesh/credentials.json`, mode 0600, dir 0700). Fields:
`{account_id, host_id, tailnet_name, controller_token, sites_token, joined_at}`.
The preauth key is consumed immediately by Unit 2 and NOT persisted (it is
single-use). Public API:

- `save_mesh_credentials(payload: dict) -> None` (atomic write; validates the
  expected keys; refuses to widen scope silently)
- `get_controller_token() -> str | None` and `get_sites_token() -> str | None`
  (replace the env-var placeholder in `passkey_client.py`; keep the env var as a
  dev override that wins if set, so existing tests + local dev keep working)
- `get_host_id() -> str | None`, `clear()` (on leave/unjoin)

**How the controller obtains the payload - two options:**

- **(A) Poll-intercept (recommended).** `cluster_join_poll` in `account_proxy.py`
  already forwards the poll and receives the ready payload on its way to the
  browser. When the forwarded response is a `ready` with the credentials, extract
  and `save_mesh_credentials(...)` server-side *before* returning to the browser,
  and strip the raw `controller_token`/`sites_token` from the browser-facing body
  (the browser only needs join status + whatever the UI shows; the service tokens
  must not sit in browser JS). Minimal new surface, reuses the existing proxy, and
  keeps the credentials server-side by construction.
- (B) Explicit `POST /api/account/cluster/join/finalize` the browser calls after
  approval, which makes the controller do its own authenticated poll + persist.
  More moving parts, another endpoint to auth. Rejected unless (A) proves awkward.

Decision: **A.** Note the one subtlety to verify at build time: confirm the poll
response shape so the intercept keys off `ready` state correctly and never
double-consumes a single-use preauth key (idempotent: if credentials already
persisted for this host_id, skip re-save).

### Unit 2 - headless Headscale mesh-join (the open infra decision)

This is the real fork and the reason this is a spec, not a straight build. The
controller must bring up a tailnet interface using the one-shot
`headscale_preauth_key` against the account's Headscale control server
(`hs.taos.my`). Options, with tradeoffs:

- **(2a) System `tailscale` + `tailscaled`.** `tailscale up --login-server
  https://hs.taos.my --authkey <preauth> --hostname <handle-host>`. Simplest and
  most robust (real kernel networking, standard tooling), but adds a system
  dependency + a privileged daemon, and the install/upgrade path must ship it per
  platform (Pi/Debian, Fedora, macOS). Best for a device that IS the always-on
  host.
- **(2b) `tsnet` (userspace Tailscale, in-process).** taOS embeds a userspace
  tailnet in the controller process (Go `tsnet`, or a Python binding / sidecar).
  No system daemon, no root, self-contained. But taOS is Python; this means a Go
  sidecar or a userspace-WireGuard lib, and userspace perf/reliability is lower.
  The website side already anticipates a `tsnet` menubar app for clients (bus
  discussion), so there is precedent for tsnet on the CLIENT; the QUESTION is
  whether the always-on HOST should also be userspace or system.
- (2c) Reuse whatever the streamed-browser / Neko work uses for its tailnet, if
  it already brings up a tailnet on the Pi. To verify at build time.

**Recommendation: 2a (system tailscale) for the always-on host**, because the
published site + remote desktop need a stable, performant, boot-persistent mesh
membership, and the host is a managed device where installing a package + service
is acceptable (it already manages systemd units per the backend-service work).
Keep 2b (tsnet) for the light clients. But this is Jay's call - it defines a new
per-platform install dependency, so it belongs in the installer story. If 2a: the
join sequence is `mesh_up(preauth, hostname)` -> shell out to `tailscale up`
(fail-soft, structured error), then record membership; on reboot, tailscaled
rejoins automatically and Unit 1's persisted credentials remain valid.

Health/observability: a `mesh_status()` that reports `{joined, tailnet_name,
node_ip}` for the Account pane and the routing (the published_sites `host_id` maps
to this node in taos.my's hosts table).

### Unit 3 - publish caller + static-site server

Once Units 1+2 land, publishing a Web Studio site is:

1. **Serve the static export.** The controller serves the site's already-built
   static bundle (the same `/api/web/sites/{id}/package` / preview content) on a
   dedicated internal port bound to the tailnet interface (not the LAN). A tiny
   static file server per published site, or one server keyed by label. Static
   only in v1 (matches Web Studio's stated scope); "dynamic + keep-running"
   containers are a later phase.
2. **Register the route.** `POST hs.taos.my.../api/sites/publish` with
   `Authorization: Bearer <sites_token>` and `{subdomain, port, host_id}` -- the
   subdomain is checked against the account's active claims server-side
   The label is validated client-side against the same reserved rules, but taos.my
   re-validates.
3. **Web Studio UI.** ShareView gains a "Publish to <subdomain>.taos.my" action
   (alongside install/export): pick a label, POST, show the resulting FQDN + a
   copy link, and an "unpublish". Gated on the host having joined a mesh
   (`mesh_status().joined`) with a clear "connect your taOS account first" empty
   state otherwise.

The ops half (wildcard `*.taos.my` DNS-01 cert + the front proxy that calls
`/api/relay/authorize-site`) is already flagged for Jay/@hermes and is
independent of this controller work.

## Slice plan (each independently shippable + testable)

- **Slice 1 - credential store + poll-intercept persistence.** `mesh_credentials.py`
  + `save/get` API, `get_controller_token`/`get_sites_token` backed by it, the
  `cluster_join_poll` intercept that persists server-side and strips tokens from
  the browser body. Tests: persistence round-trip, 0600 perms, idempotent
  re-poll, browser body has no raw tokens, env-var dev override still wins. NO
  mesh-join yet (credentials just persist). This alone makes `get_controller_token`
  real and unblocks the taOSnet passkey fetch too.
- **Slice 2 - headless mesh-join** (pending Jay's 2a/2b decision). `mesh_up`,
  `mesh_status`, install-story changes, Account-pane status. Tests: mock the
  join call; live-verify on the Pi against hs.taos.my.
- **Slice 3 - publish caller + static server + Web Studio UI.** Static-site server
  on the tailnet port, `sites_token` publish/unpublish caller, ShareView publish
  action. Tests: publish round-trip (mock taos.my), unpublish, mesh-not-joined
  empty state; live-verify the FQDN resolves once ops lands the cert+proxy.

## Open questions for Jay

1. **Mesh-join mechanism (Unit 2): system `tailscale` (2a, recommended) or
   userspace `tsnet` (2b)?** This defines a new per-platform install dependency,
   so it is the gating decision. Everything in Slice 1 is independent of it and
   can land first regardless.
2. **Static-site serving (Unit 3): a per-site static server on the tailnet is the
   v1 plan. Confirm static-only for v1** (dynamic/keep-running containers deferred),
   consistent with Web Studio's current scope.
3. Is there already a tailnet brought up on the Pi by the streamed-browser/Neko
   work that Unit 2 should reuse rather than duplicate? (I will verify in code
   before building Slice 2.)

## Non-goals (v1)

Dynamic/backend sites + "keep running" containers; custom TLD domains (#168);
multi-host publish; client (phone/laptop) mesh membership (that is the separate
tsnet client work website-dev referenced).
