//! Live updates: a reconnecting Server-Sent Events client, typed wrappers for
//! the controller's streams, and [`PollWhileVisible`].
//!
//! Streams are driven by a background Tokio task, so the constructors must be
//! called from inside a Tokio runtime context (for a GUI toolkit, from the
//! thread that owns the runtime, or after `runtime.enter()`). Dropping a
//! stream aborts its task and closes the connection.
//!
//! WebSocket chat (`/ws/chat`) is out of scope for this crate version; a
//! later change adds it next to this module.

use std::future::Future;
use std::pin::Pin;
use std::task::{Context, Poll};
use std::time::Duration;

use futures_util::{Stream, StreamExt};
use reqwest::header::{HeaderName, CACHE_CONTROL};
use serde::Deserialize;
use tokio::sync::{mpsc, watch, Notify};
use tokio::task::JoinHandle;
use tokio::time::Instant;

use crate::client::{decode, Client, Req};
use crate::error::{Error, Result};
use crate::notifications::Notification;

// ---------------------------------------------------------------------------
// Frames and parsing
// ---------------------------------------------------------------------------

/// One dispatched Server-Sent Event.
#[derive(Debug, Clone, PartialEq, Eq, Default)]
pub struct Event {
    /// The `event:` name; `None` for the default `message` event.
    pub event: Option<String>,
    /// The `data:` lines joined with `\n`.
    pub data: String,
    /// The last `id:` the server sent on this connection (the SSE
    /// "last event ID"), `None` when the server never sends ids.
    pub id: Option<String>,
    /// A `retry:` hint in milliseconds, if the frame carried one.
    pub retry: Option<u64>,
}

/// Incremental parser for the `text/event-stream` format.
#[derive(Default)]
pub(crate) struct SseParser {
    buf: Vec<u8>,
    data: String,
    has_data: bool,
    event: Option<String>,
    last_id: Option<String>,
    retry: Option<u64>,
    started: bool,
}

impl SseParser {
    /// Feed bytes; returns every event completed by them. Handles `\n`,
    /// `\r\n` and `\r` line ends split across chunks, comments, multi-line
    /// `data:` and a leading UTF-8 BOM.
    pub(crate) fn feed(&mut self, chunk: &[u8]) -> Vec<Event> {
        self.buf.extend_from_slice(chunk);
        if !self.started {
            if self.buf.len() < 3 && b"\xEF\xBB\xBF".starts_with(&self.buf) {
                return Vec::new();
            }
            self.started = true;
            if self.buf.starts_with(b"\xEF\xBB\xBF") {
                self.buf.drain(..3);
            }
        }
        let mut out = Vec::new();
        while let Some(pos) = self.buf.iter().position(|&b| b == b'\n' || b == b'\r') {
            if self.buf[pos] == b'\r' && pos + 1 == self.buf.len() {
                // Could be the first half of \r\n; wait for the next byte.
                break;
            }
            let line: Vec<u8> = self.buf.drain(..pos).collect();
            // Drop the terminator (and the \n of a \r\n pair).
            let term = self.buf.remove(0);
            if term == b'\r' && self.buf.first() == Some(&b'\n') {
                self.buf.remove(0);
            }
            if let Some(ev) = self.line(&String::from_utf8_lossy(&line)) {
                out.push(ev);
            }
        }
        out
    }

    fn line(&mut self, line: &str) -> Option<Event> {
        if line.is_empty() {
            let name = self.event.take();
            let retry = self.retry.take();
            if !self.has_data {
                return None;
            }
            self.has_data = false;
            let mut data = std::mem::take(&mut self.data);
            if data.ends_with('\n') {
                data.pop();
            }
            return Some(Event {
                event: name,
                data,
                id: self.last_id.clone(),
                retry,
            });
        }
        if line.starts_with(':') {
            return None; // comment or heartbeat
        }
        let (field, value) = match line.split_once(':') {
            Some((f, v)) => (f, v.strip_prefix(' ').unwrap_or(v)),
            None => (line, ""),
        };
        match field {
            "event" => self.event = Some(value.to_string()),
            "data" => {
                self.data.push_str(value);
                self.data.push('\n');
                self.has_data = true;
            }
            "id" if !value.contains('\0') => self.last_id = Some(value.to_string()),
            "retry" => self.retry = value.parse().ok(),
            _ => {}
        }
        None
    }
}

