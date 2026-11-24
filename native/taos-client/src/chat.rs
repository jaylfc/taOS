// SPDX-License-Identifier: AGPL-3.0-or-later
//! Chat: channels, messages and the live socket `WS /ws/chat`.
//!
//! Source: `tinyagentos/routes/chat.py` (REST routes and the socket),
//! `tinyagentos/chat/hub.py` (fan-out), and the web app
//! `desktop/src/apps/MessagesApp/index.tsx` (what it calls and which frames it
//! handles).
//!
//! The socket authenticates with the `taos_session` cookie only (the route reads
//! nothing else) and the controller binds that cookie to the `User-Agent`, so the
//! handshake sends the exact `User-Agent` of the signed-in client. The hub only
//! sends a channel's frames to sockets that joined it, so the stream keeps a set
//! of joined channels ([`Chat::set_joined`]) and re-joins it after every
//! reconnect, as the web app does.
//!
//! The socket is a small hand-written RFC 6455 client over a plain TCP stream
//! (no new dependency): `ws://` only, which is what a desktop controller on
//! loopback or the LAN serves. A controller behind `https://` gets
//! [`Error::Unsupported`] (a TLS socket needs `tokio-rustls`, a later change).

use std::collections::{BTreeSet, HashMap};
use std::pin::Pin;
use std::sync::{Arc, Mutex, OnceLock};
use std::task::{Context, Poll};
use std::time::Duration;

use futures_util::Stream;
use serde::{Deserialize, Deserializer, Serialize};
use serde_json::{json, Value};
use tokio::io::{AsyncReadExt, AsyncWriteExt};
use tokio::net::TcpStream;
use tokio::sync::{mpsc, Notify};
use tokio::task::JoinHandle;
use tokio::time::Instant;

use crate::client::{Client, Req};
use crate::error::{Error, Result};
use crate::events::{Event, SseOptions, CONNECTED_EVENT};
use crate::util::{encode_segment, Timestamp};

/// The path of the chat socket. [`Client::chat_stream`] and the FFI stream open
/// route this path to the socket instead of Server-Sent Events.
pub const WS_PATH: &str = "/ws/chat";

/// The `event:` name of every frame the chat stream yields.
pub const CHAT_EVENT: &str = "chat";

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

fn secs<'de, D: Deserializer<'de>>(d: D) -> std::result::Result<f64, D::Error> {
    Ok(Option::<Timestamp>::deserialize(d)?
        .and_then(|t| t.epoch_secs())
        .unwrap_or(0.0))
}

fn opt_secs<'de, D: Deserializer<'de>>(d: D) -> std::result::Result<Option<f64>, D::Error> {
    Ok(Option::<Timestamp>::deserialize(d)?.and_then(|t| t.epoch_secs()))
}

fn null_vec<'de, D: Deserializer<'de>>(d: D) -> std::result::Result<Vec<String>, D::Error> {
    Ok(Option::<Vec<Value>>::deserialize(d)?
        .unwrap_or_default()
        .into_iter()
        .filter_map(|v| match v {
            Value::String(s) => Some(s),
            _ => None,
        })
        .collect())
}

/// A chat channel (`GET /api/chat/channels`). `kind` of channel is in
/// [`Channel::kind`] (`dm`, `dm-remote`, `topic`, `group`); `settings` is kept as
/// the server sent it (`archived`, `muted`, `kind: "a2a"`, `taostalk_agent`...).
#[derive(Debug, Clone, PartialEq, Deserialize, Serialize, Default)]
pub struct Channel {
    /// Channel id.
    #[serde(default)]
    pub id: String,
    /// Display name (for an agent DM, the agent's name).
    #[serde(default)]
    pub name: String,
    /// `dm`, `dm-remote`, `topic` or `group`.
    #[serde(default, rename = "type")]
    pub kind: String,
    /// Description, when set.
    #[serde(default)]
    pub description: Option<String>,
    /// Topic line, when set.
    #[serde(default)]
    pub topic: Option<String>,
    /// Member ids (`user` is the human, anything else an agent or person).
    #[serde(default, deserialize_with = "null_vec")]
    pub members: Vec<String>,
    /// Creation time, epoch seconds.
    #[serde(default, deserialize_with = "opt_secs")]
    pub created_at: Option<f64>,
    /// Time of the newest message, epoch seconds.
    #[serde(default, deserialize_with = "opt_secs")]
    pub last_message_at: Option<f64>,
    /// Text of the newest message (set by the list route).
    #[serde(default)]
    pub last_preview: Option<String>,
    /// Author of the newest message.
    #[serde(default)]
    pub last_preview_author: Option<String>,
    /// Owning project, when the channel belongs to one.
    #[serde(default)]
    pub project_id: Option<String>,
    /// Free-form settings object.
    #[serde(default)]
    pub settings: Value,
}

impl Channel {
    /// `settings.kind`, for example `a2a`.
    pub fn settings_kind(&self) -> Option<&str> {
        self.settings.get("kind").and_then(Value::as_str)
    }
}

/// One chat message, as the list route and the `message` frame carry it. The
/// optional structured parts (`content_blocks`, `metadata`, `reactions`,
/// `attachments`) are passed through as the server sent them.
#[derive(Debug, Clone, PartialEq, Deserialize, Serialize, Default)]
pub struct ChatMessage {
    /// Message id (`uuid4().hex[:12]`).
    #[serde(default)]
    pub id: String,
    /// The channel it belongs to.
    #[serde(default)]
    pub channel_id: String,
    /// Author id: a user id or an agent slug (`system` for the server).
    #[serde(default)]
    pub author_id: String,
    /// `user`, `agent` or `system`.
    #[serde(default)]
    pub author_type: String,
    /// Plain text or markdown.
    #[serde(default)]
    pub content: String,
    /// The parent message id when this is a thread reply.
    #[serde(default)]
    pub thread_id: Option<String>,
    /// `text` or `canvas`.
    #[serde(default)]
    pub content_type: Option<String>,
    /// Structured turn content (`text`, `thinking`, `tool_call`, `status`, ...).
    #[serde(default)]
    pub content_blocks: Option<Value>,
    /// Message metadata (`canvas_url`, ...).
    #[serde(default)]
    pub metadata: Option<Value>,
    /// `pending`, `streaming`, `complete` or `error`.
    #[serde(default)]
    pub state: Option<String>,
    /// Creation time, epoch seconds (strings and milliseconds are normalised).
    #[serde(default, deserialize_with = "secs")]
    pub created_at: f64,
    /// `emoji -> [author ids]`.
    #[serde(default)]
    pub reactions: Option<Value>,
    /// Edit time, epoch seconds.
    #[serde(default, deserialize_with = "opt_secs")]
    pub edited_at: Option<f64>,
    /// Soft-delete time, epoch seconds.
    #[serde(default, deserialize_with = "opt_secs")]
    pub deleted_at: Option<f64>,
    /// Attachment records.
    #[serde(default)]
    pub attachments: Option<Value>,
    /// Replies in this message's thread.
    #[serde(default)]
    pub reply_count: Option<i64>,
    /// Time of the newest reply.
    #[serde(default, deserialize_with = "opt_secs")]
    pub last_reply_at: Option<f64>,
}

