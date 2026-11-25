// SPDX-License-Identifier: AGPL-3.0-or-later
//! Shared data model: events decoded by drivers and the stored record.

use serde::{Deserialize, Serialize};
use sha2::{Digest, Sha256};

pub const NETWORK_MESHTASTIC: &str = "meshtastic";
pub const NETWORK_MESHCORE: &str = "meshcore";

/// A position as carried by a frame. Every field is optional because radios
/// send partial positions; unknown stays unknown (never zero).
#[derive(Debug, Clone, Default, PartialEq, Serialize, Deserialize)]
pub struct Position {
    #[serde(skip_serializing_if = "Option::is_none")]
    pub lat: Option<f64>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub lon: Option<f64>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub alt_m: Option<i32>,
    /// Position time, seconds since 1970, as reported by the radio.
    #[serde(skip_serializing_if = "Option::is_none")]
    pub time: Option<u64>,
    /// Horizontal accuracy in metres when the radio states one. Meshtastic
    /// does not (it sends DOP and a hardware constant), so this stays empty
    /// there and `hdop`, `pdop`, `gps_accuracy_mm` and `precision_bits` carry
    /// the raw figures.
    #[serde(skip_serializing_if = "Option::is_none")]
    pub accuracy_m: Option<f64>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub hdop: Option<u32>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub pdop: Option<u32>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub gps_accuracy_mm: Option<u32>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub precision_bits: Option<u32>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub sats: Option<u32>,
    /// Where this position came from when it is attached to a message:
    /// "packet" (the frame itself) or "last_known" (the sender's most
    /// recent position frame, with its own `time`).
    #[serde(skip_serializing_if = "Option::is_none")]
    pub source: Option<String>,
}

impl Position {
    pub fn has_fix(&self) -> bool {
        self.lat.is_some() && self.lon.is_some()
    }
}

#[derive(Debug, Clone, Default, PartialEq, Serialize, Deserialize)]
pub struct Signal {
    #[serde(skip_serializing_if = "Option::is_none")]
    pub rssi: Option<i32>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub snr: Option<f32>,
    /// Hops travelled; None when the firmware did not say (unknown, not 0).
    #[serde(skip_serializing_if = "Option::is_none")]
    pub hops: Option<u32>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub hop_limit: Option<u32>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub hop_start: Option<u32>,
}

/// What a driver decoded from one frame.
#[derive(Debug, Clone, PartialEq)]
pub enum Event {
    Text {
        text: String,
    },
    Position(Position),
    NodeInfo {
        long_name: String,
        short_name: String,
        hw_model: Option<u32>,
        position: Option<Position>,
    },
    Telemetry {
        kind: String,
        values: serde_json::Value,
    },
    /// Routing ack/nak for an earlier packet we sent or saw.
    Ack {
        request_id: u32,
        error: Option<String>,
    },
    /// Handshake, config, channel and similar traffic from the radio itself.
    Control {
        what: String,
    },
    /// A packet whose payload the radio could not decrypt, or a port we do
    /// not decode. The raw bytes are in the record.
    Opaque {
        port: Option<u32>,
        note: String,
    },
    /// A MeshCore radio accepted a send and says which acknowledgement code to expect.
    Sent {
        expected_ack: u32,
    },
}

impl Event {
    pub fn kind(&self) -> &'static str {
        match self {
            Event::Text { .. } => "text",
            Event::Position(_) => "position",
            Event::NodeInfo { .. } => "nodeinfo",
            Event::Telemetry { .. } => "telemetry",
            Event::Ack { .. } => "ack",
            Event::Control { .. } => "control",
            Event::Opaque { .. } => "opaque",
            Event::Sent { .. } => "send_info",
        }
    }
}

/// One decoded inbound frame with its metadata, before storage.
#[derive(Debug, Clone, PartialEq)]
pub struct Inbound {
    pub raw: Vec<u8>,
    pub event: Event,
    pub from: Option<String>,
    pub to: Option<String>,
    pub channel: Option<u32>,
    pub packet_id: Option<u32>,
    pub radio_ts: Option<u64>,
    pub signal: Signal,
    pub position: Option<Position>,
    /// True when `to` is the network's broadcast address.
    pub broadcast: bool,
    /// "encrypted" (the frame says it was public-key encrypted) or "signed"
    /// (the frame says it carries a signature); None when the frame says neither.
    pub trust: Option<String>,
}

impl Inbound {
    pub fn control(raw: Vec<u8>, what: &str) -> Self {
        Inbound {
            raw,
            event: Event::Control {
                what: what.to_string(),
            },
            from: None,
            to: None,
            channel: None,
            packet_id: None,
            radio_ts: None,
            signal: Signal::default(),
            position: None,
            broadcast: false,
            trust: None,
        }
    }
}