// ---------------------------------------------------------------------------
// Backoff and options
// ---------------------------------------------------------------------------

/// Exponential backoff with jitter, capped.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct Backoff {
    base: Duration,
    cap: Duration,
}

impl Default for Backoff {
    fn default() -> Self {
        Self {
            base: Duration::from_millis(500),
            cap: Duration::from_secs(30),
        }
    }
}

impl Backoff {
    /// A backoff starting at `base` and never exceeding `cap`.
    pub fn new(base: Duration, cap: Duration) -> Self {
        Self { base, cap }
    }

    /// The un-jittered delay for the given attempt (0-based): `base * 2^attempt`,
    /// capped.
    pub fn ceiling(&self, attempt: u32) -> Duration {
        let factor = 1u32.checked_shl(attempt.min(30)).unwrap_or(u32::MAX);
        self.base.saturating_mul(factor).min(self.cap)
    }

    /// The delay to actually wait: half the ceiling plus a random share of the
    /// other half ("equal jitter"), so it never exceeds the cap and never
    /// collapses to zero.
    pub fn delay(&self, attempt: u32) -> Duration {
        let ceil = self.ceiling(attempt);
        let half = ceil / 2;
        half + half.mul_f64(fastrand::f64())
    }
}

/// Options for a reconnecting SSE stream.
#[derive(Debug, Clone, Copy)]
pub struct SseOptions {
    /// Send `Last-Event-ID` on reconnect when the server sent ids. Turn off
    /// for streams whose ids are per-connection counters (`/api/events/stream`).
    pub resume: bool,
    /// Reconnect backoff.
    pub backoff: Backoff,
    /// Treat a connection with no bytes (not even a heartbeat) for this long
    /// as dead and reconnect; `None` waits forever. The OS-event, system-event
    /// and model-activity streams send a keepalive every 10 s, so the default
    /// 60 s is safe for them. The file watch routes send **nothing** while a
    /// folder is unchanged, so they must use `None` (the typed
    /// [`Files::watch`](crate::files::Files::watch) does).
    pub idle_timeout: Option<Duration>,
    /// Send a synthetic [`CONNECTED_EVENT`] item each time a connection is
    /// established (the response headers arrived with a success status). Off by
    /// default; a UI turns it on to show "live" before the first frame and to
    /// refetch whatever it missed while disconnected.
    pub announce_connection: bool,
}

/// The `event` name of the synthetic item produced by
/// [`SseOptions::announce_connection`]. Its `data` is empty.
pub const CONNECTED_EVENT: &str = "taos.connected";

impl Default for SseOptions {
    fn default() -> Self {
        Self {
            resume: true,
            backoff: Backoff::default(),
            idle_timeout: Some(Duration::from_secs(60)),
            announce_connection: false,
        }
    }
}

// ---------------------------------------------------------------------------
// The raw stream
// ---------------------------------------------------------------------------

/// A reconnecting stream of [`Event`]s.
///
/// Transient trouble is reported as an `Err` item ([`Error::Disconnected`],
/// [`Error::Transport`], [`Error::Server`]) and the stream keeps reconnecting,
/// so a UI can show a "paused" state while errors arrive and clear it on the
/// next `Ok`. Fatal errors (401, 403, 404, other 4xx) are yielded once and end
/// the stream. Dropping the stream cancels it.
pub struct SseStream {
    rx: mpsc::Receiver<Result<Event>>,
    task: JoinHandle<()>,
}

