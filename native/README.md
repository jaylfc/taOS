# taOS native client

A Cargo workspace for the native (Rust) side of taOS. For now it holds one
crate.

| Crate | What it is |
|---|---|
| `taos-client` | The shared, GUI-toolkit-independent client for the Python controller: REST, SSE, session sign-in. Every native app and the native shell use it. |

Minimum Rust version: **1.85** (Debian 13's `rustc`). Dependency versions are
resolved with the MSRV-aware resolver, so `Cargo.lock` is committed and builds
on 1.85. Nothing here uses `unsafe`; TLS is rustls (no OpenSSL).

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
* `pin_login` (`POST /auth/pin-login`) sends `X-taOS-Console: 1`. The server
  accepts it only from the device's own screen over loopback.

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
server sends ids, an idle timeout, and cancellation on drop. Transient trouble
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
