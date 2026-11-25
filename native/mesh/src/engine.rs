// SPDX-License-Identifier: AGPL-3.0-or-later
//! The engine: records every frame durably, attributes it through the
//! identity registry, enforces grants on sends, and serves queries.
//!
//! Zero loss rules:
//! * a frame is appended and fsynced before the engine reports it stored;
//! * if the append fails the record stays in memory in `pending`, the error
//!   is surfaced (`health`, and the `Err` of `ingest`), and every later
//!   ingest retries the pending records first, in order;
//! * nothing is ever dropped, trimmed or rewritten.

use crate::grants::{Denied, Grant, Grants, OWNER};
use crate::identity::{visible_to, Contact, IdentityStore};
use crate::link::{Link, LinkKind};
use crate::meshcore::{self, MeshcoreDriver};
use crate::meshtastic::{self, MeshtasticDriver};
use crate::model::*;
use crate::store::{Store, StoreError};
use serde::{Deserialize, Serialize};
use std::collections::{BTreeMap, HashMap, VecDeque};
use std::path::{Path, PathBuf};
use std::sync::mpsc::{channel, Receiver, Sender};
use std::sync::{Arc, Mutex, MutexGuard};

#[derive(Debug)]
pub enum EngineError {
    Store(StoreError),
    Denied(Denied),
    Invalid(String),
    NoRadio(String),
    Forbidden(String),
}

impl std::fmt::Display for EngineError {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        match self {
            EngineError::Store(e) => write!(f, "{e}"),
            EngineError::Denied(d) => write!(f, "{d}"),
            EngineError::Invalid(s) | EngineError::NoRadio(s) | EngineError::Forbidden(s) => {
                write!(f, "{s}")
            }
        }
    }
}
impl std::error::Error for EngineError {}
impl From<StoreError> for EngineError {
    fn from(e: StoreError) -> Self {
        EngineError::Store(e)
    }
}

impl EngineError {
    pub fn code(&self) -> &'static str {
        match self {
            EngineError::Store(_) => "store_error",
            EngineError::Denied(_) => "no_grant",
            EngineError::Invalid(_) => "invalid",
            EngineError::NoRadio(_) => "no_radio",
            EngineError::Forbidden(_) => "forbidden",
        }
    }
}

#[derive(Debug, Clone, Default, Serialize, Deserialize)]
pub struct Filter {
    /// Sender or recipient is this identity (`agent:x`, `person:x`, `contact:x`).
    pub identity: Option<String>,
    pub node: Option<String>,
    pub network: Option<String>,
    pub channel: Option<u32>,
    pub kind: Option<String>,
    pub dir: Option<String>,
    pub since_ms: Option<u64>,
    pub until_ms: Option<u64>,
    pub limit: Option<usize>,
    /// Newest first when true.
    pub newest_first: Option<bool>,
}

pub fn kind_name(k: LinkKind) -> &'static str {
    match k {
        LinkKind::Serial => "usb",
        LinkKind::Tcp => "tcp",
        LinkKind::Ble => "ble",
    }
}

#[derive(Debug, Clone, Serialize)]
pub struct RadioStatus {
    pub id: String,
    pub network: String,
    pub link: String,
    pub link_kind: String,
    /// True once the radio has given its configuration (Meshtastic) or
    /// identified itself (MeshCore).
    pub ready: bool,
    pub connected: bool,
    /// `connected` (ready), `connecting`, `reconnecting` (link dropped, retrying with back-off) or `offline`.
    pub state: String,
    /// Consecutive failed (re)connects; 0 when connected.
    pub attempt: u32,
    /// When the next reconnect is tried, ms since 1970, while `reconnecting`.
    pub next_retry_ms: Option<u64>,
    pub identity: Option<Attribution>,
    pub frames_in: u64,
    pub frames_out: u64,
    pub console_bytes: u64,
    pub decode_errors: u64,
    pub last_error: Option<String>,
    pub queued: usize,
    /// Read from the radio itself; absent until it has said.
    pub firmware: Option<String>,
    pub hardware: Option<String>,
}

/// What a radio has said about its own settings.
#[derive(Debug, Clone, PartialEq)]
pub enum DeviceCfg {
    Meshtastic(crate::mt_device::DeviceState),
    Meshcore(crate::meshcore::McDevice),
}

impl DeviceCfg {
    fn firmware(&self) -> Option<String> {
        match self {
            DeviceCfg::Meshtastic(d) => d.firmware(),
            DeviceCfg::Meshcore(d) => d.firmware(),
        }
    }
    fn hardware(&self) -> Option<String> {
        match self {
            DeviceCfg::Meshtastic(d) => d.hardware(),
            DeviceCfg::Meshcore(d) => d.model.clone(),
        }
    }
    fn snapshot(&self) -> serde_json::Value {
        match self {
            DeviceCfg::Meshtastic(d) => {
                let mut v = d.snapshot();
                v["writable"] = serde_json::json!(crate::mt_device::DeviceState::WRITABLE);
                v
            }
            DeviceCfg::Meshcore(d) => d.snapshot(),
        }
    }
}

struct RadioState {
    id: String,
    network: String,
    link: String,
    connected: bool,
    frames_in: u64,
    frames_out: u64,
    console_bytes: u64,
    decode_errors: u64,
    last_error: Option<String>,
    outbox: VecDeque<(String, Vec<u8>)>,
    link_kind: String,
    ready: bool,
    reconnect: Option<(u32, u64)>,
    cfg: Option<DeviceCfg>,
    /// Framed admin writes (settings) waiting for the session to write them.
    admin: VecDeque<Vec<u8>>,
}