impl SseStream {
    /// Stop the background task and close the channel.
    fn shutdown(&mut self) {
        self.task.abort();
        self.rx.close();
    }
}

impl Drop for SseStream {
    fn drop(&mut self) {
        self.task.abort();
    }
}

impl Stream for SseStream {
    type Item = Result<Event>;
    fn poll_next(mut self: Pin<&mut Self>, cx: &mut Context<'_>) -> Poll<Option<Self::Item>> {
        self.rx.poll_recv(cx)
    }
}

fn is_fatal(e: &Error) -> bool {
    match e {
        Error::Unauthenticated { .. }
        | Error::Forbidden { .. }
        | Error::NotFound { .. }
        | Error::Conflict { .. }
        | Error::BadRequest { .. }
        | Error::Validation { .. } => true,
        Error::Http { status, .. } => *status != 408,
        Error::Decode { .. }
        | Error::InvalidInput(_)
        | Error::Protocol(_)
        | Error::Unsupported(_) => true,
        _ => false,
    }
}

impl Client {
    /// Open a reconnecting SSE stream on `path` (already percent-encoded).
    ///
    /// Must be called inside a Tokio runtime. See [`SseStream`] for the error
    /// contract and [`SseOptions`] for tuning.
    pub fn sse(
        &self,
        path: impl Into<String>,
        query: Vec<(String, String)>,
        opts: SseOptions,
    ) -> SseStream {
        let (tx, rx) = mpsc::channel(64);
        let client = self.clone();
        let path = path.into();
        let task = tokio::spawn(run_sse(client, path, query, opts, tx));
        SseStream { rx, task }
    }
}

async fn run_sse(
    client: Client,
    path: String,
    query: Vec<(String, String)>,
    opts: SseOptions,
    tx: mpsc::Sender<Result<Event>>,
) {
    let mut last_id: Option<String> = None;
    let mut attempt: u32 = 0;
    let last_event_id = HeaderName::from_static("last-event-id");
    loop {
        let mut req = Req::get(path.clone()).streaming("text/event-stream");
        req.query = query.clone();
        let resume = if opts.resume { last_id.clone() } else { None };
        let (le, no_cache) = (last_event_id.clone(), CACHE_CONTROL);
        let resp = client
            .send_with(&req, &move |rb| {
                let rb = rb.header(no_cache.clone(), "no-cache");
                match &resume {
                    Some(id) => rb.header(le.clone(), id.as_str()),
                    None => rb,
                }
            })
            .await;
        let mut extra_wait = Duration::ZERO;
        match resp {
            Err(e) => {
                if let Error::RateLimited {
                    retry_after: Some(d),
                    ..
                } = &e
                {
                    extra_wait = *d;
                }
                let fatal = is_fatal(&e);
                if tx.send(Err(e)).await.is_err() || fatal {
                    return;
                }
            }
            Ok(resp) => {
                if opts.announce_connection {
                    let ev = Event {
                        event: Some(CONNECTED_EVENT.to_string()),
                        ..Event::default()
                    };
                    if tx.send(Ok(ev)).await.is_err() {
                        return;
                    }
                }
                let mut parser = SseParser::default();
                let body = resp.bytes_stream();
                futures_util::pin_mut!(body);
                loop {
                    let next = match opts.idle_timeout {
                        Some(limit) => tokio::time::timeout(limit, body.next()).await,
                        None => Ok(body.next().await),
                    };
                    match next {
                        Err(_) | Ok(None) => {
                            if tx.send(Err(Error::Disconnected)).await.is_err() {
                                return;
                            }
                            break;
                        }
                        Ok(Some(Err(e))) => {
                            if tx.send(Err(Error::Transport(e))).await.is_err() {
                                return;
                            }
                            break;
                        }
                        Ok(Some(Ok(chunk))) => {
                            attempt = 0;
                            for ev in parser.feed(&chunk) {
                                if opts.resume {
                                    if let Some(id) = &ev.id {
                                        last_id = Some(id.clone());
                                    }
                                }
                                if tx.send(Ok(ev)).await.is_err() {
                                    return;
                                }
                            }
                        }
                    }
                }
            }
        }
        let wait = opts
            .backoff
            .delay(attempt)
            .max(extra_wait.min(opts.backoff.cap));
        attempt = attempt.saturating_add(1);
        tokio::time::sleep(wait).await;
    }
}