/// A registered agent, as the chat lists need it (`GET /api/agents`).
#[derive(Debug, Clone, PartialEq, Deserialize, Serialize, Default)]
pub struct AgentRef {
    /// Agent slug.
    #[serde(default)]
    pub name: String,
    /// Display name.
    #[serde(default)]
    pub display_name: Option<String>,
    /// Emoji, when set.
    #[serde(default)]
    pub emoji: Option<String>,
    /// Framework id.
    #[serde(default)]
    pub framework: Option<String>,
    /// Model id.
    #[serde(default)]
    pub model: Option<String>,
    /// `running`, `paused`, ...
    #[serde(default)]
    pub status: Option<String>,
}

/// An archived agent (`GET /api/agents/archived`).
#[derive(Debug, Clone, PartialEq, Deserialize, Serialize, Default)]
pub struct ArchivedAgentRef {
    /// Archive id.
    #[serde(default)]
    pub id: String,
    /// Slug the agent was archived under.
    #[serde(default)]
    pub archived_slug: String,
    /// The agent as it was (`name`, `display_name`).
    #[serde(default)]
    pub original: Value,
}

/// The result of sending text.
#[derive(Debug, Clone, PartialEq)]
pub enum SendOutcome {
    /// The stored message.
    Sent(Box<ChatMessage>),
    /// The server handled the text itself (`/help`) and broadcast its own
    /// system message; nothing of the user's was stored.
    Handled {
        /// What handled it (`help`).
        handled: String,
        /// The server's reply, when it sent one.
        system_message: Option<Box<ChatMessage>>,
    },
}

// ---------------------------------------------------------------------------
// Live frames
// ---------------------------------------------------------------------------

/// One frame of the chat socket, parsed. Unknown `type`s are [`ChatEvent::Other`]
/// so a newer server never breaks an older client; a frame that is not JSON, has
/// no `type` or has fields of the wrong type is an error ([`ChatEvent::parse`])
/// and the stream drops it.
#[derive(Debug, Clone, PartialEq, Deserialize, Serialize)]
#[serde(tag = "type", rename_all = "snake_case")]
pub enum ChatEvent {
    /// A new message (also the broadcast of the caller's own).
    Message(ChatMessage),
    /// More text for a streaming message. Carries no channel id.
    MessageDelta {
        /// The message.
        message_id: String,
        /// Text to append.
        #[serde(default)]
        delta: String,
    },
    /// A message changed state (`complete`, `error`, ...).
    MessageState {
        /// The message.
        message_id: String,
        /// The new state.
        #[serde(default)]
        state: String,
    },
    /// Somebody is typing: the phase 2a frame (`kind`: `human`/`agent`, `slug`)
    /// or the legacy one (`user_id`, `user_type`). Neither carries a channel id
    /// for the phase 2a form.
    Typing {
        /// `human` or `agent` (phase 2a).
        #[serde(default)]
        kind: Option<String>,
        /// Who (phase 2a).
        #[serde(default)]
        slug: Option<String>,
        /// Who (legacy).
        #[serde(default)]
        user_id: Option<String>,
        /// `user` or `agent` (legacy).
        #[serde(default)]
        user_type: Option<String>,
        /// The channel, on the legacy form.
        #[serde(default)]
        channel_id: Option<String>,
    },
    /// An agent started or stopped working.
    Thinking {
        /// The agent.
        slug: String,
        /// `start` or `end`.
        #[serde(default)]
        state: String,
        /// `thinking`, `tool`, `reading`, `writing`, `searching`, `planning`.
        #[serde(default)]
        phase: Option<String>,
        /// A short detail line.
        #[serde(default)]
        detail: Option<String>,
    },
    /// The reactions of a message changed.
    ReactionUpdate {
        /// The message.
        message_id: String,
        /// `emoji -> [author ids]`.
        #[serde(default)]
        reactions: Value,
    },
    /// A message was edited.
    MessageEdit {
        /// The message.
        message_id: String,
        /// New text, when it changed.
        #[serde(default)]
        content: Option<String>,
        /// Edit time.
        #[serde(default, deserialize_with = "opt_secs")]
        edited_at: Option<f64>,
        /// New metadata, when it changed.
        #[serde(default)]
        metadata: Option<Value>,
        /// New blocks, when they changed.
        #[serde(default)]
        content_blocks: Option<Value>,
    },
    /// A message was deleted (soft: the row stays as a tombstone).
    MessageDelete {
        /// The message.
        message_id: String,
        /// The channel, when sent.
        #[serde(default)]
        channel_id: Option<String>,
        /// Deletion time, when sent.
        #[serde(default, deserialize_with = "opt_secs")]
        deleted_at: Option<f64>,
    },
    /// A delivery or seen receipt.
    Receipt {
        /// The message.
        message_id: String,
        /// The reader.
        #[serde(default)]
        agent_id: String,
        /// `agent` or `user`.
        #[serde(default)]
        reader_type: Option<String>,
        /// Delivery time.
        #[serde(default, deserialize_with = "opt_secs")]
        delivered_at: Option<f64>,
        /// Seen time.
        #[serde(default, deserialize_with = "opt_secs")]
        seen_at: Option<f64>,
    },
    /// The server refused something this socket sent.
    Error {
        /// The channel, when sent.
        #[serde(default)]
        channel_id: Option<String>,
        /// The reason.
        #[serde(default)]
        error: String,
    },
    /// A frame type this version does not know.
    #[serde(other)]
    Other,
}

impl ChatEvent {
    /// Parse one text frame.
    pub fn parse(text: &str) -> Result<Self> {
        crate::client::decode(text.as_bytes())
    }
}

// ---------------------------------------------------------------------------
// REST
// ---------------------------------------------------------------------------

/// Chat calls. Obtain with [`Client::chat`].
#[derive(Debug, Clone)]
pub struct Chat {
    client: Client,
}

impl Client {
    /// Chat calls and the chat socket.
    pub fn chat(&self) -> Chat {
        Chat {
            client: self.clone(),
        }
    }

    /// Open the chat socket as a stream of [`Event`]s: a synthetic
    /// [`CONNECTED_EVENT`] on every (re)connection, then one [`CHAT_EVENT`] per
    /// frame whose `data` is the parsed frame re-serialised as JSON (the shape
    /// of [`ChatEvent`]). Reconnects with `opts.backoff` and re-joins the
    /// channels set with [`Chat::set_joined`]. Must be called inside a Tokio
    /// runtime. Dropping the stream closes the socket.
    pub fn chat_stream(&self, opts: SseOptions) -> ChatStream {
        let (tx, rx) = mpsc::channel(256);
        let client = self.clone();
        let hub = hub_for(client.base_url());
        let task = tokio::spawn(run_socket(client, hub, opts, tx));
        ChatStream { rx, task }
    }
}