struct Inner {
    store: Store,
    ids: IdentityStore,
    grants: Grants,
    positions: HashMap<(String, String), Position>,
    seq: u64,
    radios: BTreeMap<String, RadioState>,
    subscribers: Vec<Sender<Record>>,
    pending: VecDeque<Record>,
    last_store_error: Option<String>,
    stored: u64,
    owner: String,
}

pub struct Engine {
    pub dir: PathBuf,
    pub owner: String,
    inner: Mutex<Inner>,
}

impl Inner {
    fn flush_pending(&mut self) -> Result<(), StoreError> {
        while let Some(rec) = self.pending.front() {
            match self.store.append(rec) {
                Ok(()) => {
                    let rec = self.pending.pop_front().unwrap();
                    self.stored += 1;
                    self.last_store_error = None;
                    self.subscribers.retain(|s| s.send(rec.clone()).is_ok());
                }
                Err(e) => {
                    self.last_store_error = Some(e.to_string());
                    return Err(e);
                }
            }
        }
        Ok(())
    }

    fn commit(&mut self, mut rec: Record) -> Result<Record, StoreError> {
        rec.owner = self.owner.clone();
        let copy = rec.clone();
        self.pending.push_back(rec);
        self.flush_pending()?;
        Ok(copy)
    }

    /// Commit for a send: if the write fails, take the record back out of
    /// `pending` so a send that was reported as failed is not stored later as
    /// if it had been queued.
    fn commit_strict(&mut self, mut rec: Record) -> Result<Record, StoreError> {
        rec.owner = self.owner.clone();
        let copy = rec.clone();
        self.pending.push_back(rec);
        match self.flush_pending() {
            Ok(()) => Ok(copy),
            Err(e) => {
                if self.pending.back().is_some_and(|r| r.id == copy.id) {
                    self.pending.pop_back();
                }
                Err(e)
            }
        }
    }

    #[allow(clippy::too_many_arguments)]
    fn next_id(
        &mut self,
        dir: &str,
        network: &str,
        radio: &str,
        host_ts: u64,
        raw_hex: &str,
        ref_id: Option<&str>,
        state: Option<&str>,
    ) -> String {
        self.seq += 1;
        Record::compute_id(
            dir, network, radio, host_ts, self.seq, raw_hex, ref_id, state,
        )
    }
}

fn base_record(dir: &str, network: &str, radio: &str, kind: &str) -> Record {
    Record {
        v: RECORD_VERSION,
        id: String::new(),
        dir: dir.into(),
        network: network.into(),
        radio: radio.into(),
        observer: None,
        kind: kind.into(),
        channel: None,
        host_ts_ms: 0,
        radio_ts: None,
        from: None,
        to: None,
        broadcast: false,
        packet_id: None,
        text: None,
        position: None,
        signal: Signal::default(),
        detail: None,
        raw_hex: String::new(),
        sender: None,
        recipient: None,
        principal: None,
        ref_id: None,
        state: None,
        note: None,
        trust: None,
        delivery: None,
        owner: String::new(),
    }
}

impl Engine {
    pub fn open(dir: &Path, owner: &str) -> Result<Arc<Engine>, StoreError> {
        Self::open_with_store(dir, owner, Store::open(dir)?)
    }

    /// Open with an already opened record store (tests use this to put the
    /// store on a device that fails writes).
    pub fn open_with_store(
        dir: &Path,
        owner: &str,
        store: Store,
    ) -> Result<Arc<Engine>, StoreError> {
        std::fs::create_dir_all(dir)?;
        let ids = IdentityStore::open(dir)?;
        let mut grants = Grants::open(dir)?;
        let mut positions = HashMap::new();
        let mut seq = 0u64;
        let now = now_ms();
        for rec in Store::read_all(dir)? {
            seq += 1;
            if rec.dir == "out" && rec.kind == "text" {
                if let Some(p) = &rec.principal {
                    if now.saturating_sub(rec.host_ts_ms) < 3_600_000 {
                        grants.note_sent(
                            p,
                            &rec.network,
                            rec.channel,
                            rec.host_ts_ms,
                            rec.text.as_ref().map(|t| t.len() as u32).unwrap_or(0),
                        );
                    }
                }
            }
            if rec.dir == "in" {
                if let (Some(p), Some(f)) = (&rec.position, &rec.from) {
                    if p.has_fix() && p.source.as_deref() != Some("last_known") {
                        positions.insert((rec.network.clone(), f.clone()), p.clone());
                    }
                }
            }
        }
        Ok(Arc::new(Engine {
            dir: dir.to_path_buf(),
            owner: owner.into(),
            inner: Mutex::new(Inner {
                store,
                ids,
                grants,
                positions,
                seq,
                radios: BTreeMap::new(),
                subscribers: vec![],
                pending: VecDeque::new(),
                last_store_error: None,
                stored: 0,
                owner: owner.into(),
            }),
        }))
    }