// ---------------------------------------------------------------------------
// Typed streams
// ---------------------------------------------------------------------------

/// What a frame mapper decided about one [`Event`].
pub(crate) enum Mapped<T> {
    /// Yield this item.
    Item(T),
    /// Yield this error and keep going.
    Soft(Error),
    /// Yield this error and end the stream.
    Fatal(Error),
}

/// A stream of typed items decoded from an [`SseStream`].
///
/// A frame that fails to decode is reported as an `Err(Error::Decode)` item
/// and the stream continues; an unknown frame never ends it. Dropping the
/// stream cancels the connection.
pub struct EventStream<T> {
    raw: SseStream,
    map: Box<dyn FnMut(Event) -> Mapped<T> + Send>,
    done: bool,
}

impl<T> EventStream<T> {
    pub(crate) fn new(
        raw: SseStream,
        map: impl FnMut(Event) -> Mapped<T> + Send + 'static,
    ) -> Self {
        Self {
            raw,
            map: Box::new(map),
            done: false,
        }
    }
}

impl<T> Unpin for EventStream<T> {}

impl<T> Stream for EventStream<T> {
    type Item = Result<T>;
    fn poll_next(mut self: Pin<&mut Self>, cx: &mut Context<'_>) -> Poll<Option<Self::Item>> {
        if self.done {
            return Poll::Ready(None);
        }
        match Pin::new(&mut self.raw).poll_next(cx) {
            Poll::Pending => Poll::Pending,
            Poll::Ready(None) => Poll::Ready(None),
            Poll::Ready(Some(Err(e))) => {
                if is_fatal(&e) {
                    self.done = true;
                    self.raw.shutdown();
                }
                Poll::Ready(Some(Err(e)))
            }
            Poll::Ready(Some(Ok(ev))) => match (self.map)(ev) {
                Mapped::Item(t) => Poll::Ready(Some(Ok(t))),
                Mapped::Soft(e) => Poll::Ready(Some(Err(e))),
                Mapped::Fatal(e) => {
                    self.done = true;
                    self.raw.shutdown();
                    Poll::Ready(Some(Err(e)))
                }
            },
        }
    }
}

/// Decode a JSON `data:` payload, reporting an `event: error` frame as a stream error.
pub(crate) fn json_frame<T: serde::de::DeserializeOwned>(ev: &Event) -> Mapped<T> {
    if ev.event.as_deref() == Some("error") {
        return Mapped::Fatal(Error::Stream(ev.data.clone()));
    }
    match decode::<T>(ev.data.as_bytes()) {
        Ok(t) => Mapped::Item(t),
        Err(e) => Mapped::Soft(e),
    }
}

/// A frame of `GET /api/os/events`. Frames never carry a payload, only the
/// kind: refetch the data the kind names.
#[derive(Debug, Clone, PartialEq, Deserialize, Default)]
pub struct OsEvent {
    /// Event kind, for example `notification.added`, `decision.answered`,
    /// `events.heartbeat` or `events.lagged`.
    #[serde(default)]
    pub kind: String,
    /// Trace id of the event, when it has one.
    #[serde(default)]
    pub id: Option<String>,
    /// Server time in epoch seconds.
    #[serde(default)]
    pub ts: Option<f64>,
    /// Present on `events.lagged`: how many events were dropped.
    #[serde(default)]
    pub dropped: Option<u64>,
}

