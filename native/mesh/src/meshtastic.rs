// SPDX-License-Identifier: AGPL-3.0-or-later
//! Meshtastic host protocol: stream framing, `FromRadio`/`ToRadio` messages.
//!
//! Field numbers are taken from github.com/meshtastic/protobufs
//! (`mesh.proto`, `portnums.proto`, `telemetry.proto`, GPL-3.0). Framing is
//! from the client API document (docs/development/device/client-api): a
//! 4-byte header `0x94 0xC3` plus a big-endian 16-bit length; a length over
//! 512 means corruption and the reader resynchronises.
//!
//! This module is sans-I/O: bytes in, decoded frames out.

use crate::model::{now_ms, Event, Inbound, Position, Signal};
use crate::proto::{DecodeError, Fields, Writer};

pub const START1: u8 = 0x94;
pub const START2: u8 = 0xC3;
pub const MAX_PAYLOAD: usize = 512;
/// Broadcast destination (`NODENUM_BROADCAST`).
pub const BROADCAST: u32 = 0xFFFF_FFFF;

pub const PORT_TEXT: u32 = 1;
pub const PORT_POSITION: u32 = 3;
pub const PORT_NODEINFO: u32 = 4;
pub const PORT_ROUTING: u32 = 5;
pub const PORT_ADMIN: u32 = 6;
pub const PORT_WAYPOINT: u32 = 8;
pub const PORT_TELEMETRY: u32 = 67;

pub fn node_name(n: u32) -> String {
    format!("!{n:08x}")
}

/// Parse `!deadbeef` into a node number.
pub fn parse_node(s: &str) -> Option<u32> {
    u32::from_str_radix(s.strip_prefix('!')?, 16).ok()
}

pub fn frame(payload: &[u8]) -> Vec<u8> {
    let mut v = Vec::with_capacity(payload.len() + 4);
    v.extend_from_slice(&[
        START1,
        START2,
        (payload.len() >> 8) as u8,
        payload.len() as u8,
    ]);
    v.extend_from_slice(payload);
    v
}

/// Bytes that wake a sleeping serial radio before the first request: the
/// reference client sends START2 repeatedly. They are not a frame.
pub fn wake_bytes() -> Vec<u8> {
    vec![START2; 32]
}

/// Incremental stream reader. Bytes that are not part of a frame are the
/// radio's debug console and are counted, not decoded.
#[derive(Default)]
pub struct StreamDecoder {
    buf: Vec<u8>,
    pub console_bytes: u64,
    pub oversize_resyncs: u64,
}

impl StreamDecoder {
    pub fn new() -> Self {
        Self::default()
    }

    pub fn push(&mut self, data: &[u8]) -> Vec<Vec<u8>> {
        self.buf.extend_from_slice(data);
        let mut out = Vec::new();
        loop {
            // find START1 START2
            let mut i = 0;
            while i < self.buf.len() {
                if self.buf[i] == START1 {
                    if i + 1 >= self.buf.len() {
                        break;
                    }
                    if self.buf[i + 1] == START2 {
                        break;
                    }
                }
                i += 1;
            }
            if i > 0 {
                self.console_bytes += i as u64;
                self.buf.drain(..i);
            }
            if self.buf.len() < 4 || self.buf[0] != START1 || self.buf[1] != START2 {
                break;
            }
            let len = ((self.buf[2] as usize) << 8) | self.buf[3] as usize;
            if len > MAX_PAYLOAD {
                // corrupt header: drop one byte and look again
                self.oversize_resyncs += 1;
                self.console_bytes += 1;
                self.buf.drain(..1);
                continue;
            }
            if self.buf.len() < 4 + len {
                break;
            }
            out.push(self.buf[4..4 + len].to_vec());
            self.buf.drain(..4 + len);
        }
        out
    }
}

// ---- messages ----------------------------------------------------------