    fn lock(&self) -> MutexGuard<'_, Inner> {
        // A panic while holding the lock must not turn into silent loss:
        // recover the guard; the store is append-only so state is consistent.
        self.inner.lock().unwrap_or_else(|p| p.into_inner())
    }

    // ---- ingest ------------------------------------------------------

    /// Store one inbound frame. On `Err` the record is held in memory and
    /// retried on the next call; the caller must surface the error.
    pub fn ingest(&self, network: &str, radio: &str, inb: Inbound) -> Result<Record, StoreError> {
        let mut g = self.lock();
        let host_ts = now_ms();
        let mut rec = base_record("in", network, radio, inb.event.kind());
        rec.host_ts_ms = host_ts;
        rec.radio_ts = inb.radio_ts;
        rec.channel = inb.channel;
        rec.from = inb.from.clone();
        rec.to = inb.to.clone();
        rec.broadcast = inb.broadcast;
        rec.packet_id = inb.packet_id;
        rec.signal = inb.signal.clone();
        rec.raw_hex = to_hex(&inb.raw);
        rec.trust = inb.trust.clone();
        match &inb.event {
            Event::Text { text } => rec.text = Some(text.clone()),
            Event::Position(p) => rec.position = Some(p.clone()),
            Event::NodeInfo {
                long_name,
                short_name,
                hw_model,
                position,
            } => {
                rec.detail = Some(
                    serde_json::json!({"long_name": long_name, "short_name": short_name, "hw_model": hw_model}),
                );
                rec.position = position.clone();
            }
            Event::Telemetry { kind, values } => {
                rec.detail = Some(serde_json::json!({"telemetry": kind, "values": values}))
            }
            Event::Ack { request_id, error } => {
                rec.detail = Some(serde_json::json!({"request_id": request_id, "error": error}))
            }
            Event::Control { what } => rec.detail = Some(serde_json::json!({"control": what})),
            Event::Opaque { port, note } => {
                rec.detail = Some(serde_json::json!({"port": port, "note": note}))
            }
            Event::Sent { expected_ack } => {
                rec.detail = Some(serde_json::json!({"expected_ack": expected_ack}))
            }
        }
        // position cache: a position in the frame updates it; a message
        // without one carries the sender's last known position, labelled.
        if let (Some(p), Some(f)) = (&rec.position, &rec.from) {
            if p.has_fix() {
                g.positions.insert((network.into(), f.clone()), p.clone());
            }
        }
        if rec.position.is_none() && matches!(inb.event, Event::Text { .. }) {
            if let Some(f) = &rec.from {
                if let Some(p) = g.positions.get(&(network.to_string(), f.clone())) {
                    let mut p = p.clone();
                    p.source = Some("last_known".into());
                    rec.position = Some(p);
                }
            }
        }
        rec.sender = rec.from.as_ref().and_then(|f| g.ids.reg.attribute(f));
        rec.recipient = if rec.broadcast {
            None
        } else {
            rec.to.as_ref().and_then(|t| g.ids.reg.attribute(t))
        };
        rec.observer = g.ids.reg.attribute(radio).map(|a| a.identity);
        rec.id = g.next_id("in", network, radio, host_ts, &rec.raw_hex, None, None);
        g.commit(rec)
    }

    // ---- radios ------------------------------------------------------

    pub fn register_radio(&self, id: &str, network: &str, link: &str, link_kind: &str) {
        let mut g = self.lock();
        let e = g
            .radios
            .entry(id.to_string())
            .or_insert_with(|| RadioState {
                id: id.into(),
                network: network.into(),
                link: link.into(),
                connected: true,
                frames_in: 0,
                frames_out: 0,
                console_bytes: 0,
                decode_errors: 0,
                last_error: None,
                outbox: VecDeque::new(),
                link_kind: link_kind.into(),
                ready: false,
                reconnect: None,
                cfg: None,
                admin: VecDeque::new(),
            });
        e.connected = true;
        e.ready = false;
        e.reconnect = None;
        e.link = link.into();
        e.last_error = None;
    }

    /// The supervisor's view of a dropped link: retrying, attempt number,
    /// when, and why the last attempt failed.
    pub fn radio_reconnecting(&self, id: &str, attempt: u32, next_ms: u64, why: &str) {
        self.radio_note(id, |r| {
            r.connected = false;
            r.ready = false;
            r.reconnect = Some((attempt, next_ms));
            r.last_error = Some(why.to_string());
        });
    }

    /// The supervisor gave up or was stopped.
    pub fn radio_offline(&self, id: &str, why: &str) {
        self.radio_note(id, |r| {
            r.connected = false;
            r.ready = false;
            r.reconnect = None;
            r.last_error = Some(why.to_string());
        });
    }

    fn radio_note(&self, id: &str, f: impl FnOnce(&mut RadioState)) {
        let mut g = self.lock();
        if let Some(r) = g.radios.get_mut(id) {
            f(r);
        }
    }

    pub fn radios(&self) -> Vec<RadioStatus> {
        let g = self.lock();
        g.radios
            .values()
            .map(|r| RadioStatus {
                id: r.id.clone(),
                network: r.network.clone(),
                link: r.link.clone(),
                link_kind: r.link_kind.clone(),
                ready: r.ready,
                connected: r.connected,
                identity: g.ids.reg.attribute(&r.id),
                frames_in: r.frames_in,
                frames_out: r.frames_out,
                console_bytes: r.console_bytes,
                decode_errors: r.decode_errors,
                last_error: r.last_error.clone(),
                state: if r.connected {
                    if r.ready {
                        "connected"
                    } else {
                        "connecting"
                    }
                } else if r.reconnect.is_some() {
                    "reconnecting"
                } else {
                    "offline"
                }
                .into(),
                attempt: r.reconnect.map(|x| x.0).unwrap_or(0),
                next_retry_ms: r.reconnect.map(|x| x.1),
                queued: r.outbox.len(),
                firmware: r.cfg.as_ref().and_then(|c| c.firmware()),
                hardware: r.cfg.as_ref().and_then(|c| c.hardware()),
            })
            .collect()
    }

    pub fn health(&self) -> serde_json::Value {
        let g = self.lock();
        serde_json::json!({
            "ok": g.last_store_error.is_none(),
            "store_error": g.last_store_error,
            "pending_unwritten": g.pending.len(),
            "stored_this_run": g.stored,
            "torn_tail_preserved": g.store.torn_preserved.as_ref().and_then(|p| p.file_name()).map(|n| n.to_string_lossy().into_owned()),
        })
    }

    // ---- send --------------------------------------------------------

    /// Queue a text for a radio. The outbound record is written (state
    /// "queued") before anything is handed to the radio link.
    pub fn send_text(
        &self,
        principal: &str,
        network: &str,
        radio: Option<&str>,
        to: Option<&str>,
        channel: Option<u32>,
        text: &str,
    ) -> Result<Record, EngineError> {
        if text.is_empty() {
            return Err(EngineError::Invalid("empty text".into()));
        }
        let limit = if network == NETWORK_MESHTASTIC {
            200
        } else {
            160
        };
        if text.len() > limit {
            return Err(EngineError::Invalid(format!(
                "text is {} bytes; the conservative limit for {network} is {limit}",
                text.len()
            )));
        }
        let mut g = self.lock();
        let now = now_ms();
        let grant_channel = if to.is_some() {
            None
        } else {
            channel.or(Some(0))
        };
        if let Err(d) =
            g.grants
                .authorize_send(principal, network, grant_channel, text.len() as u32, now)
        {
            // audit the refusal; it is a record too
            let mut r = base_record("state", network, radio.unwrap_or(""), "send_refused");
            r.host_ts_ms = now;
            r.principal = Some(principal.into());
            r.state = Some("refused".into());
            r.note = Some(d.to_string());
            r.text = Some(text.into());
            r.channel = channel;
            r.to = to.map(String::from);
            r.id = g.next_id(
                "state",
                network,
                radio.unwrap_or(""),
                now,
                "",
                None,
                Some("refused"),
            );
            g.commit(r)?;
            return Err(EngineError::Denied(d));
        }
        let rid = match radio {
            Some(r) => r.to_string(),
            None => {
                let c: Vec<&RadioState> = g
                    .radios
                    .values()
                    .filter(|r| r.network == network && r.connected)
                    .collect();
                match c.len() {
                    1 => c[0].id.clone(),
                    0 => {
                        return Err(EngineError::NoRadio(format!(
                            "no connected {network} radio"
                        )))
                    }
                    _ => {
                        return Err(EngineError::Invalid(
                            "more than one radio is connected: name one with `radio`".into(),
                        ))
                    }
                }
            }
        };
        match g.radios.get(&rid) {
            Some(r) if r.connected && r.network == network => {}
            Some(_) => {
                return Err(EngineError::NoRadio(format!(
                    "radio {rid} is not connected on {network}"
                )))
            }
            None => return Err(EngineError::NoRadio(format!("no radio {rid}"))),
        }
        // encode
        let mut packet_id: Option<u32> = None;
        let (bytes, raw, broadcast, to_name, chan) = if network == NETWORK_MESHTASTIC {
            let dest = match to {
                Some(t) => meshtastic::parse_node(t).ok_or_else(|| {
                    EngineError::Invalid(format!("bad node id {t:?}; expected !xxxxxxxx"))
                })?,
                None => meshtastic::BROADCAST,
            };
            let ch = channel.unwrap_or(0);
            let id = meshtastic::fresh_packet_id();
            packet_id = Some(id);
            let payload =
                meshtastic::encode_text(dest, ch, text, id, dest != meshtastic::BROADCAST);
            (
                meshtastic::frame(&payload),
                payload,
                dest == meshtastic::BROADCAST,
                meshtastic::node_name(dest),
                Some(ch),
            )
        } else if network == NETWORK_MESHCORE {
            let ts = (now / 1000) as u32;
            match to {
                Some(t) => {
                    let p = meshcore::parse_prefix(t).ok_or_else(|| {
                        EngineError::Invalid(format!(
                            "bad MeshCore node id {t:?}; expected 12+ hex chars"
                        ))
                    })?;
                    let payload = meshcore::send_direct(&p, ts, text);
                    (
                        meshcore::frame_to_radio(&payload),
                        payload,
                        false,
                        t.to_string(),
                        None,
                    )
                }
                None => {
                    let ch = channel.unwrap_or(0);
                    if ch > 255 {
                        return Err(EngineError::Invalid("channel index out of range".into()));
                    }
                    let payload = meshcore::send_channel(ch as u8, ts, text);
                    (
                        meshcore::frame_to_radio(&payload),
                        payload,
                        true,
                        "channel".into(),
                        Some(ch),
                    )
                }
            }
        } else {
            return Err(EngineError::Invalid(format!("unknown network {network}")));
        };
        let mut rec = base_record("out", network, &rid, "text");
        rec.host_ts_ms = now;
        rec.channel = chan;
        rec.from = Some(rid.clone());
        rec.to = Some(to_name);
        rec.broadcast = broadcast;
        rec.text = Some(text.into());
        rec.raw_hex = to_hex(&raw);
        rec.state = Some("queued".into());
        rec.packet_id = packet_id;
        rec.principal = Some(principal.into());
        rec.sender = g.ids.reg.attribute(&rid);
        rec.recipient = if broadcast {
            None
        } else {
            rec.to.as_ref().and_then(|t| g.ids.reg.attribute(t))
        };
        rec.observer = rec.sender.as_ref().map(|s| s.identity.clone());
        rec.position = g
            .positions
            .get(&(network.to_string(), rid.clone()))
            .cloned()
            .map(|mut p| {
                p.source = Some("last_known".into());
                p
            });
        rec.id = g.next_id(
            "out",
            network,
            &rid,
            now,
            &rec.raw_hex,
            None,
            Some("queued"),
        );
        let stored = g.commit_strict(rec)?;
        g.grants
            .charge(principal, network, stored.channel, text.len() as u32, now);
        if let Some(r) = g.radios.get_mut(&rid) {
            r.outbox.push_back((stored.id.clone(), bytes));
        }
        Ok(stored)
    }

    fn mark_state(
        &self,
        ref_id: &str,
        network: &str,
        radio: &str,
        state: &str,
        note: Option<String>,
    ) -> Result<(), StoreError> {
        let mut g = self.lock();
        let now = now_ms();
        let mut r = base_record("state", network, radio, "send_state");
        r.host_ts_ms = now;
        r.ref_id = Some(ref_id.into());
        r.state = Some(state.into());
        r.note = note;
        r.id = g.next_id("state", network, radio, now, "", Some(ref_id), Some(state));
        g.commit(r).map(|_| ())
    }

    // ---- queries -----------------------------------------------------

    fn enrich(g: &Inner, mut r: Record) -> Record {
        if r.sender.is_none() {
            r.sender = r.from.as_ref().and_then(|f| g.ids.reg.attribute(f));
        }
        if r.recipient.is_none() && !r.broadcast {
            r.recipient = r.to.as_ref().and_then(|t| g.ids.reg.attribute(t));
        }
        if r.observer.is_none() {
            r.observer = g.ids.reg.attribute(&r.radio).map(|a| a.identity);
        }
        r
    }

    /// Records visible to `viewer` (`owner`, `person:<id>` or `agent:<id>`).
    /// State records (send progress) are returned only when the filter asks
    /// for `dir = "state"` and the viewer is the owner. Outbound records
    /// carry the folded `delivery`.
    pub fn messages(&self, viewer: &str, f: &Filter) -> Result<Vec<Record>, EngineError> {
        let g = self.lock();
        let all = Store::read_all(&self.dir)?;
        let now = now_ms();
        let viewer_id: String = if viewer == OWNER {
            self.owner.clone()
        } else {
            viewer.to_string()
        };
        let is_agent = viewer.starts_with("agent:");
        let delivery = fold_delivery(&all);
        let want_state = f.dir.as_deref() == Some("state") && viewer == OWNER;
        let mut out = Vec::new();
        for rec in all {
            let mut rec = Self::enrich(&g, rec);
            if f.network.as_deref().is_some_and(|n| n != rec.network) {
                continue;
            }
            if rec.dir == "state" {
                if !want_state {
                    continue;
                }
            } else if is_agent {
                // Reading is per channel for broadcast traffic; a direct
                // message belongs to no channel, so it needs an all-channel
                // grant (same rule as sending).
                let grant_channel = if rec.broadcast { rec.channel } else { None };
                if !g.grants.may_read(viewer, &rec.network, grant_channel, now) {
                    continue;
                }
                if !visible_to(&g.ids.reg, &rec, &self.owner, &self.owner) {
                    continue;
                }
                // positions are their own grant: a channel grant never
                // includes where anyone is
                if !g.grants.may_read_positions(viewer, &rec.network, now) {
                    if rec.kind == "position" {
                        continue;
                    }
                    rec.position = None;
                }
            } else if !visible_to(&g.ids.reg, &rec, &viewer_id, &self.owner) {
                continue;
            }
            if !crate::identity::redact_hidden_locations(&g.ids.reg, &mut rec) {
                continue;
            }
            if rec.dir == "out" {
                rec.delivery = delivery.get(&rec.id).cloned();
            }
            if f.channel.is_some() && rec.channel != f.channel {
                continue;
            }
            if f.kind.as_deref().is_some_and(|k| k != rec.kind) {
                continue;
            }
            if f.dir.as_deref().is_some_and(|d| d != rec.dir) {
                continue;
            }
            if f.since_ms.is_some_and(|t| rec.host_ts_ms < t)
                || f.until_ms.is_some_and(|t| rec.host_ts_ms > t)
            {
                continue;
            }
            if let Some(n) = &f.node {
                if rec.from.as_deref() != Some(n) && rec.to.as_deref() != Some(n) {
                    continue;
                }
            }
            if let Some(i) = &f.identity {
                let nodes = g.ids.reg.nodes_of(i);
                let hit = rec.sender.as_ref().is_some_and(|s| &s.identity == i)
                    || rec.recipient.as_ref().is_some_and(|s| &s.identity == i)
                    || rec.from.as_ref().is_some_and(|n| nodes.contains(n))
                    || rec
                        .to
                        .as_ref()
                        .is_some_and(|n| nodes.contains(n) && !rec.broadcast);
                if !hit {
                    continue;
                }
            }
            out.push(rec);
        }
        if f.newest_first.unwrap_or(false) {
            out.reverse();
        }
        if let Some(l) = f.limit {
            out.truncate(l);
        }
        Ok(out)
    }

    /// The last known position of every identity (or unattributed node) the
    /// viewer may see: "where was everyone, and when".
    pub fn last_positions(&self, viewer: &str) -> Result<Vec<serde_json::Value>, EngineError> {
        let recs = self.messages(
            viewer,
            &Filter {
                kind: None,
                ..Default::default()
            },
        )?;
        let g = self.lock();
        let mut best: BTreeMap<String, (u64, serde_json::Value)> = BTreeMap::new();
        for r in recs {
            let Some(p) = &r.position else { continue };
            if !p.has_fix() || r.dir != "in" || p.source.as_deref() == Some("last_known") {
                continue;
            }
            let Some(node) = &r.from else { continue };
            let key = r
                .sender
                .as_ref()
                .map(|s| s.identity.clone())
                .unwrap_or_else(|| format!("node:{node}"));
            let when = p.time.map(|t| t * 1000).unwrap_or(r.host_ts_ms);
            let v = serde_json::json!({
                "identity": r.sender.as_ref().map(|s| s.identity.clone()),
                "name": r.sender.as_ref().map(|s| s.name.clone()),
                "node": node, "network": r.network, "position": p,
                "heard_ms": r.host_ts_ms, "record": r.id,
            });
            if best.get(&key).is_none_or(|(w, _)| when >= *w) {
                best.insert(key, (when, v));
            }
        }
        let _ = &g;
        Ok(best.into_values().map(|x| x.1).collect())
    }

    /// The radio list names identities and links: only the owner, or an
    /// agent the owner has granted some read access, may see it.
    pub fn may_see_radios(&self, viewer: &str) -> bool {
        if viewer == OWNER {
            return true;
        }
        viewer.starts_with("agent:") && self.lock().grants.has_any_read(viewer, now_ms())
    }

    // ---- subscribe ---------------------------------------------------

    pub fn subscribe(&self) -> Receiver<Record> {
        let (tx, rx) = channel();
        self.lock().subscribers.push(tx);
        rx
    }

    // ---- identities and grants (owner operations) ------------------------

    pub fn with_ids<T>(
        &self,
        by: &str,
        f: impl FnOnce(&mut IdentityStore) -> Result<T, StoreError>,
    ) -> Result<T, EngineError> {
        if by != OWNER {
            return Err(EngineError::Forbidden(
                "only the owner changes identities".into(),
            ));
        }
        let mut g = self.lock();
        Ok(f(&mut g.ids)?)
    }

    /// Hide location history: the only caller is the linked account.
    pub fn hide_locations(&self, by_account: &str, contact: &str) -> Result<(), EngineError> {
        let mut g = self.lock();
        Ok(g.ids.hide_locations(contact, by_account)?)
    }

    pub fn identities(&self) -> serde_json::Value {
        let g = self.lock();
        serde_json::json!({
            "contacts": g.ids.reg.contacts.values().collect::<Vec<&Contact>>(),
            "assignments": g.ids.reg.assignments.values().collect::<Vec<_>>(),
        })
    }

    pub fn grant_add(&self, by: &str, grant: Grant) -> Result<Grant, EngineError> {
        if by != OWNER {
            return Err(EngineError::Forbidden(
                "only the owner grants access".into(),
            ));
        }
        Ok(self.lock().grants.add(Grant {
            created_by: by.into(),
            ..grant
        })?)
    }

    pub fn grant_revoke(&self, by: &str, id: &str) -> Result<(), EngineError> {
        if by != OWNER {
            return Err(EngineError::Forbidden(
                "only the owner revokes grants".into(),
            ));
        }
        Ok(self.lock().grants.revoke(id, by)?)
    }

    pub fn grants(&self) -> Vec<Grant> {
        self.lock().grants.list()
    }

    // ---- sessions ----------------------------------------------------

    /// Take the queued outbound payloads of a radio.
    pub fn set_device_cfg(&self, id: &str, cfg: DeviceCfg) {
        self.radio_note(id, |r| {
            if r.cfg.as_ref() != Some(&cfg) {
                r.cfg = Some(cfg);
            }
        });
    }

    /// `device.config`: what the radio itself said. `state` is `loading` until it has said anything.
    pub fn device_config(&self, radio: &str) -> Result<serde_json::Value, EngineError> {
        let g = self.lock();
        let r = g.radios.get(radio).ok_or_else(|| EngineError::NoRadio(format!("no radio {radio}")))?;
        Ok(match &r.cfg {
            Some(c) if matches!(c, DeviceCfg::Meshtastic(d) if !d.complete) => {
                serde_json::json!({"radio": radio, "state": "loading", "note": "the radio is still sending its settings", "values": c.snapshot()})
            }
            Some(c) => serde_json::json!({"radio": radio, "state": "ok", "note": "", "values": c.snapshot()}),
            None => serde_json::json!({"radio": radio, "state": "loading", "note": "the radio has not said anything yet", "values": {}}),
        })
    }

    /// `device.apply`: plan the writes, queue them for the session. Nothing is queued when any change is refused.
    pub fn device_apply(&self, radio: &str, changes: &serde_json::Map<String, serde_json::Value>, receive_only: bool) -> Result<serde_json::Value, EngineError> {
        if receive_only {
            return Err(EngineError::Forbidden("the engine is in receive-only mode: it writes nothing to a radio".into()));
        }
        let mut g = self.lock();
        g.seq += 1;
        let mut seed = (now_ms() as u32) ^ (g.seq as u32).wrapping_mul(0x9E37_79B1);
        let r = g.radios.get_mut(radio).ok_or_else(|| EngineError::NoRadio(format!("no radio {radio}")))?;
        if !r.connected || !r.ready {
            return Err(EngineError::NoRadio(format!("{radio} is not connected")));
        }
        let Some(cfg) = &r.cfg else {
            return Err(EngineError::Invalid("the radio has not said anything about its settings yet".into()));
        };
        let DeviceCfg::Meshtastic(d) = cfg else {
            return Err(EngineError::Invalid("changing the settings of a MeshCore radio is not supported yet".into()));
        };
        let (payloads, applied) = d
            .plan_apply(changes, || {
                seed = seed.wrapping_add(0x6D2B_79F5);
                seed | 1
            })
            .map_err(EngineError::Invalid)?;
        for p in payloads {
            r.admin.push_back(crate::meshtastic::frame(&p));
        }
        Ok(serde_json::json!({"radio": radio, "applied": applied, "note": "Sent to the radio. It may restart to apply some settings and will reconnect by itself."}))
    }

    fn take_admin(&self, id: &str) -> Vec<Vec<u8>> {
        let mut g = self.lock();
        g.radios.get_mut(id).map(|r| r.admin.drain(..).collect()).unwrap_or_default()
    }

    /// Drop a radio's entry (after it was released): only when it is not connected.
    pub fn forget_radio(&self, link: &str) -> usize {
        let mut g = self.lock();
        let ids: Vec<String> = g.radios.values().filter(|r| r.link == link && !r.connected).map(|r| r.id.clone()).collect();
        for i in &ids {
            g.radios.remove(i);
        }
        ids.len()
    }

    fn take_outbox(&self, id: &str) -> Vec<(String, Vec<u8>)> {
        let mut g = self.lock();
        g.radios
            .get_mut(id)
            .map(|r| r.outbox.drain(..).collect())
            .unwrap_or_default()
    }
}

