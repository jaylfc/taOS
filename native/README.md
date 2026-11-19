# taOS native client

A Cargo workspace for the native (Rust) side of taOS. For now it holds one
crate.

| Crate | What it is |
|---|---|
| `taos-client` | The shared, GUI-toolkit-independent client for the Python controller: REST, SSE, session sign-in. Every native app and the native shell use it. |
| `taos-client-ffi` | A small C ABI over `taos-client` (header `taos-client-ffi/include/taos_client.h`) for the Qt layer. |

The Qt (QML + C++) layer on top is in [`qt/`](qt/README.md); the fake controller
the tests use is `testing/fake_controller.py`.

Minimum Rust version: **1.85** (Debian 13's `rustc`). Dependency versions are
resolved with the MSRV-aware resolver, so `Cargo.lock` is committed and builds
on 1.85. `taos-client` uses no `unsafe` (the only `unsafe` is the C boundary in `taos-client-ffi`); TLS is rustls (no OpenSSL).

```text
native/check.sh        # fmt, clippy -D warnings, tests, and a 1.85 build
cargo run --example smoke   # real controller, see below
```

## Using `taos-client`

```rust
use taos_client::{Client, Error};

# async fn demo() -> Result<(), Error> {
let client = Client::with_base_url("http://127.0.0.1:6969")?;   // default is that URL
client.login(Some("jay"), "password", Some(true)).await?;       // sets the session cookies
let me = client.me().await?;

let unread = client.notifications().list(true).await?;
let ws = client.files().list(&taos_client::files::Location::Workspace, "").await?;
# Ok(()) }
```

Domains: `client.notifications()`, `.decisions()`, `.files()`, `.models()`,
`.dashboard()`. Each method's doc comment names the route it calls. Live
updates are in `taos_client::events` (`os_events`, `event_stream`, the
generic `Client::sse`, and `PollWhileVisible`).

### Sessions

* Sign-in is the web UI's cookie path: `POST /auth/login` (JSON) sets
  `taos_session` and `csrf_token`. The client keeps them in a private jar and
  copies `csrf_token` into `X-CSRF-Token` on every POST, PUT, PATCH and
  DELETE, never on GET. A CSRF rejection (the server re-issues a bound cookie
  on the rejecting response) is retried once automatically.
* The controller binds a session to a hash of the `User-Agent` it was created
  with (`AuthManager.create_session` and `validate_session` in
  `tinyagentos/auth.py`). The client therefore sends one constant string,
  `taOS-native/<crate version>`, on every request, streams included.
* `Client::export_session()` returns an opaque, serialisable `Session` and
  `Client::with_session(session)` restores it. Put it in the OS keyring; the
  crate never writes a file. The snapshot records the User-Agent it was
  created with and the restored client keeps sending it, so a session survives
  an upgrade of this crate even though the default string carries the crate
  version. The serialised form contains the live session token: treat it like a
  password.
* `Debug` for `Client`, `Session` and the cookie jar prints `<redacted>` for
  every secret. Passwords are never stored.
* `pin_login` (`POST /auth/pin-login`) sends no console header: that route
  gates only on `is_console_origin` (a loopback peer and no forwarding
  header), unlike the other `/auth/lock-*` posts, which also need
  `X-taOS-Console` (`_lock_post_refusal`). The server accepts a PIN only from
  the device's own screen.

### Errors

One `Error` enum. A 401 anywhere is `Error::Unauthenticated` (`is_unauthenticated()`),
so a UI can drop to the lock screen. 403 is `Forbidden { csrf }`, then
`NotFound`, `Conflict`, `RateLimited { retry_after }`, `Validation { fields }`
(422), `BadRequest`, `Http { status }` (other 4xx, for example 410), `Server`
(5xx), `Transport`, and `Decode { path, body }` with a truncated body. The
controller mixes `{"detail": ...}` and `{"error": ...}` (and OpenAI-shaped
`{"error": {"message": ...}}` on the LLM gateway); all of them end up in the
variant's `message`, so show it verbatim.

Response types are hand-written because the controller declares almost no
response models. Unknown fields are ignored, optional fields default, and
every enum keeps unknown values in an `Unknown(String)` variant.

### Streams and polling

`Client::sse` is a generic Server-Sent Events stream with reconnect (backoff
500 ms doubling to a 30 s cap, with jitter), `Last-Event-ID` resume when the
server sends ids, an optional idle timeout (default 60 s, which suits the streams that send a
10 s keepalive; the file watch routes send no keepalive, so `Files::watch`
turns it off), and cancellation on drop. Transient trouble
shows up as an `Err` item (`Disconnected`, `Transport`, `Server`) while it
keeps reconnecting, which is what a "paused" banner needs; fatal errors (401,
403, 404 and other 4xx) are yielded once and end the stream. Typed wrappers:
`os_events`, `event_stream`, `Files::watch`, `Dashboard::model_activity_stream`.

`PollWhileVisible::spawn(period, visible, action)` runs `action` immediately,
then `period` after each run finishes (never overlapping). `set_visible(false)`
pauses it; `set_visible(true)` fires immediately. `poll_now()` is the manual
refresh. Dropping it stops it.

Streams and pollers spawn Tokio tasks, so create them inside a Tokio runtime.
WebSocket chat is not part of this version (`taos_client::chat` is a stub).

### Driving it from a GTK or Qt event loop

The client is async on Tokio; the toolkit keeps its own loop. Own one runtime
on a worker thread and send results back to the UI thread:

```rust
// Once, at startup. Keep `rt` alive for the life of the app.
let rt = tokio::runtime::Builder::new_multi_thread().enable_all().build()?;
let client = taos_client::Client::new()?;                     // clone freely, it is an Arc
let (tx, rx) = async_channel::unbounded();                    // or std mpsc / a Qt signal
// GTK: marshal onto the default main context
glib::MainContext::default().spawn_local(async move { while let Ok(msg) = rx.recv().await { update_ui(msg); } });
// Qt: emit a queued signal (or QMetaObject::invokeMethod with Qt::QueuedConnection) from the closure instead.
let c = client.clone();
rt.spawn(async move { let _ = tx.send(c.notifications().list(true).await).await; });  // never block the UI thread
let _guard = rt.enter();   // lets you call os_events(), PollWhileVisible::spawn() from UI code
```

`async_channel` and `glib` are shown for illustration; the crate has no toolkit
dependency. Results are plain owned values and streams are `Send`.

## Manual smoke run

`examples/smoke.rs` signs in to a real controller and prints the user, the
unread count and three file locations. It is not run by CI.

```text
TAOS_URL=http://127.0.0.1:6969 TAOS_USER=jay TAOS_PASS=... cargo run --example smoke
```

## Tests

`cargo test` runs offline against a wiremock fake controller. Fixtures under
`taos-client/tests/fixtures/` are built from the response shapes in the
controller source (`tinyagentos/routes/*.py`), including the places where the
source and the port specs disagree.

## Controller behaviours the typed API accounts for

* `GET /api/notifications` returns every pinned grant row plus at most 20
  others, newest first (the store's default limit; the route passes none).
* Agent files are deleted into a host-side workspace trash
  (`/api/agents/{a}/workspace/trash`), not the container recycle bin that
  `GET /api/recycle` lists. `Files::trash` reads the former, `Files::agent_recycle`
  the latter.
* Shared folders can only be listed and uploaded to. `Location::supports` says
  which operations exist, and unsupported ones return `Error::Unsupported`
  without sending anything.
* There is no route to cancel a model download. `DownloadWatch::cancel` stops
  polling; the download keeps running on the controller.
* `/api/activity/models*` accepts only a session cookie, which is what this
  client uses.

## Where the controller differs from the port specs and the surface doc

Found by reading the source in this tree (line numbers are this tree's, which
is newer than the docs'). The typed API follows the source.

| Topic | Docs say | Source says |
|---|---|---|
| `pin-login` console header | surface doc 4B: send `X-taOS-Console: 1` | not required; only `routes/auth.py:12951` (`pin_login`) peer check |
| Scope vocabulary | `{project_scopes}` | also `valid_scopes` (`routes/agent_auth_requests.py:566`) |
| Deny bodies | deny takes `{reason}` | access-request deny takes no body (`:1776`); scope deny reason is optional (`:202`, `:2369`) |
| Notification list | capped at 100 client-side | server returns all pinned rows plus 20 others (`tinyagentos/notifications.py:247`, `:273`; route `routes/notifications.py:57` passes no limit); rows add `pinned` and `user_id` |
| Decision `created_at` | epoch seconds or ISO | always epoch float (`tinyagentos/decisions/decision_store.py:34`); both are still accepted |
| Gate decision kinds | four kinds | five: adds `collab_delegation_gate` and `shell_command` (`routes/decisions.py:41`) |
| Shared-folder shapes | `FileEntry` and `{id,name,description,agents[],created_at}` | listing rows are `{name,size_mb,modified}` (`tinyagentos/shared_folders.py:118`); folders are `{id,name,description,owner_type,owner_name}` (`:90`, `:93`) |
| Agent file delete | restore from `/api/recycle` | delete goes to a host-side trash (`routes/agent_workspace.py:236`, `:254`; `/workspace/trash` routes `:259` to `:301`), not the container bin of `routes/recycle.py` |
| `/api/recycle` | returns `status?` | no `status`; only `GET /api/agents/{name}/recycle` has it (`routes/recycle.py:80`, `:92`) |
| Review-stamp cycle | awaiting_review, approved, changes_requested | `approved` cannot go to `changes_requested` (`tinyagentos/projects/doc_review_store.py:27`); routes take the project id, not the slug |
| Download start | `{download_id}` | `{status,download_id,app_id,variant_id}` (`routes/models.py:517`), and 503 on insufficient VRAM (`:429`) |
| Download list | bare list | `{downloads: [...]}` (`routes/models.py:525`) |
| Download cancel | requested | no route exists |
| Workers for members | full record | reduced record: name, status, tier, kind (`routes/cluster.py:344`) |
| Provider models | `{id,name}` | seeded entries are `{name,size_mb}` with no `id` (`routes/providers.py:416`); entries also carry `api_key` for admins, which this crate never models |
| Loaded models | has `host` | no entry sets `host` (`routes/models.py:855`, `:878`, `:896`) |
| `/api/activity` | lists | `zram`, `network` and `gpu.load` become `{}` on error paths (`routes/activity.py:91`, `:101`, `:106`) |
| Scheduler | statuses complete/running/error/rejected | also `queued`, `cancelled` (`tinyagentos/scheduler/types.py:34`, `:39`); routes answer 503 before the scheduler exists (`routes/scheduler.py:16`) |
| Model-activity stream | frames without ids | frames carry `id: <seq>` and `Last-Event-ID` resumes (`routes/model_activity.py:68`, `:119`); events add `owner` (`tinyagentos/model_activity.py:109`) |
| AI-stack restart | ok/partial/failed | also `noop` (`routes/system.py:258`) |
| `/api/events/stream` | resumable | its `id:` is a per-connection counter, so resume is off for it (`routes/event_stream.py`) |

Deliberately outside the typed API: the many other routes in the surface doc,
the notification source-to-app routing table (app logic), the project and
agent-workspace trash routes beyond what delete needs (they are included
because delete lands there), and WebSocket chat.