impl Chat {
    /// `GET /api/chat/channels[?archived=true][&project_id=..]`.
    pub async fn channels(
        &self,
        archived: bool,
        project_id: Option<&str>,
    ) -> Result<Vec<Channel>> {
        let mut req = Req::get("/api/chat/channels");
        if archived {
            req = req.query("archived", "true");
        }
        req = req.opt_query("project_id", project_id);
        let v: Value = self.client.json(req).await?;
        list_of(&v, "channels")
    }

    /// `GET /api/chat/unread` -> channel id to unread count (zero counts dropped).
    pub async fn unread(&self) -> Result<Vec<(String, u64)>> {
        let v: Value = self.client.json(Req::get("/api/chat/unread")).await?;
        let mut out: Vec<(String, u64)> = v
            .get("unread")
            .and_then(Value::as_object)
            .map(|m| {
                m.iter()
                    .filter_map(|(k, n)| n.as_u64().filter(|n| *n > 0).map(|n| (k.clone(), n)))
                    .collect()
            })
            .unwrap_or_default();
        out.sort();
        Ok(out)
    }

    /// `GET /api/chat/channels/{id}/messages?limit=&before=`, oldest first.
    /// `before` is the `created_at` (epoch seconds) of the oldest message you
    /// have; the server returns the `limit` messages before it.
    pub async fn messages(
        &self,
        channel_id: &str,
        limit: u32,
        before: Option<f64>,
    ) -> Result<Vec<ChatMessage>> {
        let req = Req::get(format!(
            "/api/chat/channels/{}/messages",
            encode_segment(channel_id)
        ))
        .query("limit", limit)
        .opt_query("before", before);
        let v: Value = self.client.json(req).await?;
        list_of(&v, "messages")
    }

    /// `POST /api/chat/messages`: send text as the signed-in user. The route
    /// indexes `author_id` directly, so it is always sent; the controller
    /// replaces it with the session user.
    pub async fn send(
        &self,
        channel_id: &str,
        content: &str,
        thread_id: Option<&str>,
    ) -> Result<SendOutcome> {
        let mut body = json!({
            "channel_id": channel_id,
            "author_id": "user",
            "author_type": "user",
            "content": content,
            "content_type": "text",
        });
        if let Some(t) = thread_id {
            body["thread_id"] = Value::String(t.to_string());
        }
        let req = Req::post("/api/chat/messages").json_body(&body)?;
        let v: Value = self.client.json(req).await?;
        if let Some(h) = v.get("handled").and_then(Value::as_str) {
            let sys = v
                .get("system_message")
                .filter(|s| s.is_object())
                .map(|s| serde_json::from_value::<ChatMessage>(s.clone()))
                .transpose()
                .map_err(|e| Error::Protocol(format!("system_message: {e}")))?;
            return Ok(SendOutcome::Handled {
                handled: h.to_string(),
                system_message: sys.map(Box::new),
            });
        }
        let m: ChatMessage = serde_json::from_value(v)
            .map_err(|e| Error::Protocol(format!("sent message: {e}")))?;
        Ok(SendOutcome::Sent(Box::new(m)))
    }

    /// `POST /api/chat/channels/{id}/mark-read` (an empty body marks the whole
    /// channel read).
    pub async fn mark_read(&self, channel_id: &str, message_id: Option<&str>) -> Result<()> {
        let body = match message_id {
            Some(m) => json!({ "message_id": m }),
            None => json!({}),
        };
        let req = Req::post(format!(
            "/api/chat/channels/{}/mark-read",
            encode_segment(channel_id)
        ))
        .json_body(&body)?;
        self.client.send(&req).await?;
        Ok(())
    }

    /// `GET /api/agents[?include_guests=1]`.
    pub async fn agents(&self, include_guests: bool) -> Result<Vec<AgentRef>> {
        let mut req = Req::get("/api/agents");
        if include_guests {
            req = req.query("include_guests", 1);
        }
        let v: Value = self.client.json(req).await?;
        list_of(&v, "agents")
    }

    /// `GET /api/agents/archived`.
    pub async fn archived_agents(&self) -> Result<Vec<ArchivedAgentRef>> {
        let v: Value = self.client.json(Req::get("/api/agents/archived")).await?;
        list_of(&v, "agents")
    }

    /// `POST /api/chat/channels/{id}/typing`: tell the channel the human is typing.
    pub async fn typing(&self, channel_id: &str, author_id: &str) -> Result<()> {
        let req = Req::post(format!(
            "/api/chat/channels/{}/typing",
            encode_segment(channel_id)
        ))
        .json_body(&json!({ "author_id": author_id }))?;
        self.client.send(&req).await?;
        Ok(())
    }

    /// Replace the set of channels the socket is joined to. Takes effect on the
    /// live socket at once and is re-applied after every reconnect.
    pub fn set_joined<I: IntoIterator<Item = String>>(&self, channels: I) {
        let hub = hub_for(self.client.base_url());
        *lock(&hub.joined) = channels.into_iter().collect();
        hub.wake.notify_one();
    }

    /// Run a named operation (`chat.channels`, `chat.messages`, ...). This is
    /// what the C ABI's `taos_call` dispatches to; arguments and results are
    /// JSON.
    pub async fn run(&self, op: &str, args: Value) -> Result<Value> {
        let s = |k: &str| args.get(k).and_then(Value::as_str);
        let need = |k: &str| {
            s(k).ok_or_else(|| Error::InvalidInput(format!("missing string argument `{k}`")))
        };
        let to = |v: &dyn erased::Ser| v.to_value();
        match op {
            "chat.channels" => {
                let archived = args
                    .get("archived")
                    .and_then(Value::as_bool)
                    .unwrap_or(false);
                to(&self.channels(archived, s("project_id")).await?)
            }
            "chat.unread" => Ok(Value::Object(
                self.unread()
                    .await?
                    .into_iter()
                    .map(|(k, n)| (k, Value::from(n)))
                    .collect(),
            )),
            "chat.messages" => {
                let limit = args
                    .get("limit")
                    .and_then(Value::as_u64)
                    .map_or(50, |n| n.clamp(1, 200) as u32);
                let before = args.get("before").and_then(Value::as_f64);
                to(&self.messages(need("channel_id")?, limit, before).await?)
            }
            "chat.send" => {
                let out = self
                    .send(need("channel_id")?, need("content")?, s("thread_id"))
                    .await?;
                match out {
                    SendOutcome::Sent(m) => Ok(json!({ "message": &*m })),
                    SendOutcome::Handled {
                        handled,
                        system_message,
                    } => Ok(json!({ "handled": handled, "system_message": system_message })),
                }
            }
            "chat.mark_read" => {
                self.mark_read(need("channel_id")?, s("message_id")).await?;
                Ok(Value::Null)
            }
            "chat.agents" => to(&self
                .agents(
                    args.get("include_guests")
                        .and_then(Value::as_bool)
                        .unwrap_or(true),
                )
                .await?),
            "chat.archived_agents" => to(&self.archived_agents().await?),
            "chat.typing" => {
                self.typing(need("channel_id")?, s("author_id").unwrap_or("user"))
                    .await?;
                Ok(Value::Null)
            }
            "chat.join" => {
                let ids = args
                    .get("channel_ids")
                    .and_then(Value::as_array)
                    .ok_or_else(|| {
                        Error::InvalidInput("missing array argument `channel_ids`".into())
                    })?
                    .iter()
                    .filter_map(|v| v.as_str().map(str::to_string))
                    .collect::<Vec<_>>();
                self.set_joined(ids);
                Ok(Value::Null)
            }
            other => Err(Error::InvalidInput(format!("unknown operation `{other}`"))),
        }
    }
}