/// Who a radio belongs to, as a stable reference string:
/// `agent:<taos agent id>`, `person:<taos account id>`, `contact:<id>`.
pub type IdentityRef = String;

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct Attribution {
    pub identity: IdentityRef,
    pub name: String,
    /// For a contact linked to a taOS account: that account's reference.
    #[serde(skip_serializing_if = "Option::is_none")]
    pub linked_account: Option<IdentityRef>,
}

pub const RECORD_VERSION: u32 = 1;

/// The stored record. One line of the append-only store. Fields are only
/// ever added; an old line stays readable.
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct Record {
    pub v: u32,
    /// Stable id: SHA-256 over the immutable core of the record, 32 hex chars.
    pub id: String,
    /// "in", "out" or "state" (a state change of an outbound record).
    pub dir: String,
    pub network: String,
    /// The taOS identity that owns this engine's store (the person it runs
    /// for). Present on every row so the taOSmd replay always has an owner.
    #[serde(default)]
    pub owner: String,
    /// The radio that carried the frame (its node id).
    pub radio: String,
    /// The identity the radio belongs to (who heard or sent it for us).
    #[serde(skip_serializing_if = "Option::is_none")]
    pub observer: Option<IdentityRef>,
    pub kind: String,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub channel: Option<u32>,
    pub host_ts_ms: u64,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub radio_ts: Option<u64>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub from: Option<String>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub to: Option<String>,
    pub broadcast: bool,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub packet_id: Option<u32>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub text: Option<String>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub position: Option<Position>,
    #[serde(default)]
    pub signal: Signal,
    /// Decoded fields other than text and position (nodeinfo, telemetry, ack).
    #[serde(skip_serializing_if = "Option::is_none")]
    pub detail: Option<serde_json::Value>,
    /// Exact bytes of the frame as it crossed the host link.
    pub raw_hex: String,
    /// Who sent it, as known when the frame was stored.
    #[serde(skip_serializing_if = "Option::is_none")]
    pub sender: Option<Attribution>,
    /// Who the frame was addressed to, as known when stored.
    #[serde(skip_serializing_if = "Option::is_none")]
    pub recipient: Option<Attribution>,
    /// Outbound only: the principal that asked for the send ("owner" or an agent id).
    #[serde(skip_serializing_if = "Option::is_none")]
    pub principal: Option<String>,
    /// State records only: the record this one updates, and the new state.
    #[serde(skip_serializing_if = "Option::is_none")]
    pub ref_id: Option<String>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub state: Option<String>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub note: Option<String>,
    /// From flags in the frame only: "encrypted", "signed", or "claimed" for
    /// an unsigned broadcast. Absent when the frame does not say.
    #[serde(skip_serializing_if = "Option::is_none")]
    pub trust: Option<String>,
    /// Never stored. Added to outbound records returned by queries: the
    /// folded delivery state (queued, sent, acknowledged, failed).
    #[serde(skip_serializing_if = "Option::is_none")]
    pub delivery: Option<String>,
}

pub fn to_hex(b: &[u8]) -> String {
    let mut s = String::with_capacity(b.len() * 2);
    for x in b {
        s.push_str(&format!("{x:02x}"));
    }
    s
}

pub fn from_hex(s: &str) -> Option<Vec<u8>> {
    if s.len() % 2 != 0 {
        return None;
    }
    (0..s.len())
        .step_by(2)
        .map(|i| u8::from_str_radix(s.get(i..i + 2)?, 16).ok())
        .collect()
}

impl Record {
    /// Compute the stable id from the immutable core. The same frame stored
    /// twice (a replay) gets the same id, so replays are idempotent.
    pub fn compute_id(
        dir: &str,
        network: &str,
        radio: &str,
        host_ts_ms: u64,
        seq: u64,
        raw_hex: &str,
        ref_id: Option<&str>,
        state: Option<&str>,
    ) -> String {
        let mut h = Sha256::new();
        for part in [
            dir,
            network,
            radio,
            raw_hex,
            ref_id.unwrap_or(""),
            state.unwrap_or(""),
        ] {
            h.update(part.as_bytes());
            h.update([0u8]);
        }
        h.update(host_ts_ms.to_be_bytes());
        h.update(seq.to_be_bytes());
        to_hex(&h.finalize()[..16])
    }
}

pub fn now_ms() -> u64 {
    std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)
        .map(|d| d.as_millis() as u64)
        .unwrap_or(0)
}
