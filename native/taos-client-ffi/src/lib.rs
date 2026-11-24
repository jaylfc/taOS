// SPDX-License-Identifier: AGPL-3.0-or-later
//! C ABI over `taos-client`.
//!
//! The header is `include/taos_client.h` (kept in step by hand, and checked by
//! `tests/header.rs`). The contract, in short:
//!
//! * One callback per client, registered at creation. Every request and every
//!   stream is identified by a caller-chosen `uint64` id; the callback receives
//!   `(user, id, kind, json, len)`.
//! * A request gets exactly one terminal event (`TAOS_EV_RESULT` or
//!   `TAOS_EV_FAILED`). A stream gets zero or more `TAOS_EV_STREAM_EVENT` /
//!   `TAOS_EV_STREAM_RETRYING` and then exactly one `TAOS_EV_STREAM_ENDED`,
//!   unless it is cancelled first.
//! * Callbacks are serialised (one at a time, from tokio worker threads) and run
//!   under an internal lock: they must only copy the data and post it to the
//!   UI thread, and must not call back into this library.
//! * `taos_cancel(id)` and `taos_client_free` guarantee that no callback for
//!   that id (resp. any id) is running or will run after they return.
//! * Every `extern "C"` function catches panics and reports failure instead of
//!   unwinding into C++.
//!
//! The `unsafe` in this file is the pointer handling at the boundary only.
#![allow(unsafe_code)]

use std::collections::HashMap;
use std::ffi::{c_char, c_int, c_void, CStr, CString};
use std::panic::{catch_unwind, AssertUnwindSafe};
use std::sync::{Arc, Mutex, MutexGuard, OnceLock};
use std::time::Duration;

use futures_util::StreamExt;
use serde_json::{json, Value};
use taos_client::events::{Backoff, SseOptions};
use taos_client::Client;

mod ops;

// ---- constants (mirrored in the header) ----------------------------------

/// A request finished; `json` is the response value.
pub const TAOS_EV_RESULT: c_int = 1;
/// A request failed; `json` is an error object (see `error_json`).
pub const TAOS_EV_FAILED: c_int = 2;
/// A stream frame; `json` is `{"event","id","data"}`.
pub const TAOS_EV_STREAM_EVENT: c_int = 3;
/// A stream lost its connection and is reconnecting; `json` is an error object.
pub const TAOS_EV_STREAM_RETRYING: c_int = 4;
/// A stream ended for good (fatal error, or the server's `event: error`);
/// `json` is an error object.
pub const TAOS_EV_STREAM_ENDED: c_int = 5;

/// Success.
pub const TAOS_OK: c_int = 0;
/// A pointer argument was null or not valid UTF-8 / JSON.
pub const TAOS_ERR_ARGS: c_int = 1;
/// The id is already in use.
pub const TAOS_ERR_ID_IN_USE: c_int = 2;
/// The client was closed.
pub const TAOS_ERR_CLOSED: c_int = 3;
/// An internal failure (a panic was caught).
pub const TAOS_ERR_INTERNAL: c_int = 4;
/// `taos_client_free` was called from inside a callback; nothing was freed.
pub const TAOS_ERR_IN_CALLBACK: c_int = 5;

/// A stream (re)connected: the response headers arrived. `json` is `null`.
pub const TAOS_EV_STREAM_CONNECTED: c_int = 6;

/// The C callback type.
pub type TaosCallback = Option<
    unsafe extern "C" fn(user: *mut c_void, id: u64, kind: c_int, json: *const c_char, len: usize),
>;

/// Client configuration. All pointers may be null except `base_url`; zero
/// durations mean "default".
#[repr(C)]
pub struct TaosConfig {
    /// Controller base URL.
    pub base_url: *const c_char,
    /// A session snapshot from `taos_session_export`, or null.
    pub session_json: *const c_char,
    /// A bearer token (host-local token), or null.
    pub bearer_token: *const c_char,
    /// Request timeout for ordinary calls.
    pub request_timeout_ms: u32,
    /// TCP connect timeout.
    pub connect_timeout_ms: u32,
    /// First reconnect delay of a stream.
    pub sse_backoff_base_ms: u32,
    /// Upper bound of the reconnect delay.
    pub sse_backoff_cap_ms: u32,
    /// A stream with no bytes for this long is treated as dead; 0 = 60 s.
    pub sse_idle_timeout_ms: u32,
}

// ---- runtime and client state -------------------------------------------

fn runtime() -> &'static tokio::runtime::Runtime {
    static RT: OnceLock<tokio::runtime::Runtime> = OnceLock::new();
    RT.get_or_init(|| {
        tokio::runtime::Builder::new_multi_thread()
            .worker_threads(2)
            .thread_name("taos-net")
            .enable_all()
            .build()
            .expect("tokio runtime")
    })
}