/// Small helper so `run` can serialise any result with one closure.
mod erased {
    use super::*;
    pub trait Ser {
        fn to_value(&self) -> Result<Value>;
    }
    impl<T: Serialize> Ser for T {
        fn to_value(&self) -> Result<Value> {
            serde_json::to_value(self).map_err(|e| Error::InvalidInput(e.to_string()))
        }
    }
}

/// The array in `v` (or in `v[key]`), each element decoded; one bad element
/// fails the call with a decode error naming it.
fn list_of<T: serde::de::DeserializeOwned>(v: &Value, key: &str) -> Result<Vec<T>> {
    let arr = match v {
        Value::Array(a) => a,
        Value::Object(o) => match o.get(key) {
            Some(Value::Array(a)) => a,
            None | Some(Value::Null) => return Ok(Vec::new()),
            Some(_) => {
                return Err(Error::Protocol(format!("`{key}` is not a list")));
            }
        },
        _ => return Ok(Vec::new()),
    };
    arr.iter()
        .enumerate()
        .map(|(i, e)| {
            serde_json::from_value(e.clone())
                .map_err(|err| Error::Protocol(format!("{key}[{i}]: {err}")))
        })
        .collect()
}

// ---------------------------------------------------------------------------
// The socket: the hub (what to join), the stream, the connection
// ---------------------------------------------------------------------------

/// What the socket task shares with [`Chat::set_joined`], one per controller.
struct Hub {
    joined: Mutex<BTreeSet<String>>,
    wake: Notify,
}

fn lock<T>(m: &Mutex<T>) -> std::sync::MutexGuard<'_, T> {
    m.lock().unwrap_or_else(|p| p.into_inner())
}

fn hub_for(base_url: &str) -> Arc<Hub> {
    static HUBS: OnceLock<Mutex<HashMap<String, Arc<Hub>>>> = OnceLock::new();
    let map = HUBS.get_or_init(|| Mutex::new(HashMap::new()));
    lock(map)
        .entry(base_url.to_string())
        .or_insert_with(|| {
            Arc::new(Hub {
                joined: Mutex::new(BTreeSet::new()),
                wake: Notify::new(),
            })
        })
        .clone()
}

/// A reconnecting chat socket as a stream of [`Event`]s. See [`Client::chat_stream`].
pub struct ChatStream {
    rx: mpsc::Receiver<Result<Event>>,
    task: JoinHandle<()>,
}

impl Drop for ChatStream {
    fn drop(&mut self) {
        self.task.abort();
    }
}

impl Stream for ChatStream {
    type Item = Result<Event>;
    fn poll_next(mut self: Pin<&mut Self>, cx: &mut Context<'_>) -> Poll<Option<Self::Item>> {
        self.rx.poll_recv(cx)
    }
}

/// A socket that must not be retried (a retry cannot succeed until the caller
/// changes something): sign-in, TLS, a refusing route.
fn fatal(e: &Error) -> bool {
    !matches!(
        e,
        Error::Disconnected | Error::Server { .. } | Error::RateLimited { .. }
    )
}

async fn run_socket(
    client: Client,
    hub: Arc<Hub>,
    opts: SseOptions,
    tx: mpsc::Sender<Result<Event>>,
) {
    let mut attempt: u32 = 0;
    loop {
        match Socket::connect(&client).await {
            Err(e) => {
                let end = fatal(&e);
                if tx.send(Err(e)).await.is_err() || end {
                    return;
                }
            }
            Ok(mut sock) => {
                let started = Instant::now();
                let ev = Event {
                    event: Some(CONNECTED_EVENT.to_string()),
                    ..Event::default()
                };
                if tx.send(Ok(ev)).await.is_err() {
                    return;
                }
                let err = pump(&mut sock, &hub, &opts, &tx).await;
                if tx.is_closed() {
                    return;
                }
                if started.elapsed() > Duration::from_secs(10) {
                    attempt = 0; // a connection that lived is not a flapping one
                }
                if tx.send(Err(err)).await.is_err() {
                    return;
                }
            }
        }
        tokio::time::sleep(opts.backoff.delay(attempt)).await;
        attempt = attempt.saturating_add(1);
    }
}

/// Read frames until the socket ends; returns why. Applies join changes and
/// keeps the connection alive with pings.
async fn pump(
    sock: &mut Socket,
    hub: &Hub,
    opts: &SseOptions,
    tx: &mpsc::Sender<Result<Event>>,
) -> Error {
    let mut sent: BTreeSet<String> = BTreeSet::new();
    let idle = opts.idle_timeout.unwrap_or(Duration::from_secs(60));
    let mut ping = tokio::time::interval(Duration::from_secs(20));
    ping.tick().await;
    let mut last_rx = Instant::now();
    loop {
        // Join and leave to match the wanted set (also the first pass after a connect).
        let want = lock(&hub.joined).clone();
        for c in want.difference(&sent.clone()) {
            if sock
                .send_text(&json!({"type": "join", "channel_id": c}).to_string())
                .await
                .is_err()
            {
                return Error::Disconnected;
            }
        }
        for c in sent.difference(&want.clone()) {
            if sock
                .send_text(&json!({"type": "leave", "channel_id": c}).to_string())
                .await
                .is_err()
            {
                return Error::Disconnected;
            }
        }
        sent = want;
        tokio::select! {
            m = sock.next_message() => match m {
                Err(e) => return e,
                Ok(Msg::Text(t)) => {
                    last_rx = Instant::now();
                    // A frame this client cannot parse is dropped, never fatal.
                    if let Ok(ev) = ChatEvent::parse(&t) {
                        if let Ok(data) = serde_json::to_string(&ev) {
                            let item = Event {
                                event: Some(CHAT_EVENT.to_string()),
                                data,
                                ..Event::default()
                            };
                            if tx.send(Ok(item)).await.is_err() {
                                return Error::Disconnected;
                            }
                        }
                    }
                }
                Ok(Msg::Ping(p)) => {
                    last_rx = Instant::now();
                    if sock.send_frame(0xA, &p).await.is_err() {
                        return Error::Disconnected;
                    }
                }
                Ok(Msg::Pong) => last_rx = Instant::now(),
                Ok(Msg::Close) => return Error::Disconnected,
            },
            _ = hub.wake.notified() => {}
            _ = ping.tick() => {
                if last_rx.elapsed() > idle || sock.send_frame(0x9, b"").await.is_err() {
                    return Error::Disconnected;
                }
            }
        }
    }
}