/// Fold send progress into one delivery state per outbound record id:
/// queued, then sent or failed (state records), then acknowledged or failed
/// when the network says so.
///
/// * Meshtastic: a routing packet whose `request_id` equals our packet id;
///   no error means acknowledged, an error means failed.
/// * MeshCore: the radio answers a send with an expected ack code; the later
///   send-confirmed push carrying that code means acknowledged.
pub fn fold_delivery(all: &[Record]) -> HashMap<String, String> {
    let mut d: HashMap<String, String> = HashMap::new();
    let mut by_packet: HashMap<(String, u32), (String, Option<String>)> = HashMap::new();
    let mut by_ack: HashMap<(String, u32), String> = HashMap::new();
    let mut last_out: HashMap<String, String> = HashMap::new();
    for r in all {
        match r.dir.as_str() {
            "out" if r.kind == "text" => {
                d.insert(
                    r.id.clone(),
                    r.state.clone().unwrap_or_else(|| "queued".into()),
                );
                if let Some(p) = r.packet_id {
                    by_packet.insert(
                        (r.radio.clone(), p),
                        (r.id.clone(), if r.broadcast { None } else { r.to.clone() }),
                    );
                }
                last_out.insert(r.radio.clone(), r.id.clone());
            }
            "state" => {
                if let (Some(rf), Some(st)) = (&r.ref_id, &r.state) {
                    // never move backwards out of a final network answer
                    if d.get(rf).is_none_or(|cur| cur != "acknowledged") {
                        d.insert(rf.clone(), st.clone());
                    }
                }
            }
            "in" if r.kind == "ack" => {
                let det = r.detail.clone().unwrap_or_default();
                let req = det["request_id"].as_u64().unwrap_or(0) as u32;
                let key = (r.radio.clone(), req);
                if let Some((t, to)) = by_packet.get(&key) {
                    // Meshtastic sends an ack with no error both from the
                    // recipient and as an implicit ack from a neighbour that
                    // relayed it: only the first is "acknowledged".
                    let st = if !det["error"].is_null() {
                        "failed"
                    } else if to.is_some() && r.from == *to {
                        "acknowledged"
                    } else {
                        "heard"
                    };
                    if d.get(t).is_none_or(|cur| cur != "acknowledged") {
                        d.insert(t.clone(), st.to_string());
                    }
                } else if let Some(t) = by_ack.get(&key) {
                    d.insert(
                        t.clone(),
                        if det["error"].is_null() {
                            "acknowledged"
                        } else {
                            "failed"
                        }
                        .to_string(),
                    );
                }
            }
            "in" if r.kind == "send_info" => {
                if let (Some(det), Some(o)) = (&r.detail, last_out.get(&r.radio)) {
                    if let Some(a) = det["expected_ack"].as_u64() {
                        by_ack.insert((r.radio.clone(), a as u32), o.clone());
                    }
                }
            }
            _ => {}
        }
    }
    d
}