#[derive(Debug, Clone, Default, PartialEq)]
pub struct PositionMsg {
    pub latitude_i: Option<i32>,
    pub longitude_i: Option<i32>,
    pub altitude: Option<i32>,
    pub time: Option<u32>,
    pub timestamp: Option<u32>,
    pub pdop: Option<u32>,
    pub hdop: Option<u32>,
    pub gps_accuracy: Option<u32>,
    pub sats_in_view: Option<u32>,
    pub precision_bits: Option<u32>,
}

impl PositionMsg {
    pub fn decode(b: &[u8]) -> Result<Self, DecodeError> {
        let mut p = PositionMsg::default();
        for f in Fields::new(b) {
            let (n, v) = f?;
            match n {
                1 => p.latitude_i = Some(v.as_i32()),
                2 => p.longitude_i = Some(v.as_i32()),
                3 => p.altitude = Some(v.as_i32()),
                4 => p.time = Some(v.as_u32()),
                7 => p.timestamp = Some(v.as_u32()),
                11 => p.pdop = Some(v.as_u32()),
                12 => p.hdop = Some(v.as_u32()),
                14 => p.gps_accuracy = Some(v.as_u32()),
                19 => p.sats_in_view = Some(v.as_u32()),
                23 => p.precision_bits = Some(v.as_u32()),
                _ => {}
            }
        }
        Ok(p)
    }

    pub fn to_model(&self) -> Position {
        let t = self
            .time
            .filter(|t| *t != 0)
            .or(self.timestamp.filter(|t| *t != 0));
        Position {
            lat: self.latitude_i.map(|v| f64::from(v) * 1e-7),
            lon: self.longitude_i.map(|v| f64::from(v) * 1e-7),
            alt_m: self.altitude,
            time: t.map(u64::from),
            accuracy_m: None,
            hdop: self.hdop,
            pdop: self.pdop,
            gps_accuracy_mm: self.gps_accuracy,
            precision_bits: self.precision_bits,
            sats: self.sats_in_view,
            source: Some("packet".into()),
        }
    }
}

#[derive(Debug, Clone, Default, PartialEq)]
pub struct UserMsg {
    pub id: String,
    pub long_name: String,
    pub short_name: String,
    pub hw_model: Option<u32>,
    pub role: Option<u32>,
}

impl UserMsg {
    pub fn decode(b: &[u8]) -> Result<Self, DecodeError> {
        let mut u = UserMsg::default();
        for f in Fields::new(b) {
            let (n, v) = f?;
            match n {
                1 => u.id = String::from_utf8_lossy(v.as_bytes()).into_owned(),
                2 => u.long_name = String::from_utf8_lossy(v.as_bytes()).into_owned(),
                3 => u.short_name = String::from_utf8_lossy(v.as_bytes()).into_owned(),
                5 => u.hw_model = Some(v.as_u32()),
                7 => u.role = Some(v.as_u32()),
                _ => {}
            }
        }
        Ok(u)
    }
}

#[derive(Debug, Clone, Default, PartialEq)]
pub struct DataMsg {
    pub portnum: u32,
    pub payload: Vec<u8>,
    pub want_response: bool,
    pub dest: Option<u32>,
    pub source: Option<u32>,
    pub request_id: Option<u32>,
    pub reply_id: Option<u32>,
}

impl DataMsg {
    pub fn decode(b: &[u8]) -> Result<Self, DecodeError> {
        let mut d = DataMsg::default();
        for f in Fields::new(b) {
            let (n, v) = f?;
            match n {
                1 => d.portnum = v.as_u32(),
                2 => d.payload = v.as_bytes().to_vec(),
                3 => d.want_response = v.as_bool(),
                4 => d.dest = Some(v.as_u32()),
                5 => d.source = Some(v.as_u32()),
                6 => d.request_id = Some(v.as_u32()),
                7 => d.reply_id = Some(v.as_u32()),
                _ => {}
            }
        }
        Ok(d)
    }
}