enum Msg {
    Text(String),
    Ping(Vec<u8>),
    Pong,
    Close,
}

/// A connected socket: the stream plus the bytes read but not yet parsed.
struct Socket {
    stream: TcpStream,
    buf: Vec<u8>,
    frag: Vec<u8>,
}

const MAX_FRAME: usize = 16 * 1024 * 1024;

impl Socket {
    async fn connect(client: &Client) -> Result<Self> {
        let url = url::Url::parse(client.base_url())
            .map_err(|e| Error::InvalidInput(format!("base url: {e}")))?;
        if url.scheme() != "http" {
            return Err(Error::Unsupported(
                "the chat socket over TLS (https controllers)",
            ));
        }
        let host = url
            .host_str()
            .ok_or_else(|| Error::InvalidInput("base url has no host".into()))?
            .to_string();
        let port = url.port_or_known_default().unwrap_or(80);
        let session = client.export_session().ok_or(Error::Unauthenticated {
            message: "not signed in".into(),
        })?;
        let key = base64(&fastrand::u128(..).to_le_bytes());
        let host_header = match url.port() {
            Some(p) => format!("{host}:{p}"),
            None => host.clone(),
        };
        let req = format!(
            "GET {WS_PATH} HTTP/1.1\r\nHost: {host_header}\r\nUpgrade: websocket\r\nConnection: Upgrade\r\n\
             Sec-WebSocket-Key: {key}\r\nSec-WebSocket-Version: 13\r\nOrigin: http://{host_header}\r\n\
             User-Agent: {}\r\nCookie: {}={}\r\n\r\n",
            session.user_agent(),
            crate::session::SESSION_COOKIE,
            session.token()
        );
        let mut stream = tokio::time::timeout(
            Duration::from_secs(10),
            TcpStream::connect((host.as_str(), port)),
        )
        .await
        .map_err(|_| Error::Disconnected)?
        .map_err(|_| Error::Disconnected)?;
        let _ = stream.set_nodelay(true);
        stream
            .write_all(req.as_bytes())
            .await
            .map_err(|_| Error::Disconnected)?;
        // The response head.
        let mut buf = Vec::with_capacity(1024);
        let mut tmp = [0u8; 2048];
        let head_end = loop {
            if let Some(p) = find(&buf, b"\r\n\r\n") {
                break p;
            }
            if buf.len() > 16 * 1024 {
                return Err(Error::Protocol("oversized handshake response".into()));
            }
            let n = tokio::time::timeout(Duration::from_secs(10), stream.read(&mut tmp))
                .await
                .map_err(|_| Error::Disconnected)?
                .map_err(|_| Error::Disconnected)?;
            if n == 0 {
                return Err(Error::Disconnected);
            }
            buf.extend_from_slice(&tmp[..n]);
        };
        let head = String::from_utf8_lossy(&buf[..head_end]).to_string();
        let rest = buf[head_end + 4..].to_vec();
        check_handshake(&head, &key)?;
        Ok(Self {
            stream,
            buf: rest,
            frag: Vec::new(),
        })
    }

    async fn send_text(&mut self, s: &str) -> std::io::Result<()> {
        self.send_frame(0x1, s.as_bytes()).await
    }

    async fn send_frame(&mut self, opcode: u8, payload: &[u8]) -> std::io::Result<()> {
        let frame = encode_client_frame(opcode, payload, fastrand::u32(..).to_le_bytes());
        self.stream.write_all(&frame).await
    }

    /// The next data or control message. Cancel safe: the only await is a read
    /// into a scratch buffer, and parsing works on `self.buf`.
    async fn next_message(&mut self) -> Result<Msg> {
        let mut tmp = [0u8; 8192];
        loop {
            if let Some(m) = self.parse_one()? {
                return Ok(m);
            }
            let n = self
                .stream
                .read(&mut tmp)
                .await
                .map_err(|_| Error::Disconnected)?;
            if n == 0 {
                return Err(Error::Disconnected);
            }
            self.buf.extend_from_slice(&tmp[..n]);
        }
    }

    fn parse_one(&mut self) -> Result<Option<Msg>> {
        loop {
            let Some((fin, opcode, payload, used)) = parse_server_frame(&self.buf)? else {
                return Ok(None);
            };
            self.buf.drain(..used);
            match opcode {
                0x0 | 0x1 | 0x2 => {
                    // Continuation, text, binary. Binary is ignored (the hub only sends text).
                    self.frag.extend_from_slice(&payload);
                    if self.frag.len() > MAX_FRAME {
                        return Err(Error::Protocol("oversized message".into()));
                    }
                    if fin {
                        let data = std::mem::take(&mut self.frag);
                        if opcode != 0x2 {
                            if let Ok(t) = String::from_utf8(data) {
                                return Ok(Some(Msg::Text(t)));
                            }
                        }
                    }
                }
                0x8 => return Ok(Some(Msg::Close)),
                0x9 => return Ok(Some(Msg::Ping(payload))),
                0xA => return Ok(Some(Msg::Pong)),
                _ => return Err(Error::Protocol("unknown websocket opcode".into())),
            }
        }
    }
}

fn find(hay: &[u8], needle: &[u8]) -> Option<usize> {
    hay.windows(needle.len()).position(|w| w == needle)
}

/// Check the 101 response; map a refusal to the error a UI needs. A refused
/// session is closed by the route before accept, which arrives as HTTP 403.
fn check_handshake(head: &str, key: &str) -> Result<()> {
    let mut lines = head.split("\r\n");
    let status_line = lines.next().unwrap_or("");
    let status: u16 = status_line
        .split_whitespace()
        .nth(1)
        .and_then(|s| s.parse().ok())
        .ok_or_else(|| Error::Protocol("bad handshake response".into()))?;
    match status {
        101 => {}
        401 | 403 => {
            return Err(Error::Unauthenticated {
                message: "the chat socket refused the session".into(),
            })
        }
        s if s >= 500 => {
            return Err(Error::Server {
                status: s,
                message: "chat socket handshake failed".into(),
            })
        }
        s => {
            return Err(Error::Http {
                status: s,
                message: "chat socket handshake refused".into(),
            })
        }
    }
    let want = accept_key(key);
    let got = lines.find_map(|l| {
        let (k, v) = l.split_once(':')?;
        k.eq_ignore_ascii_case("sec-websocket-accept")
            .then(|| v.trim().to_string())
    });
    if got.as_deref() != Some(want.as_str()) {
        return Err(Error::Protocol("bad Sec-WebSocket-Accept".into()));
    }
    Ok(())
}