impl OsEvent {
    /// True for the 10 s `events.heartbeat` liveness frame.
    pub fn is_heartbeat(&self) -> bool {
        self.kind == "events.heartbeat"
    }
    /// True when the server dropped events for this subscriber: refetch everything.
    pub fn is_lagged(&self) -> bool {
        self.kind == "events.lagged"
    }
}

/// A frame of `GET /api/events/stream`.
#[derive(Debug, Clone, PartialEq, Deserialize, Default)]
pub struct ServerEvent {
    /// Event kind (the JSON key is `type`), for example `notification.added`.
    #[serde(default, rename = "type")]
    pub kind: String,
    /// Kind-specific payload.
    #[serde(default)]
    pub payload: serde_json::Value,
    /// Server time in epoch seconds.
    #[serde(default)]
    pub ts: Option<f64>,
    /// Stable trace id, usable to de-duplicate across reconnects.
    #[serde(default)]
    pub id: Option<String>,
}

impl ServerEvent {
    /// For `notification.added`, the notification row carried in the payload.
    pub fn notification(&self) -> Option<Notification> {
        if self.kind != "notification.added" {
            return None;
        }
        serde_json::from_value(self.payload.clone()).ok()
    }
}

impl Client {
    /// `GET /api/os/events?kinds=a,b`: change notifications without payloads.
    /// An empty `kinds` slice subscribes to every kind. The server keeps a
    /// 32-event replay buffer per channel and ignores `Last-Event-ID`.
    pub fn os_events(&self, kinds: &[&str]) -> EventStream<OsEvent> {
        let query = if kinds.is_empty() {
            Vec::new()
        } else {
            vec![("kinds".to_string(), kinds.join(","))]
        };
        let opts = SseOptions {
            resume: false,
            ..SseOptions::default()
        };
        EventStream::new(self.sse("/api/os/events", query, opts), |ev| {
            json_frame(&ev)
        })
    }

    /// `GET /api/events/stream`: system events with payloads. Its `id:` lines
    /// are per-connection counters, so `Last-Event-ID` resume is disabled;
    /// use [`ServerEvent::id`] to de-duplicate instead.
    pub fn event_stream(&self) -> EventStream<ServerEvent> {
        let opts = SseOptions {
            resume: false,
            ..SseOptions::default()
        };
        EventStream::new(self.sse("/api/events/stream", Vec::new(), opts), |ev| {
            json_frame(&ev)
        })
    }
}

// ---------------------------------------------------------------------------
// PollWhileVisible
// ---------------------------------------------------------------------------

#[derive(Clone, Copy, PartialEq, Eq)]
struct Vis {
    visible: bool,
    /// Incremented every time the poller goes from hidden to shown.
    shown_epoch: u64,
}

/// A cancellable periodic poller that only runs while visible.
///
/// The action runs once immediately if visible, then again `period` after the
/// previous run finished (runs never overlap). [`set_visible(false)`](Self::set_visible)
/// pauses it, and [`set_visible(true)`](Self::set_visible) fires it again
/// immediately. [`poll_now`](Self::poll_now) triggers a manual refresh.
/// Dropping the handle stops the poller.
///
/// Must be created inside a Tokio runtime.
pub struct PollWhileVisible {
    vis: watch::Sender<Vis>,
    now: std::sync::Arc<Notify>,
    task: JoinHandle<()>,
}