#[derive(Debug, Clone, Default, PartialEq)]
pub struct MeshPacketMsg {
    pub from: u32,
    pub to: u32,
    pub channel: u32,
    pub decoded: Option<DataMsg>,
    pub encrypted: Option<Vec<u8>>,
    pub id: u32,
    pub rx_time: Option<u32>,
    pub rx_snr: Option<f32>,
    pub hop_limit: Option<u32>,
    pub want_ack: bool,
    pub rx_rssi: Option<i32>,
    pub via_mqtt: bool,
    pub hop_start: Option<u32>,
    pub pki_encrypted: bool,
    pub xeddsa_signed: bool,
}

impl MeshPacketMsg {
    pub fn decode(b: &[u8]) -> Result<Self, DecodeError> {
        let mut p = MeshPacketMsg::default();
        for f in Fields::new(b) {
            let (n, v) = f?;
            match n {
                1 => p.from = v.as_u32(),
                2 => p.to = v.as_u32(),
                3 => p.channel = v.as_u32(),
                4 => p.decoded = Some(DataMsg::decode(v.as_bytes())?),
                5 => p.encrypted = Some(v.as_bytes().to_vec()),
                6 => p.id = v.as_u32(),
                7 => p.rx_time = Some(v.as_u32()),
                8 => p.rx_snr = Some(v.as_f32()),
                9 => p.hop_limit = Some(v.as_u32()),
                10 => p.want_ack = v.as_bool(),
                12 => p.rx_rssi = Some(v.as_i32()),
                14 => p.via_mqtt = v.as_bool(),
                15 => p.hop_start = Some(v.as_u32()),
                17 => p.pki_encrypted = v.as_bool(),
                22 => p.xeddsa_signed = v.as_bool(),
                _ => {}
            }
        }
        Ok(p)
    }
}

#[derive(Debug, Clone, PartialEq)]
pub enum FromRadioMsg {
    Packet(MeshPacketMsg),
    MyInfo {
        my_node_num: u32,
    },
    NodeInfo {
        num: u32,
        user: Option<UserMsg>,
        position: Option<PositionMsg>,
        snr: Option<f32>,
        last_heard: Option<u32>,
        hops_away: Option<u32>,
    },
    ConfigCompleteId(u32),
    Rebooted,
    /// Config, channel, metadata, queue status and other radio-side messages.
    Other {
        field: u32,
    },
}

pub fn decode_from_radio(b: &[u8]) -> Result<(u32, FromRadioMsg), DecodeError> {
    let mut id = 0;
    let mut out: Option<FromRadioMsg> = None;
    for f in Fields::new(b) {
        let (n, v) = f?;
        match n {
            1 => id = v.as_u32(),
            2 => out = Some(FromRadioMsg::Packet(MeshPacketMsg::decode(v.as_bytes())?)),
            3 => {
                let mut num = 0;
                for g in Fields::new(v.as_bytes()) {
                    let (gn, gv) = g?;
                    if gn == 1 {
                        num = gv.as_u32();
                    }
                }
                out = Some(FromRadioMsg::MyInfo { my_node_num: num });
            }
            4 => {
                let (mut num, mut user, mut position, mut snr, mut last_heard, mut hops_away) =
                    (0, None, None, None, None, None);
                for g in Fields::new(v.as_bytes()) {
                    let (gn, gv) = g?;
                    match gn {
                        1 => num = gv.as_u32(),
                        2 => user = Some(UserMsg::decode(gv.as_bytes())?),
                        3 => position = Some(PositionMsg::decode(gv.as_bytes())?),
                        4 => snr = Some(gv.as_f32()),
                        5 => last_heard = Some(gv.as_u32()),
                        9 => hops_away = Some(gv.as_u32()),
                        _ => {}
                    }
                }
                out = Some(FromRadioMsg::NodeInfo {
                    num,
                    user,
                    position,
                    snr,
                    last_heard,
                    hops_away,
                });
            }
            7 => out = Some(FromRadioMsg::ConfigCompleteId(v.as_u32())),
            8 => out = Some(FromRadioMsg::Rebooted),
            _ => {
                if out.is_none() {
                    out = Some(FromRadioMsg::Other { field: n });
                }
            }
        }
    }
    Ok((id, out.unwrap_or(FromRadioMsg::Other { field: 0 })))
}