// ---- radio session ---------------------------------------------------------

enum DriverKind {
    Meshtastic(MeshtasticDriver),
    Meshcore(MeshcoreDriver),
}

/// One connected radio: a link plus a driver. `pump` does one read/ingest/
/// write cycle; the daemon calls it in a loop on a thread, tests call it
/// directly with a fake link.
pub struct Session {
    engine: Arc<Engine>,
    link: Box<dyn Link>,
    driver: DriverKind,
    network: String,
    id: String,
    started: bool,
    pub transmit_enabled: bool,
    prefeed: Vec<u8>,
}

impl Session {
    pub fn new(
        engine: Arc<Engine>,
        link: Box<dyn Link>,
        network: &str,
    ) -> Result<Self, EngineError> {
        Self::new_as(engine, link, network, None)
    }

    /// Start under a radio id already known (a reconnect), so frames read
    /// before the radio introduces itself are stored under the right id.
    pub fn new_as(
        engine: Arc<Engine>,
        link: Box<dyn Link>,
        network: &str,
        known_id: Option<&str>,
    ) -> Result<Self, EngineError> {
        let driver = match network {
            NETWORK_MESHTASTIC => DriverKind::Meshtastic(MeshtasticDriver::new()),
            NETWORK_MESHCORE => DriverKind::Meshcore(MeshcoreDriver::new("taos-mesh")),
            other => return Err(EngineError::Invalid(format!("unknown network {other}"))),
        };
        let id = known_id.map(String::from).unwrap_or_else(|| link.name());
        engine.register_radio(&id, network, &link.name(), kind_name(link.kind()));
        Ok(Session {
            engine,
            link,
            driver,
            network: network.into(),
            id,
            started: false,
            transmit_enabled: true,
            prefeed: vec![],
        })
    }