impl PollWhileVisible {
    /// Start polling. `action` is called for every tick and its future is
    /// awaited to completion before the next wait starts.
    pub fn spawn<F, Fut>(period: Duration, initially_visible: bool, mut action: F) -> Self
    where
        F: FnMut() -> Fut + Send + 'static,
        Fut: Future<Output = ()> + Send + 'static,
    {
        let (vis, mut rx) = watch::channel(Vis {
            visible: initially_visible,
            shown_epoch: 0,
        });
        let now = std::sync::Arc::new(Notify::new());
        let wake = now.clone();
        let task = tokio::spawn(async move {
            loop {
                // Wait until visible.
                loop {
                    if rx.borrow_and_update().visible {
                        break;
                    }
                    if rx.changed().await.is_err() {
                        return;
                    }
                }
                let epoch = rx.borrow_and_update().shown_epoch;
                action().await;
                // Sleep until the period elapses, a manual poll is requested,
                // the poller is hidden, or it is hidden and shown again.
                let deadline = Instant::now() + period;
                loop {
                    tokio::select! {
                        _ = tokio::time::sleep_until(deadline) => break,
                        _ = wake.notified() => break,
                        changed = rx.changed() => {
                            if changed.is_err() {
                                return;
                            }
                            let state = *rx.borrow_and_update();
                            if !state.visible {
                                break; // outer loop waits for visibility
                            }
                            if state.shown_epoch != epoch {
                                break; // shown again: fire immediately
                            }
                        }
                    }
                }
            }
        });
        Self { vis, now, task }
    }

    /// Tell the poller whether its UI is on screen. Hiding pauses it; showing
    /// it again fires the action immediately. Repeating the current state is a
    /// no-op.
    pub fn set_visible(&self, visible: bool) {
        self.vis.send_if_modified(|v| {
            if v.visible == visible {
                return false;
            }
            v.visible = visible;
            if visible {
                v.shown_epoch += 1;
            }
            true
        });
    }

    /// Whether the poller currently considers itself visible.
    pub fn is_visible(&self) -> bool {
        self.vis.borrow().visible
    }

    /// Run the action as soon as possible even if the period has not elapsed
    /// (a manual refresh button). Has no effect while hidden.
    pub fn poll_now(&self) {
        self.now.notify_one();
    }

    /// Stop the poller. Equivalent to dropping the handle.
    pub fn stop(self) {}
}

impl Drop for PollWhileVisible {
    fn drop(&mut self) {
        self.task.abort();
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn feed_all(chunks: &[&[u8]]) -> Vec<Event> {
        let mut p = SseParser::default();
        chunks.iter().flat_map(|c| p.feed(c)).collect()
    }

    #[test]
    fn multi_line_data_and_comments() {
        let evs = feed_all(&[b": hello\n\nid: 7\nevent: x\ndata: a\ndata: b\n\n"]);
        assert_eq!(evs.len(), 1);
        assert_eq!(evs[0].data, "a\nb");
        assert_eq!(evs[0].event.as_deref(), Some("x"));
        assert_eq!(evs[0].id.as_deref(), Some("7"));
    }

    #[test]
    fn crlf_and_split_chunks() {
        let evs = feed_all(&[b"data: on", b"e\r", b"\n\r", b"\ndata: two\r\r\n"]);
        assert_eq!(evs.len(), 2);
        assert_eq!(evs[0].data, "one");
        assert_eq!(evs[1].data, "two");
    }

    #[test]
    fn bom_and_no_data_frames() {
        let evs = feed_all(&[b"\xEF\xBB\xBFdata: x\n\nevent: nodata\n\n"]);
        assert_eq!(evs.len(), 1);
        assert_eq!(evs[0].data, "x");
    }

    #[test]
    fn id_persists_until_changed() {
        let evs = feed_all(&[b"id: 1\ndata: a\n\ndata: b\n\n"]);
        assert_eq!(evs[1].id.as_deref(), Some("1"));
    }

    #[test]
    fn backoff_is_capped() {
        let b = Backoff::default();
        assert_eq!(b.ceiling(0), Duration::from_millis(500));
        assert_eq!(b.ceiling(3), Duration::from_secs(4));
        assert_eq!(b.ceiling(40), Duration::from_secs(30));
        for attempt in 0..64 {
            let d = b.delay(attempt);
            assert!(d <= Duration::from_secs(30), "attempt {attempt}: {d:?}");
            assert!(d >= b.ceiling(attempt) / 2);
        }
    }
}