struct Live {
    closed: bool,
    tasks: HashMap<u64, tokio::task::AbortHandle>,
}

struct Shared {
    cb: unsafe extern "C" fn(*mut c_void, u64, c_int, *const c_char, usize),
    user: usize,
    live: Mutex<Live>,
}

thread_local! {
    static IN_CALLBACK: std::cell::Cell<bool> = const { std::cell::Cell::new(false) };
}

impl Shared {
    fn lock(&self) -> MutexGuard<'_, Live> {
        self.live.lock().unwrap_or_else(|p| p.into_inner())
    }

    /// Deliver one event for `id` under the lock, unless the id was cancelled
    /// or the client closed. `terminal` removes the id afterwards.
    fn emit(&self, id: u64, kind: c_int, json: &str, terminal: bool) -> bool {
        let mut live = self.lock();
        if live.closed || !live.tasks.contains_key(&id) {
            return false;
        }
        if terminal {
            live.tasks.remove(&id);
        }
        let Ok(c) = CString::new(json.replace('\0', "")) else {
            return false;
        };
        IN_CALLBACK.with(|f| f.set(true));
        // SAFETY: `cb` was supplied by the caller with the contract documented
        // in the header; `c` outlives the call.
        let r = catch_unwind(AssertUnwindSafe(|| unsafe {
            (self.cb)(self.user as *mut c_void, id, kind, c.as_ptr(), json.len())
        }));
        IN_CALLBACK.with(|f| f.set(false));
        r.is_ok()
    }
}

/// Opaque client handle.
pub struct TaosClient {
    client: Client,
    shared: Arc<Shared>,
    sse: SseOptions,
}

// ---- helpers -------------------------------------------------------------

fn error_json(e: &taos_client::Error) -> String {
    use taos_client::Error as E;
    let (kind, message): (&str, String) = match e {
        E::Unauthenticated { message } => ("unauthenticated", message.clone()),
        E::Forbidden { message, .. } => ("forbidden", message.clone()),
        E::NotFound { message } => ("not_found", message.clone()),
        E::Conflict { message } => ("conflict", message.clone()),
        E::RateLimited { message, .. } => ("rate_limited", message.clone()),
        E::Validation { message, .. } => ("validation", message.clone()),
        E::BadRequest { message } => ("bad_request", message.clone()),
        E::Http { message, .. } => ("http", message.clone()),
        E::Server { message, .. } => ("server", message.clone()),
        E::Transport(t) => ("network", transport_text(&t.to_string())),
        E::Decode { path, message, .. } => ("decode", format!("{path}: {message}")),
        E::Disconnected => ("network", "stream closed by the server".to_string()),
        E::Stream(m) => ("stream", m.clone()),
        E::Unsupported(m) => ("unsupported", (*m).to_string()),
        E::InvalidInput(m) => ("invalid_input", m.clone()),
        E::Protocol(m) => ("protocol", m.clone()),
        E::Io(i) => ("io", i.to_string()),
    };
    let mut v = json!({ "kind": kind, "message": message });
    if let Some(s) = e.status() {
        v["status"] = json!(s);
    }
    if let E::Forbidden { csrf, .. } = e {
        v["csrf"] = json!(csrf);
    }
    if let E::RateLimited {
        retry_after: Some(d),
        ..
    } = e
    {
        v["retry_after_ms"] = json!(d.as_millis() as u64);
    }
    if let E::Validation { fields, .. } = e {
        v["fields"] = fields
            .iter()
            .map(|f| json!({"location": f.location, "message": f.message}))
            .collect();
    }
    v.to_string()
}

fn transport_text(s: &str) -> String {
    // Strip the URL from the text (it is the caller's own base URL anyway, and
    // the Qt side words the problem in plain language).
    match s.split_once(" for url") {
        Some((head, _)) => head.to_string(),
        None => s.to_string(),
    }
}

fn internal_error_json(msg: &str) -> String {
    json!({"kind": "internal", "message": msg}).to_string()
}

/// # Safety
/// `p` must be null or a valid NUL-terminated string.
unsafe fn cstr<'a>(p: *const c_char) -> Option<&'a str> {
    if p.is_null() {
        return None;
    }
    // SAFETY: caller contract.
    unsafe { CStr::from_ptr(p) }.to_str().ok()
}

fn ms(v: u32, default: Duration) -> Duration {
    if v == 0 {
        default
    } else {
        Duration::from_millis(u64::from(v))
    }
}