// ---- outbound ----------------------------------------------------------

/// `ToRadio { want_config_id }`
pub fn encode_want_config(id: u32) -> Vec<u8> {
    let mut w = Writer::new();
    w.varint(3, u64::from(id));
    w.finish()
}

/// `ToRadio { packet: MeshPacket { to, channel, decoded: Data { TEXT_MESSAGE_APP, payload }, id, want_ack } }`
/// `from` is left unset: the radio fills in its own node number.
pub fn encode_text(to: u32, channel: u32, text: &str, packet_id: u32, want_ack: bool) -> Vec<u8> {
    let mut data = Writer::new();
    data.varint(1, u64::from(PORT_TEXT));
    data.bytes(2, text.as_bytes());
    let mut pkt = Writer::new();
    pkt.fixed32(2, to);
    if channel != 0 {
        pkt.varint(3, u64::from(channel));
    }
    pkt.bytes(4, &data.finish());
    pkt.fixed32(6, packet_id);
    if want_ack {
        pkt.varint(10, 1);
    }
    let mut tr = Writer::new();
    tr.bytes(1, &pkt.finish());
    tr.finish()
}

// ---- telemetry ---------------------------------------------------------

fn telemetry_json(b: &[u8]) -> Result<(Option<u64>, String, serde_json::Value), DecodeError> {
    use serde_json::{json, Map, Value};
    let mut time = None;
    let mut kind = "other".to_string();
    let mut map = Map::new();
    for f in Fields::new(b) {
        let (n, v) = f?;
        match n {
            1 => time = Some(u64::from(v.as_u32())),
            2 => {
                kind = "device".into();
                for g in Fields::new(v.as_bytes()) {
                    let (gn, gv) = g?;
                    match gn {
                        1 => map.insert("battery_level".into(), json!(gv.as_u32())),
                        2 => map.insert("voltage".into(), json!(gv.as_f32())),
                        3 => map.insert("channel_utilization".into(), json!(gv.as_f32())),
                        4 => map.insert("air_util_tx".into(), json!(gv.as_f32())),
                        5 => map.insert("uptime_seconds".into(), json!(gv.as_u32())),
                        _ => None,
                    };
                }
            }
            3 => {
                kind = "environment".into();
                for g in Fields::new(v.as_bytes()) {
                    let (gn, gv) = g?;
                    let name = match gn {
                        1 => "temperature",
                        2 => "relative_humidity",
                        3 => "barometric_pressure",
                        4 => "gas_resistance",
                        5 => "voltage",
                        6 => "current",
                        _ => continue,
                    };
                    map.insert(name.into(), json!(gv.as_f32()));
                }
            }
            5 => {
                kind = "power".into();
                for g in Fields::new(v.as_bytes()) {
                    let (gn, gv) = g?;
                    let name = match gn {
                        1 => "ch1_voltage",
                        2 => "ch1_current",
                        3 => "ch2_voltage",
                        4 => "ch2_current",
                        5 => "ch3_voltage",
                        6 => "ch3_current",
                        _ => continue,
                    };
                    map.insert(name.into(), json!(gv.as_f32()));
                }
            }
            other => {
                kind = format!("variant_{other}");
            }
        }
    }
    Ok((time, kind, Value::Object(map)))
}

// ---- driver ------------------------------------------------------------

/// Sans-I/O Meshtastic session state: frames in, decoded `Inbound` out.
pub struct MeshtasticDriver {
    stream: StreamDecoder,
    pub my_node: Option<u32>,
    pub config_complete: bool,
    pub decode_errors: u64,
}

impl Default for MeshtasticDriver {
    fn default() -> Self {
        Self::new()
    }
}