    /// Bytes the radio already sent (during a probe); they are decoded and
    /// stored before anything else is read.
    pub fn prefeed(&mut self, bytes: Vec<u8>) {
        self.prefeed = bytes;
    }

    pub fn radio_id(&self) -> &str {
        &self.id
    }

    fn write(&mut self, bytes: &[u8]) -> std::io::Result<()> {
        self.link.write_all(bytes)
    }

    /// Send the session-opening bytes. Reads the radio's configuration; does
    /// not transmit on the radio.
    pub fn start(&mut self) -> std::io::Result<()> {
        self.started = true;
        match &self.driver {
            DriverKind::Meshtastic(d) => {
                let hs = d.handshake(0x7a05_0001);
                self.write(&hs)
            }
            DriverKind::Meshcore(d) => {
                for c in d.handshake() {
                    let f = meshcore::frame_to_radio(&c);
                    self.write(&f)?;
                }
                Ok(())
            }
        }
    }

    fn adopt_id(&mut self, new: Option<String>) {
        if let Some(n) = new {
            if n != self.id {
                // The radio told us its own id: move the registration. Frames
                // already stored under the link name keep that name (their
                // raw bytes are intact); new frames use the node id.
                let old = std::mem::replace(&mut self.id, n.clone());
                self.engine.register_radio(
                    &n,
                    &self.network,
                    &self.link.name(),
                    kind_name(self.link.kind()),
                );
                let mut g = self.engine.lock();
                if let Some(o) = g.radios.remove(&old) {
                    if let Some(r) = g.radios.get_mut(&n) {
                        r.admin.extend(o.admin);
                        r.outbox.extend(o.outbox);
                        r.frames_in += o.frames_in;
                    }
                }
            }
        }
    }