fn set_out(out: *mut *mut c_char, s: &str) {
    if out.is_null() {
        return;
    }
    let c = CString::new(s.replace('\0', "")).unwrap_or_default();
    // SAFETY: `out` is a caller-provided location for one pointer.
    unsafe { *out = c.into_raw() };
}

// ---- exported API --------------------------------------------------------

/// Library version string (static, do not free).
#[no_mangle]
pub extern "C" fn taos_version() -> *const c_char {
    concat!(env!("CARGO_PKG_VERSION"), "\0").as_ptr().cast()
}

/// Create a client. Returns null on failure with an error object in `*error_json_out`
/// (free it with `taos_string_free`).
///
/// # Safety
/// `cfg` must point to a valid `TaosConfig` whose strings are NUL-terminated;
/// `cb` must stay valid, and `user` usable by it, until `taos_client_free` returns.
#[no_mangle]
pub unsafe extern "C" fn taos_client_new(
    cfg: *const TaosConfig,
    cb: TaosCallback,
    user: *mut c_void,
    error_json_out: *mut *mut c_char,
) -> *mut TaosClient {
    let r = catch_unwind(AssertUnwindSafe(|| {
        let (Some(cfg), Some(cb)) = (unsafe { cfg.as_ref() }, cb) else {
            set_out(
                error_json_out,
                &internal_error_json("null config or callback"),
            );
            return std::ptr::null_mut();
        };
        let Some(base) = (unsafe { cstr(cfg.base_url) }) else {
            set_out(error_json_out, &internal_error_json("base_url missing"));
            return std::ptr::null_mut();
        };
        let mut b = Client::builder()
            .base_url(base)
            .request_timeout(ms(cfg.request_timeout_ms, Duration::from_secs(30)))
            .connect_timeout(ms(cfg.connect_timeout_ms, Duration::from_secs(10)));
        if let Some(s) = unsafe { cstr(cfg.session_json) } {
            match serde_json::from_str::<taos_client::Session>(s) {
                Ok(sess) => b = b.session(sess),
                Err(e) => {
                    set_out(
                        error_json_out,
                        &internal_error_json(&format!("saved session unreadable: {e}")),
                    );
                    return std::ptr::null_mut();
                }
            }
        }
        if let Some(t) = unsafe { cstr(cfg.bearer_token) } {
            b = b.bearer_token(t);
        }
        let client = match b.build() {
            Ok(c) => c,
            Err(e) => {
                set_out(error_json_out, &error_json(&e));
                return std::ptr::null_mut();
            }
        };
        let sse = SseOptions {
            resume: true,
            backoff: Backoff::new(
                ms(cfg.sse_backoff_base_ms, Duration::from_millis(500)),
                ms(cfg.sse_backoff_cap_ms, Duration::from_secs(30)),
            ),
            idle_timeout: Some(ms(cfg.sse_idle_timeout_ms, Duration::from_secs(60))),
            announce_connection: true,
        };
        let shared = Arc::new(Shared {
            cb,
            user: user as usize,
            live: Mutex::new(Live {
                closed: false,
                tasks: HashMap::new(),
            }),
        });
        Box::into_raw(Box::new(TaosClient {
            client,
            shared,
            sse,
        }))
    }));
    r.unwrap_or(std::ptr::null_mut())
}

/// Close a client and free it. Aborts all requests and streams. When this
/// returns, no callback is running and none will run again, so the `user`
/// pointer may be freed. Returns `TAOS_ERR_IN_CALLBACK` (and does nothing) if
/// called from inside a callback.
///
/// # Safety
/// `c` must come from `taos_client_new` and not be used afterwards.
#[no_mangle]
pub unsafe extern "C" fn taos_client_free(c: *mut TaosClient) -> c_int {
    if c.is_null() {
        return TAOS_OK;
    }
    if IN_CALLBACK.with(|f| f.get()) {
        return TAOS_ERR_IN_CALLBACK;
    }
    let r = catch_unwind(AssertUnwindSafe(|| {
        // SAFETY: caller contract; ownership returns to us.
        let boxed = unsafe { Box::from_raw(c) };
        // Taking the lock waits for a running callback to finish.
        let mut live = boxed.shared.lock();
        live.closed = true;
        for (_, h) in live.tasks.drain() {
            h.abort();
        }
        drop(live);
        TAOS_OK
    }));
    r.unwrap_or(TAOS_ERR_INTERNAL)
}