/// A masked client frame (RFC 6455 section 5.2).
fn encode_client_frame(opcode: u8, payload: &[u8], mask: [u8; 4]) -> Vec<u8> {
    let mut out = Vec::with_capacity(payload.len() + 14);
    out.push(0x80 | (opcode & 0x0F));
    match payload.len() {
        n if n < 126 => out.push(0x80 | n as u8),
        n if n <= 0xFFFF => {
            out.push(0x80 | 126);
            out.extend_from_slice(&(n as u16).to_be_bytes());
        }
        n => {
            out.push(0x80 | 127);
            out.extend_from_slice(&(n as u64).to_be_bytes());
        }
    }
    out.extend_from_slice(&mask);
    out.extend(payload.iter().enumerate().map(|(i, b)| b ^ mask[i % 4]));
    out
}

/// One frame from the server. `Ok(None)` when `buf` does not hold a whole frame
/// yet; otherwise `(fin, opcode, payload, bytes used)`.
#[allow(clippy::type_complexity)]
fn parse_server_frame(buf: &[u8]) -> Result<Option<(bool, u8, Vec<u8>, usize)>> {
    if buf.len() < 2 {
        return Ok(None);
    }
    let fin = buf[0] & 0x80 != 0;
    let opcode = buf[0] & 0x0F;
    let masked = buf[1] & 0x80 != 0;
    let mut len = (buf[1] & 0x7F) as usize;
    let mut pos = 2;
    if len == 126 {
        if buf.len() < 4 {
            return Ok(None);
        }
        len = u16::from_be_bytes([buf[2], buf[3]]) as usize;
        pos = 4;
    } else if len == 127 {
        if buf.len() < 10 {
            return Ok(None);
        }
        let mut b = [0u8; 8];
        b.copy_from_slice(&buf[2..10]);
        let l = u64::from_be_bytes(b);
        if l > MAX_FRAME as u64 {
            return Err(Error::Protocol("oversized frame".into()));
        }
        len = l as usize;
        pos = 10;
    }
    if len > MAX_FRAME {
        return Err(Error::Protocol("oversized frame".into()));
    }
    let mask = if masked {
        if buf.len() < pos + 4 {
            return Ok(None);
        }
        let m = [buf[pos], buf[pos + 1], buf[pos + 2], buf[pos + 3]];
        pos += 4;
        Some(m)
    } else {
        None
    };
    if buf.len() < pos + len {
        return Ok(None);
    }
    let mut payload = buf[pos..pos + len].to_vec();
    if let Some(m) = mask {
        for (i, b) in payload.iter_mut().enumerate() {
            *b ^= m[i % 4];
        }
    }
    Ok(Some((fin, opcode, payload, pos + len)))
}

/// `Sec-WebSocket-Accept` for a key (RFC 6455 section 1.3).
fn accept_key(key: &str) -> String {
    let mut data = key.as_bytes().to_vec();
    data.extend_from_slice(b"258EAFA5-E914-47DA-95CA-C5AB0DC85B11");
    base64(&sha1(&data))
}

fn base64(data: &[u8]) -> String {
    const T: &[u8; 64] = b"ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/";
    let mut out = String::with_capacity(data.len().div_ceil(3) * 4);
    for c in data.chunks(3) {
        let n = (u32::from(c[0]) << 16)
            | (u32::from(*c.get(1).unwrap_or(&0)) << 8)
            | u32::from(*c.get(2).unwrap_or(&0));
        out.push(T[(n >> 18) as usize & 63] as char);
        out.push(T[(n >> 12) as usize & 63] as char);
        out.push(if c.len() > 1 {
            T[(n >> 6) as usize & 63] as char
        } else {
            '='
        });
        out.push(if c.len() > 2 {
            T[n as usize & 63] as char
        } else {
            '='
        });
    }
    out
}