    /// One cycle. Returns the number of frames stored. A store failure is
    /// returned (and also visible in `health`); the frames are kept pending.
    pub fn pump(&mut self) -> Result<usize, EngineError> {
        if !self.started {
            self.start().map_err(|e| self.link_failed(e))?;
        }
        let mut buf = [0u8; 1024];
        let pre = std::mem::take(&mut self.prefeed);
        // bytes already received during a probe are processed before any new read
        let chunk: Vec<u8> = if pre.is_empty() {
            let n = self.link.read(&mut buf).map_err(|e| self.link_failed(e))?;
            buf[..n].to_vec()
        } else {
            pre
        };
        let mut stored = 0;
        let mut cmds: Vec<Vec<u8>> = Vec::new();
        let mut first_err: Option<StoreError> = None;
        if !chunk.is_empty() {
            let frames: Vec<Inbound> = match &mut self.driver {
                DriverKind::Meshtastic(d) => {
                    let f = d.feed(&chunk);
                    let (rid, c, de, ready) = (
                        d.radio_id(),
                        d.console_bytes(),
                        d.decode_errors,
                        d.config_complete,
                    );
                    self.adopt_pre(rid, c, de, ready);
                    f
                }
                DriverKind::Meshcore(d) => {
                    let (f, c) = d.feed_stream(&chunk);
                    cmds = c;
                    let (rid, cb, de) = (d.radio_id(), d.console_bytes(), d.decode_errors);
                    let ready = rid.is_some();
                    self.adopt_pre(rid, cb, de, ready);
                    f
                }
            };
            for inb in frames {
                match self.engine.ingest(&self.network, &self.id, inb) {
                    Ok(_) => {
                        stored += 1;
                        let id = self.id.clone();
                        self.engine.radio_note(&id, |r| r.frames_in += 1);
                    }
                    Err(e) => {
                        first_err.get_or_insert(e);
                    }
                }
            }
        }
        // what the radio said about itself, for device.config
        if !chunk.is_empty() {
            let cfg = match &self.driver {
                DriverKind::Meshtastic(d) => DeviceCfg::Meshtastic(d.device.clone()),
                DriverKind::Meshcore(d) => DeviceCfg::Meshcore(d.device.clone()),
            };
            self.engine.set_device_cfg(&self.id, cfg);
        }
        // driver-requested commands (read-only: sync messages)
        for c in cmds {
            let f = meshcore::frame_to_radio(&c);
            self.write(&f).map_err(|e| self.link_failed(e))?;
        }
        // settings the owner asked for (device.apply refused them in receive-only mode, so none are queued then)
        if self.transmit_enabled {
            for f in self.engine.take_admin(&self.id) {
                self.write(&f).map_err(|e| self.link_failed(e))?;
            }
        }
        // Queued sends. Every queued record ends in "sent" or "failed": a
        // link error fails the item and all the rest of the batch.
        let mut out: VecDeque<(String, Vec<u8>)> = self.engine.take_outbox(&self.id).into();
        let mut link_err: Option<EngineError> = None;
        while let Some((rec_id, bytes)) = out.pop_front() {
            if let Some(e) = &link_err {
                let _ = self.engine.mark_state(
                    &rec_id,
                    &self.network,
                    &self.id,
                    "failed",
                    Some(e.to_string()),
                );
                continue;
            }
            if !self.transmit_enabled {
                if let Err(e) = self.engine.mark_state(
                    &rec_id,
                    &self.network,
                    &self.id,
                    "failed",
                    Some("receive-only mode: transmit disabled".into()),
                ) {
                    first_err.get_or_insert(e);
                }
                continue;
            }
            match self.write(&bytes) {
                Ok(()) => {
                    if let Err(e) = self.engine.mark_state(
                        &rec_id,
                        &self.network,
                        &self.id,
                        "sent",
                        Some("handed to the radio link".into()),
                    ) {
                        first_err.get_or_insert(e);
                    }
                    let id = self.id.clone();
                    self.engine.radio_note(&id, |r| r.frames_out += 1);
                }
                Err(e) => {
                    let le = self.link_failed(e);
                    let _ = self.engine.mark_state(
                        &rec_id,
                        &self.network,
                        &self.id,
                        "failed",
                        Some(le.to_string()),
                    );
                    link_err = Some(le);
                }
            }
        }
        if let Some(e) = link_err {
            return Err(e);
        }
        match first_err {
            Some(e) => Err(e.into()),
            None => Ok(stored),
        }
    }

    fn adopt_pre(&mut self, rid: Option<String>, console: u64, decode_errors: u64, ready: bool) {
        self.adopt_id(rid);
        let id = self.id.clone();
        self.engine.radio_note(&id, |r| {
            r.console_bytes = console;
            r.decode_errors = decode_errors;
            r.ready = ready;
        });
    }

    fn link_failed(&mut self, e: std::io::Error) -> EngineError {
        let id = self.id.clone();
        let msg = format!("link {}: {e}", self.link.name());
        self.engine.radio_note(&id, |r| {
            r.connected = false;
            r.last_error = Some(msg.clone());
        });
        EngineError::NoRadio(msg)
    }

    pub fn kind(&self) -> LinkKind {
        self.link.kind()
    }
}