impl MeshtasticDriver {
    pub fn new() -> Self {
        MeshtasticDriver {
            stream: StreamDecoder::new(),
            my_node: None,
            config_complete: false,
            decode_errors: 0,
        }
    }

    pub fn radio_id(&self) -> Option<String> {
        self.my_node.map(node_name)
    }

    pub fn console_bytes(&self) -> u64 {
        self.stream.console_bytes
    }

    /// The bytes that start a session: wake bytes, then a framed
    /// `want_config_id`. Reading the radio's configuration does not transmit.
    pub fn handshake(&self, nonce: u32) -> Vec<u8> {
        let mut v = wake_bytes();
        v.extend(frame(&encode_want_config(nonce)));
        v
    }

    pub fn feed(&mut self, data: &[u8]) -> Vec<Inbound> {
        let mut out = Vec::new();
        for payload in self.stream.push(data) {
            out.push(self.decode_frame(payload));
        }
        out
    }

    /// Decode one FromRadio payload. Never drops: a payload that does not
    /// parse becomes an `Opaque` event carrying the bytes.
    pub fn decode_frame(&mut self, payload: Vec<u8>) -> Inbound {
        let (_id, msg) = match decode_from_radio(&payload) {
            Ok(x) => x,
            Err(e) => {
                self.decode_errors += 1;
                let mut i = Inbound::control(payload, "undecodable");
                i.event = Event::Opaque {
                    port: None,
                    note: e.to_string(),
                };
                return i;
            }
        };
        match msg {
            FromRadioMsg::MyInfo { my_node_num } => {
                self.my_node = Some(my_node_num);
                let mut i = Inbound::control(payload, "my_info");
                i.from = Some(node_name(my_node_num));
                i
            }
            FromRadioMsg::ConfigCompleteId(_) => {
                self.config_complete = true;
                Inbound::control(payload, "config_complete")
            }
            FromRadioMsg::Rebooted => Inbound::control(payload, "rebooted"),
            FromRadioMsg::Other { field } => {
                Inbound::control(payload, &format!("radio_message_{field}"))
            }
            FromRadioMsg::NodeInfo {
                num,
                user,
                position,
                snr,
                last_heard,
                hops_away,
            } => {
                let pos = position
                    .as_ref()
                    .map(|p| p.to_model())
                    .filter(|p| p.has_fix());
                let u = user.unwrap_or_default();
                Inbound {
                    raw: payload,
                    event: Event::NodeInfo {
                        long_name: u.long_name,
                        short_name: u.short_name,
                        hw_model: u.hw_model,
                        position: pos.clone(),
                    },
                    from: Some(node_name(num)),
                    to: None,
                    channel: None,
                    packet_id: None,
                    radio_ts: last_heard.filter(|t| *t != 0).map(u64::from),
                    signal: Signal {
                        snr,
                        hops: hops_away,
                        ..Default::default()
                    },
                    position: pos,
                    broadcast: false,
                    trust: None,
                }
            }
            FromRadioMsg::Packet(p) => self.decode_packet(payload, p),
        }
    }