/// Start an operation. `op` names a typed operation (`notifications.list`,
/// ...) or `http` for a raw request; `args_json` is a JSON object (or null for
/// none). Exactly one terminal event is delivered for `id`, unless cancelled.
///
/// # Safety
/// `c` must be a live client; strings NUL-terminated.
#[no_mangle]
pub unsafe extern "C" fn taos_call(
    c: *mut TaosClient,
    id: u64,
    op: *const c_char,
    args_json: *const c_char,
) -> c_int {
    let r = catch_unwind(AssertUnwindSafe(|| {
        let Some(c) = (unsafe { c.as_ref() }) else {
            return TAOS_ERR_ARGS;
        };
        let (Some(op), args) = (unsafe { cstr(op) }, unsafe { cstr(args_json) }) else {
            return TAOS_ERR_ARGS;
        };
        let args: Value = match args {
            None | Some("") => Value::Null,
            Some(s) => match serde_json::from_str(s) {
                Ok(v) => v,
                Err(_) => return TAOS_ERR_ARGS,
            },
        };
        let op = op.to_string();
        let client = c.client.clone();
        let shared = c.shared.clone();
        let mut live = shared.lock();
        if live.closed {
            return TAOS_ERR_CLOSED;
        }
        if live.tasks.contains_key(&id) {
            return TAOS_ERR_ID_IN_USE;
        }
        let sh = shared.clone();
        let handle = runtime().spawn(async move {
            let res = ops::run(&client, &op, args).await;
            match res {
                Ok(v) => sh.emit(id, TAOS_EV_RESULT, &v.to_string(), true),
                Err(e) => sh.emit(id, TAOS_EV_FAILED, &error_json(&e), true),
            };
        });
        live.tasks.insert(id, handle.abort_handle());
        TAOS_OK
    }));
    r.unwrap_or(TAOS_ERR_INTERNAL)
}

/// Open a reconnecting server-sent-event stream on `path` (for example
/// `/api/os/events`) with `query_json` = `{"k":"v",...}` or null. Reconnect
/// and backoff are handled here (see `TaosConfig`).
///
/// # Safety
/// `c` must be a live client; strings NUL-terminated.
#[no_mangle]
pub unsafe extern "C" fn taos_stream_open(
    c: *mut TaosClient,
    id: u64,
    path: *const c_char,
    query_json: *const c_char,
) -> c_int {
    let r = catch_unwind(AssertUnwindSafe(|| {
        let Some(c) = (unsafe { c.as_ref() }) else {
            return TAOS_ERR_ARGS;
        };
        let (Some(path), q) = (unsafe { cstr(path) }, unsafe { cstr(query_json) }) else {
            return TAOS_ERR_ARGS;
        };
        if !path.starts_with('/') {
            return TAOS_ERR_ARGS;
        }
        let mut query: Vec<(String, String)> = Vec::new();
        if let Some(q) = q.filter(|s| !s.is_empty()) {
            match serde_json::from_str::<Value>(q) {
                Ok(Value::Object(m)) => {
                    for (k, v) in m {
                        let s = match v {
                            Value::String(s) => s,
                            other => other.to_string(),
                        };
                        query.push((k, s));
                    }
                }
                Ok(Value::Null) => {}
                _ => return TAOS_ERR_ARGS,
            }
        }
        let path = path.to_string();
        let client = c.client.clone();
        let opts = c.sse;
        let shared = c.shared.clone();
        let mut live = shared.lock();
        if live.closed {
            return TAOS_ERR_CLOSED;
        }
        if live.tasks.contains_key(&id) {
            return TAOS_ERR_ID_IN_USE;
        }
        let sh = shared.clone();
        let handle = runtime().spawn(async move {
            // Streams spawn their own task, so create it inside the runtime.
            let mut stream: std::pin::Pin<
                Box<dyn futures_util::Stream<Item = taos_client::Result<taos_client::events::Event>> + Send>,
            > = if path == taos_client::chat::WS_PATH {
                Box::pin(client.chat_stream(opts))
            } else {
                Box::pin(client.sse(path, query, opts))
            };
            while let Some(item) = stream.next().await {
                match item {
                    Ok(ev) if ev.event.as_deref() == Some(taos_client::events::CONNECTED_EVENT) => {
                        if !sh.emit(id, TAOS_EV_STREAM_CONNECTED, "null", false) {
                            return;
                        }
                    }
                    Ok(ev) => {
                        let data = serde_json::from_str::<Value>(&ev.data)
                            .unwrap_or(Value::String(ev.data.clone()));
                        let j = json!({"event": ev.event, "id": ev.id, "data": data});
                        if !sh.emit(id, TAOS_EV_STREAM_EVENT, &j.to_string(), false) {
                            return;
                        }
                    }
                    Err(e) => {
                        let fatal = is_fatal(&e);
                        let kind = if fatal {
                            TAOS_EV_STREAM_ENDED
                        } else {
                            TAOS_EV_STREAM_RETRYING
                        };
                        if !sh.emit(id, kind, &error_json(&e), fatal) || fatal {
                            return;
                        }
                    }
                }
            }
            sh.emit(
                id,
                TAOS_EV_STREAM_ENDED,
                &internal_error_json("stream closed"),
                true,
            );
        });
        live.tasks.insert(id, handle.abort_handle());
        TAOS_OK
    }));
    r.unwrap_or(TAOS_ERR_INTERNAL)
}