fn sha1(data: &[u8]) -> [u8; 20] {
    let mut h: [u32; 5] = [0x67452301, 0xEFCDAB89, 0x98BADCFE, 0x10325476, 0xC3D2E1F0];
    let mut msg = data.to_vec();
    msg.push(0x80);
    while msg.len() % 64 != 56 {
        msg.push(0);
    }
    msg.extend_from_slice(&((data.len() as u64) * 8).to_be_bytes());
    for chunk in msg.chunks(64) {
        let mut w = [0u32; 80];
        for i in 0..16 {
            w[i] = u32::from_be_bytes([
                chunk[4 * i],
                chunk[4 * i + 1],
                chunk[4 * i + 2],
                chunk[4 * i + 3],
            ]);
        }
        for i in 16..80 {
            w[i] = (w[i - 3] ^ w[i - 8] ^ w[i - 14] ^ w[i - 16]).rotate_left(1);
        }
        let [mut a, mut b, mut c, mut d, mut e] = h;
        for (i, wi) in w.iter().enumerate() {
            let (f, k) = match i {
                0..=19 => ((b & c) | (!b & d), 0x5A827999),
                20..=39 => (b ^ c ^ d, 0x6ED9EBA1),
                40..=59 => ((b & c) | (b & d) | (c & d), 0x8F1BBCDC),
                _ => (b ^ c ^ d, 0xCA62C1D6),
            };
            let t = a
                .rotate_left(5)
                .wrapping_add(f)
                .wrapping_add(e)
                .wrapping_add(k)
                .wrapping_add(*wi);
            e = d;
            d = c;
            c = b.rotate_left(30);
            b = a;
            a = t;
        }
        h[0] = h[0].wrapping_add(a);
        h[1] = h[1].wrapping_add(b);
        h[2] = h[2].wrapping_add(c);
        h[3] = h[3].wrapping_add(d);
        h[4] = h[4].wrapping_add(e);
    }
    let mut out = [0u8; 20];
    for (i, v) in h.iter().enumerate() {
        out[4 * i..4 * i + 4].copy_from_slice(&v.to_be_bytes());
    }
    out
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::events::Backoff;

    fn fx(name: &str) -> String {
        let p = std::path::Path::new(env!("CARGO_MANIFEST_DIR"))
            .join("tests")
            .join("fixtures")
            .join(name);
        std::fs::read_to_string(p).unwrap_or_else(|e| panic!("fixture {name}: {e}"))
    }

    #[test]
    fn rfc6455_example_accept_key() {
        // RFC 6455 section 1.3.
        assert_eq!(
            accept_key("dGhlIHNhbXBsZSBub25jZQ=="),
            "s3pPLMBiTxaQ9kYGzzhZRbK+xOo="
        );
    }

    #[test]
    fn sha1_and_base64_vectors() {
        let hex: String = sha1(b"abc").iter().map(|b| format!("{b:02x}")).collect();
        assert_eq!(hex, "a9993e364706816aba3e25717850c26c9cd0d89d");
        let long = vec![b'a'; 1000];
        let hex: String = sha1(&long).iter().map(|b| format!("{b:02x}")).collect();
        assert_eq!(hex, "291e9a6c66994949b57ba5e650361e98fc36b1ba");
        assert_eq!(base64(b""), "");
        assert_eq!(base64(b"f"), "Zg==");
        assert_eq!(base64(b"fo"), "Zm8=");
        assert_eq!(base64(b"foo"), "Zm9v");
        assert_eq!(base64(b"foobar"), "Zm9vYmFy");
    }

    #[test]
    fn client_frame_is_masked_and_round_trips() {
        let mask = [1, 2, 3, 4];
        for len in [0usize, 5, 125, 126, 300, 70_000] {
            let payload: Vec<u8> = (0..len).map(|i| (i % 251) as u8).collect();
            let f = encode_client_frame(0x1, &payload, mask);
            assert_eq!(f[0], 0x81);
            assert!(f[1] & 0x80 != 0, "client frames must be masked");
            let (fin, op, got, used) = parse_server_frame(&f).unwrap().unwrap();
            assert!(fin);
            assert_eq!(op, 1);
            assert_eq!(used, f.len());
            assert_eq!(got, payload);
        }
    }

    #[test]
    fn server_frames_partial_and_oversized() {
        let f = [0x81u8, 0x03, b'a', b'b', b'c'];
        assert!(parse_server_frame(&f[..1]).unwrap().is_none());
        assert!(parse_server_frame(&f[..4]).unwrap().is_none());
        let (_, op, p, used) = parse_server_frame(&f).unwrap().unwrap();
        assert_eq!((op, p.as_slice(), used), (1, &b"abc"[..], 5));
        let mut huge = vec![0x81u8, 127];
        huge.extend_from_slice(&(u64::MAX).to_be_bytes());
        assert!(matches!(parse_server_frame(&huge), Err(Error::Protocol(_))));
    }

    #[test]
    fn handshake_maps_refusals() {
        let ok = format!(
            "HTTP/1.1 101 Switching Protocols\r\nUpgrade: websocket\r\nSec-WebSocket-Accept: {}",
            accept_key("k")
        );
        assert!(check_handshake(&ok, "k").is_ok());
        assert!(matches!(
            check_handshake(&ok, "other"),
            Err(Error::Protocol(_))
        ));
        assert!(matches!(
            check_handshake("HTTP/1.1 403 Forbidden", "k"),
            Err(Error::Unauthenticated { .. })
        ));
        assert!(matches!(
            check_handshake("HTTP/1.1 502 Bad Gateway", "k"),
            Err(Error::Server { .. })
        ));
        assert!(matches!(
            check_handshake("nonsense", "k"),
            Err(Error::Protocol(_))
        ));
        assert!(fatal(&Error::Unauthenticated { message: "x".into() }));
        assert!(!fatal(&Error::Disconnected));
    }

    #[test]
    fn parses_the_message_list_fixture() {
        let v: Value = serde_json::from_str(&fx("chat_messages.json")).unwrap();
        let list: Vec<ChatMessage> = list_of(&v, "messages").unwrap();
        assert_eq!(list.len(), 4);
        assert_eq!(list[0].author_type, "user");
        assert_eq!(list[0].created_at, 1_760_000_000.5);
        // A numeric string and a millisecond value are normalised by `Timestamp`
        // (strings parse; the web app's `toMs` heuristic is applied by the UI).
        assert_eq!(list[1].created_at, 1_760_000_060.0);
        assert!(list[2].content_blocks.is_some());
        assert_eq!(list[2].state.as_deref(), Some("streaming"));
        assert_eq!(list[3].deleted_at, Some(1_760_000_300.0));
    }

    #[test]
    fn parses_the_channel_list_fixture() {
        let v: Value = serde_json::from_str(&fx("chat_channels.json")).unwrap();
        let list: Vec<Channel> = list_of(&v, "channels").unwrap();
        assert_eq!(list.len(), 5);
        assert_eq!(list[0].kind, "topic");
        assert_eq!(list[1].members, vec!["user", "alpha"]);
        assert_eq!(list[1].last_preview.as_deref(), Some("on it"));
        assert_eq!(list[2].settings_kind(), Some("a2a"));
        // `members: null` and a missing `settings` are tolerated.
        assert!(list[4].members.is_empty());
        assert!(list[4].settings.is_null());
    }

    #[test]
    fn parses_every_frame_kind_from_the_fixture() {
        let frames: Vec<String> = fx("chat_frames.jsonl")
            .lines()
            .filter(|l| !l.trim().is_empty())
            .map(str::to_string)
            .collect();
        let kinds: Vec<&str> = frames
            .iter()
            .map(|f| match ChatEvent::parse(f).unwrap() {
                ChatEvent::Message(_) => "message",
                ChatEvent::MessageDelta { .. } => "delta",
                ChatEvent::MessageState { .. } => "state",
                ChatEvent::Typing { .. } => "typing",
                ChatEvent::Thinking { .. } => "thinking",
                ChatEvent::ReactionUpdate { .. } => "reaction",
                ChatEvent::MessageEdit { .. } => "edit",
                ChatEvent::MessageDelete { .. } => "delete",
                ChatEvent::Receipt { .. } => "receipt",
                ChatEvent::Error { .. } => "error",
                ChatEvent::Other => "other",
            })
            .collect();
        assert_eq!(
            kinds,
            [
                "message", "delta", "state", "typing", "typing", "thinking", "reaction", "edit",
                "delete", "receipt", "error", "other"
            ]
        );
    }

    #[test]
    fn malformed_frames_are_errors_not_panics() {
        for bad in [
            "",
            "not json",
            "[]",
            "{}",
            r#"{"type": 5}"#,
            r#"{"type":"message_delta"}"#,
            r#"{"type":"message","id": 7}"#,
        ] {
            assert!(ChatEvent::parse(bad).is_err(), "should reject {bad:?}");
        }
        // Unknown type and extra fields are fine.
        assert_eq!(
            ChatEvent::parse(r#"{"type":"from_the_future","x":1}"#).unwrap(),
            ChatEvent::Other
        );
    }

    #[test]
    fn frame_round_trips_through_the_stream_shape() {
        let ev = ChatEvent::parse(r#"{"type":"message_delta","message_id":"m1","delta":"hi","seq":9}"#)
            .unwrap();
        let s = serde_json::to_string(&ev).unwrap();
        let v: Value = serde_json::from_str(&s).unwrap();
        assert_eq!(v["type"], "message_delta");
        assert_eq!(v["delta"], "hi");
    }

    #[test]
    fn list_of_rejects_a_bad_element_and_tolerates_absent() {
        let v = json!({"messages": [{"id": "a"}, {"id": 5}]});
        assert!(list_of::<ChatMessage>(&v, "messages").is_err());
        assert!(list_of::<ChatMessage>(&json!({}), "messages")
            .unwrap()
            .is_empty());
        assert!(list_of::<ChatMessage>(&json!({"messages": "x"}), "messages").is_err());
    }

    /// A tiny RFC 6455 server on loopback: accepts one connection per call,
    /// answers the handshake, and lets the test script the exchange.
    async fn fake_server() -> (std::net::SocketAddr, tokio::net::TcpListener) {
        let l = tokio::net::TcpListener::bind("127.0.0.1:0").await.unwrap();
        (l.local_addr().unwrap(), l)
    }

    async fn accept_ws(l: &tokio::net::TcpListener) -> (TcpStream, String) {
        let (mut s, _) = l.accept().await.unwrap();
        let mut buf = Vec::new();
        let mut tmp = [0u8; 1024];
        while find(&buf, b"\r\n\r\n").is_none() {
            let n = s.read(&mut tmp).await.unwrap();
            buf.extend_from_slice(&tmp[..n]);
        }
        let head = String::from_utf8_lossy(&buf).to_string();
        let key = head
            .lines()
            .find_map(|l| l.strip_prefix("Sec-WebSocket-Key: "))
            .unwrap()
            .trim()
            .to_string();
        let resp = format!(
            "HTTP/1.1 101 Switching Protocols\r\nUpgrade: websocket\r\nConnection: Upgrade\r\nSec-WebSocket-Accept: {}\r\n\r\n",
            accept_key(&key)
        );
        s.write_all(resp.as_bytes()).await.unwrap();
        (s, head)
    }

    fn server_text(s: &str) -> Vec<u8> {
        let mut f = vec![0x81u8];
        if s.len() < 126 {
            f.push(s.len() as u8);
        } else {
            f.push(126);
            f.extend_from_slice(&(s.len() as u16).to_be_bytes());
        }
        f.extend_from_slice(s.as_bytes());
        f
    }

    async fn read_client_text(s: &mut TcpStream, pending: &mut Vec<u8>) -> String {
        let mut tmp = [0u8; 1024];
        loop {
            if let Some((_, op, payload, used)) = parse_server_frame(pending).unwrap() {
                pending.drain(..used);
                if op == 1 {
                    return String::from_utf8(payload).unwrap();
                }
                continue;
            }
            let n = s.read(&mut tmp).await.unwrap();
            assert!(n > 0, "client closed");
            pending.extend_from_slice(&tmp[..n]);
        }
    }

    async fn signed_in_client(base: &str) -> Client {
        use wiremock::matchers::{method, path};
        use wiremock::{Mock, MockServer, ResponseTemplate};
        // Sign in against a wiremock controller, then move the session to the
        // fake socket server by exporting and re-importing it with its base URL.
        let server = MockServer::start().await;
        Mock::given(method("POST"))
            .and(path("/auth/login"))
            .respond_with(
                ResponseTemplate::new(200)
                    .set_body_raw(r#"{"user":{"id":"u1","username":"alex"}}"#, "application/json")
                    .append_header("set-cookie", "taos_session=SESSTOK; Path=/")
                    .append_header("set-cookie", "csrf_token=csrfx; Path=/"),
            )
            .mount(&server)
            .await;
        let c = Client::with_base_url(server.uri()).unwrap();
        c.login(Some("alex"), "pw", None).await.unwrap();
        let snap = serde_json::to_value(c.export_session().unwrap()).unwrap();
        let mut snap = snap;
        snap["base_url"] = Value::String(base.to_string());
        Client::with_session(serde_json::from_value(snap).unwrap()).unwrap()
    }

    #[tokio::test]
    async fn socket_joins_delivers_frames_and_rejoins_after_a_reconnect() {
        use futures_util::StreamExt;
        let (addr, l) = fake_server().await;
        let client = signed_in_client(&format!("http://{addr}")).await;
        client.chat().set_joined(["c1".to_string()]);
        let opts = SseOptions {
            backoff: Backoff::new(Duration::from_millis(10), Duration::from_millis(40)),
            idle_timeout: Some(Duration::from_secs(30)),
            ..SseOptions::default()
        };
        let mut stream = client.chat_stream(opts);

        let (mut s, head) = accept_ws(&l).await;
        assert!(head.contains("Cookie: taos_session=SESSTOK"));
        assert!(head.contains(&format!("User-Agent: {}", crate::USER_AGENT)));
        let first = stream.next().await.unwrap().unwrap();
        assert_eq!(first.event.as_deref(), Some(CONNECTED_EVENT));
        let mut pending = Vec::new();
        assert_eq!(
            read_client_text(&mut s, &mut pending).await,
            r#"{"channel_id":"c1","type":"join"}"#
        );
        // A frame, a garbage frame (dropped), then a second frame.
        s.write_all(&server_text(r#"{"type":"message_delta","message_id":"m","delta":"a"}"#))
            .await
            .unwrap();
        s.write_all(&server_text("{{{")).await.unwrap();
        s.write_all(&server_text(r#"{"type":"message_state","message_id":"m","state":"complete"}"#))
            .await
            .unwrap();
        let a = stream.next().await.unwrap().unwrap();
        let b = stream.next().await.unwrap().unwrap();
        assert_eq!(a.event.as_deref(), Some(CHAT_EVENT));
        assert!(a.data.contains("message_delta"));
        assert!(b.data.contains("message_state"), "the bad frame was dropped");
        // Switching channels leaves the old one and joins the new one.
        client.chat().set_joined(["c2".to_string()]);
        let mut got = vec![
            read_client_text(&mut s, &mut pending).await,
            read_client_text(&mut s, &mut pending).await,
        ];
        got.sort();
        assert_eq!(
            got,
            [
                r#"{"channel_id":"c1","type":"leave"}"#,
                r#"{"channel_id":"c2","type":"join"}"#
            ]
        );
        // The server drops the connection: the stream reports it, reconnects, re-joins c2.
        drop(s);
        let lost = stream.next().await.unwrap();
        assert!(matches!(lost, Err(Error::Disconnected)));
        let (mut s2, _) = accept_ws(&l).await;
        let again = stream.next().await.unwrap().unwrap();
        assert_eq!(again.event.as_deref(), Some(CONNECTED_EVENT));
        let mut pending = Vec::new();
        assert_eq!(
            read_client_text(&mut s2, &mut pending).await,
            r#"{"channel_id":"c2","type":"join"}"#
        );
    }

    #[tokio::test]
    async fn a_refused_handshake_ends_the_stream_as_unauthenticated() {
        use futures_util::StreamExt;
        let (addr, l) = fake_server().await;
        let client = signed_in_client(&format!("http://{addr}")).await;
        let mut stream = client.chat_stream(SseOptions::default());
        let (mut s, _) = l.accept().await.unwrap();
        let mut tmp = [0u8; 1024];
        let _ = s.read(&mut tmp).await.unwrap();
        s.write_all(b"HTTP/1.1 403 Forbidden\r\ncontent-length: 0\r\n\r\n")
            .await
            .unwrap();
        let item = stream.next().await.unwrap();
        assert!(matches!(item, Err(Error::Unauthenticated { .. })));
        assert!(stream.next().await.is_none(), "no retry loop on a refusal");
    }

    #[tokio::test]
    async fn https_controllers_are_unsupported_not_a_retry_loop() {
        use futures_util::StreamExt;
        let client = Client::with_base_url("https://controller.invalid").unwrap();
        let mut stream = client.chat_stream(SseOptions::default());
        // Not signed in: the first fatal error is about credentials or TLS.
        let item = stream.next().await.unwrap();
        assert!(matches!(item, Err(Error::Unsupported(_))));
    }
}