    fn decode_packet(&mut self, raw: Vec<u8>, p: MeshPacketMsg) -> Inbound {
        // hop_start == 0 is unknown on older firmware, not "direct". A zero
        // hop_limit is not sent on the wire (proto3 default), so with a
        // known hop_start an absent hop_limit means the limit was used up.
        let hop_start = p.hop_start.filter(|s| *s > 0);
        let hop_limit = hop_start.map(|_| p.hop_limit.unwrap_or(0)).or(p.hop_limit);
        let hops = match (hop_start, hop_limit) {
            (Some(s), Some(l)) if s >= l => Some(s - l),
            _ => None,
        };
        let signal = Signal {
            rssi: p.rx_rssi,
            snr: p.rx_snr,
            hops,
            hop_limit,
            hop_start,
        };
        let broadcast = p.to == BROADCAST;
        let trust = if p.xeddsa_signed {
            Some("signed".to_string())
        } else if p.pki_encrypted {
            Some("encrypted".to_string())
        } else if broadcast {
            Some("claimed".to_string())
        } else {
            None
        };
        let mut inb = Inbound {
            raw,
            event: Event::Opaque {
                port: None,
                note: "encrypted".into(),
            },
            from: Some(node_name(p.from)),
            to: Some(node_name(p.to)),
            channel: Some(p.channel),
            packet_id: Some(p.id).filter(|i| *i != 0),
            radio_ts: p.rx_time.filter(|t| *t != 0).map(u64::from),
            signal,
            position: None,
            broadcast,
            trust,
        };
        let Some(d) = p.decoded else {
            inb.event = Event::Opaque {
                port: None,
                note: "encrypted payload (radio did not decrypt)".into(),
            };
            return inb;
        };
        inb.event = match d.portnum {
            PORT_TEXT => Event::Text {
                text: String::from_utf8_lossy(&d.payload).into_owned(),
            },
            PORT_POSITION => match PositionMsg::decode(&d.payload) {
                Ok(pm) => {
                    let pos = pm.to_model();
                    inb.position = Some(pos.clone());
                    Event::Position(pos)
                }
                Err(e) => Event::Opaque {
                    port: Some(d.portnum),
                    note: e.to_string(),
                },
            },
            PORT_NODEINFO => match UserMsg::decode(&d.payload) {
                Ok(u) => Event::NodeInfo {
                    long_name: u.long_name,
                    short_name: u.short_name,
                    hw_model: u.hw_model,
                    position: None,
                },
                Err(e) => Event::Opaque {
                    port: Some(d.portnum),
                    note: e.to_string(),
                },
            },
            PORT_TELEMETRY => match telemetry_json(&d.payload) {
                Ok((time, kind, values)) => {
                    if inb.radio_ts.is_none() {
                        inb.radio_ts = time.filter(|t| *t != 0);
                    }
                    Event::Telemetry { kind, values }
                }
                Err(e) => Event::Opaque {
                    port: Some(d.portnum),
                    note: e.to_string(),
                },
            },
            PORT_ROUTING => {
                let mut err = None;
                for f in Fields::new(&d.payload).flatten() {
                    if f.0 == 3 {
                        err = Some(f.1.as_u32());
                    }
                }
                Event::Ack {
                    request_id: d.request_id.unwrap_or(0),
                    error: err
                        .filter(|e| *e != 0)
                        .map(|e| format!("routing_error_{e}")),
                }
            }
            other => Event::Opaque {
                port: Some(other),
                note: format!("port {other} not decoded"),
            },
        };
        inb
    }

    /// Build the framed bytes for a text send plus the unframed ToRadio
    /// payload (what the record stores).
    pub fn text_frame(
        &self,
        to: u32,
        channel: u32,
        text: &str,
        packet_id: u32,
        want_ack: bool,
    ) -> (Vec<u8>, Vec<u8>) {
        let payload = encode_text(to, channel, text, packet_id, want_ack);
        (frame(&payload), payload)
    }
}

/// A fresh packet id. Meshtastic ids are random 32-bit values and a repeat
/// can make the mesh drop the second packet as a duplicate, so the id is a
/// counter that starts from a clock-derived seed: never the same twice in a
/// process, and different between runs.
pub fn fresh_packet_id() -> u32 {
    use std::sync::atomic::{AtomicU32, Ordering};
    static NEXT: AtomicU32 = AtomicU32::new(0);
    if NEXT.load(Ordering::Relaxed) == 0 {
        let seed = (now_ms() as u32).wrapping_mul(2_654_435_761) | 1;
        let _ = NEXT.compare_exchange(0, seed, Ordering::Relaxed, Ordering::Relaxed);
    }
    let id = NEXT.fetch_add(1, Ordering::Relaxed);
    if id == 0 {
        NEXT.fetch_add(1, Ordering::Relaxed)
    } else {
        id
    }
}