/// Mirrors the stream's own rule: 401/403/404 and other 4xx end the stream.
fn is_fatal(e: &taos_client::Error) -> bool {
    use taos_client::Error as E;
    match e {
        E::Transport(_) | E::Server { .. } | E::RateLimited { .. } | E::Disconnected => false,
        E::Http { status, .. } => *status != 408,
        _ => true,
    }
}

/// Cancel a request or stream. After this returns no callback for `id` is
/// running or will run, and no terminal event is delivered for it.
///
/// # Safety
/// `c` must be a live client.
#[no_mangle]
pub unsafe extern "C" fn taos_cancel(c: *mut TaosClient, id: u64) -> c_int {
    let r = catch_unwind(AssertUnwindSafe(|| {
        let Some(c) = (unsafe { c.as_ref() }) else {
            return TAOS_ERR_ARGS;
        };
        if let Some(h) = c.shared.lock().tasks.remove(&id) {
            h.abort();
        }
        TAOS_OK
    }));
    r.unwrap_or(TAOS_ERR_INTERNAL)
}

/// Replace (or with null remove) the bearer token. Takes effect on the next
/// request; open streams pick it up on their next reconnect.
///
/// # Safety
/// `c` must be a live client; `token` null or NUL-terminated.
#[no_mangle]
pub unsafe extern "C" fn taos_set_bearer(c: *mut TaosClient, token: *const c_char) -> c_int {
    let r = catch_unwind(AssertUnwindSafe(|| {
        let Some(c) = (unsafe { c.as_ref() }) else {
            return TAOS_ERR_ARGS;
        };
        c.client
            .set_bearer_token(unsafe { cstr(token) }.map(str::to_string));
        TAOS_OK
    }));
    r.unwrap_or(TAOS_ERR_INTERNAL)
}

/// 1 when the client holds a session cookie or bearer token, else 0.
///
/// # Safety
/// `c` must be a live client.
#[no_mangle]
pub unsafe extern "C" fn taos_has_credentials(c: *mut TaosClient) -> c_int {
    catch_unwind(AssertUnwindSafe(|| match unsafe { c.as_ref() } {
        Some(c) => c_int::from(c.client.has_credentials()),
        None => 0,
    }))
    .unwrap_or(0)
}

/// The current session as an opaque JSON string for the keyring, or null when
/// not signed in with a cookie session. Contains the live token: treat it like
/// a password. Free with `taos_string_free`.
///
/// # Safety
/// `c` must be a live client.
#[no_mangle]
pub unsafe extern "C" fn taos_session_export(c: *mut TaosClient) -> *mut c_char {
    catch_unwind(AssertUnwindSafe(|| {
        let Some(c) = (unsafe { c.as_ref() }) else {
            return std::ptr::null_mut();
        };
        match c.client.export_session() {
            Some(s) => match serde_json::to_string(&s) {
                Ok(j) => CString::new(j).map_or(std::ptr::null_mut(), CString::into_raw),
                Err(_) => std::ptr::null_mut(),
            },
            None => std::ptr::null_mut(),
        }
    }))
    .unwrap_or(std::ptr::null_mut())
}

/// Forget the local session cookies (does not tell the server).
///
/// # Safety
/// `c` must be a live client.
#[no_mangle]
pub unsafe extern "C" fn taos_session_forget(c: *mut TaosClient) -> c_int {
    catch_unwind(AssertUnwindSafe(|| match unsafe { c.as_ref() } {
        Some(c) => {
            c.client.forget_session();
            TAOS_OK
        }
        None => TAOS_ERR_ARGS,
    }))
    .unwrap_or(TAOS_ERR_INTERNAL)
}

/// Free a string returned by this library.
///
/// # Safety
/// `s` must be null or a pointer returned by this library, freed once.
#[no_mangle]
pub unsafe extern "C" fn taos_string_free(s: *mut c_char) {
    if !s.is_null() {
        // SAFETY: produced by CString::into_raw in this crate.
        drop(unsafe { CString::from_raw(s) });
    }
}
