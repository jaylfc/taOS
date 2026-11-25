// SPDX-License-Identifier: AGPL-3.0-or-later
//! MeshCore companion protocol (USB serial, BLE Nordic UART, TCP).
//!
//! Sources (MIT): meshcore-dev/MeshCore `docs/companion_protocol.md` and
//! `examples/companion_radio/MyMesh.cpp`; meshcore-dev/meshcore_py
//! `reader.py` as the decoding oracle. All integers are little-endian.
//!
//! Serial framing: host to radio `'<'` + u16 length + payload; radio to host
//! `'>'` + u16 length + payload. BLE: one characteristic write or
//! notification is exactly one frame, with no length prefix. The protocol
//! document says it is "still in development", so the driver reports frames
//! it does not know as control frames with their raw bytes instead of
//! failing.
//!
//! Sans-I/O. The driver is read-only in its handshake: APP_START,
//! DEVICE_QUERY, GET_CONTACTS and SYNC_NEXT_MESSAGE; it does not set the
//! radio clock or change any setting.

use crate::model::{to_hex, Event, Inbound, Position, Signal};

pub const CMD_APP_START: u8 = 0x01;
pub const CMD_SEND_TXT_MSG: u8 = 0x02;
pub const CMD_SEND_CHANNEL_TXT_MSG: u8 = 0x03;
pub const CMD_GET_CONTACTS: u8 = 0x04;
pub const CMD_SYNC_NEXT_MESSAGE: u8 = 0x0A;
pub const CMD_DEVICE_QUERY: u8 = 0x16;

pub const RESP_OK: u8 = 0;
pub const RESP_ERR: u8 = 1;
pub const RESP_CONTACTS_START: u8 = 2;
pub const RESP_CONTACT: u8 = 3;
pub const RESP_END_OF_CONTACTS: u8 = 4;
pub const RESP_SELF_INFO: u8 = 5;
pub const RESP_SENT: u8 = 6;
pub const RESP_CONTACT_MSG_RECV: u8 = 7;
pub const RESP_CHANNEL_MSG_RECV: u8 = 8;
pub const RESP_NO_MORE_MESSAGES: u8 = 10;
pub const RESP_DEVICE_INFO: u8 = 13;
pub const RESP_CONTACT_MSG_RECV_V3: u8 = 16;
pub const RESP_CHANNEL_MSG_RECV_V3: u8 = 17;
pub const PUSH_ADVERT: u8 = 0x80;
pub const PUSH_SEND_CONFIRMED: u8 = 0x82;
pub const PUSH_MSG_WAITING: u8 = 0x83;
pub const PUSH_NEW_ADVERT: u8 = 0x8A;

pub const SERIAL_MAX_FRAME: usize = 300;
pub const APP_PROTOCOL_VERSION: u8 = 3;

/// `'<'` + length + payload, for USB serial and TCP.
pub fn frame_to_radio(payload: &[u8]) -> Vec<u8> {
    let mut v = vec![b'<', payload.len() as u8, (payload.len() >> 8) as u8];
    v.extend_from_slice(payload);
    v
}

/// Reader for the radio-to-host serial stream. Bytes before a `'>'` are
/// console text and are counted, not decoded.
#[derive(Default)]
pub struct SerialDecoder {
    buf: Vec<u8>,
    pub console_bytes: u64,
    pub oversize_resyncs: u64,
}

impl SerialDecoder {
    pub fn new() -> Self {
        Self::default()
    }
    pub fn push(&mut self, data: &[u8]) -> Vec<Vec<u8>> {
        self.buf.extend_from_slice(data);
        let mut out = Vec::new();
        loop {
            match self.buf.iter().position(|b| *b == b'>') {
                None => {
                    self.console_bytes += self.buf.len() as u64;
                    self.buf.clear();
                    break;
                }
                Some(i) if i > 0 => {
                    self.console_bytes += i as u64;
                    self.buf.drain(..i);
                }
                _ => {}
            }
            if self.buf.len() < 3 {
                break;
            }
            let len = u16::from_le_bytes([self.buf[1], self.buf[2]]) as usize;
            if len > SERIAL_MAX_FRAME {
                self.oversize_resyncs += 1;
                self.console_bytes += 1;
                self.buf.drain(..1);
                continue;
            }
            if self.buf.len() < 3 + len {
                break;
            }
            out.push(self.buf[3..3 + len].to_vec());
            self.buf.drain(..3 + len);
        }
        out
    }
}

pub fn app_start(name: &str) -> Vec<u8> {
    // 0x01, reserved bytes (the reference client puts the app protocol
    // version in byte 1 and spaces after it), then the application name.
    let mut v = vec![CMD_APP_START, APP_PROTOCOL_VERSION];
    v.extend_from_slice(b"      ");
    v.extend_from_slice(name.as_bytes());
    v
}

pub fn device_query() -> Vec<u8> {
    vec![CMD_DEVICE_QUERY, APP_PROTOCOL_VERSION]
}

pub fn sync_next_message() -> Vec<u8> {
    vec![CMD_SYNC_NEXT_MESSAGE]
}

pub fn get_contacts() -> Vec<u8> {
    vec![CMD_GET_CONTACTS]
}

/// `02 | txt_type 0 | attempt 0 | timestamp | 6-byte pubkey prefix | text`
pub fn send_direct(prefix: &[u8; 6], ts: u32, text: &str) -> Vec<u8> {
    let mut v = vec![CMD_SEND_TXT_MSG, 0, 0];
    v.extend_from_slice(&ts.to_le_bytes());
    v.extend_from_slice(prefix);
    v.extend_from_slice(text.as_bytes());
    v
}

/// `03 | txt_type 0 | channel index | timestamp | text`
pub fn send_channel(channel: u8, ts: u32, text: &str) -> Vec<u8> {
    let mut v = vec![CMD_SEND_CHANNEL_TXT_MSG, 0, channel];
    v.extend_from_slice(&ts.to_le_bytes());
    v.extend_from_slice(text.as_bytes());
    v
}

pub fn parse_prefix(s: &str) -> Option<[u8; 6]> {
    let b = crate::model::from_hex(s)?;
    b.get(..6)?.try_into().ok()
}

fn u32le(b: &[u8]) -> u32 {
    u32::from_le_bytes(b[..4].try_into().unwrap())
}
fn i32le(b: &[u8]) -> i32 {
    i32::from_le_bytes(b[..4].try_into().unwrap())
}

/// Hops from a frame's path_len byte. MyMesh.cpp sets it to the flood
/// packet's path length, or 0xFF when the message was not flood routed (it
/// came by a stored direct path, whose length the frame does not report):
/// unknown, not zero. The top 2 bits are the hash size mode.
fn path_len(b: u8) -> Option<u32> {
    if b == 255 {
        None
    } else {
        Some(u32::from(b & 0x3F))
    }
}

pub struct MeshcoreDriver {
    serial: SerialDecoder,
    pub self_prefix: Option<String>,
    pub app_name: String,
    pub version: Option<u8>,
    pub decode_errors: u64,
    draining: bool,
}

impl MeshcoreDriver {
    pub fn new(app_name: &str) -> Self {
        MeshcoreDriver { serial: SerialDecoder::new(), self_prefix: None, app_name: app_name.into(), version: None, decode_errors: 0, draining: false }
    }

    pub fn radio_id(&self) -> Option<String> {
        self.self_prefix.clone()
    }

    pub fn console_bytes(&self) -> u64 {
        self.serial.console_bytes
    }

    /// Commands that open a session, as payloads (not framed).
    pub fn handshake(&self) -> Vec<Vec<u8>> {
        vec![app_start(&self.app_name), device_query(), get_contacts(), sync_next_message()]
    }

    /// Feed bytes from a serial or TCP stream. Returns decoded frames and
    /// the command payloads the driver wants sent in reply.
    pub fn feed_stream(&mut self, data: &[u8]) -> (Vec<Inbound>, Vec<Vec<u8>>) {
        let mut inb = Vec::new();
        let mut cmds = Vec::new();
        for f in self.serial.push(data) {
            let (i, c) = self.feed_frame(f);
            inb.push(i);
            cmds.extend(c);
        }
        (inb, cmds)
    }

    /// Feed exactly one frame (BLE notification, or one decoded serial frame).
    pub fn feed_frame(&mut self, f: Vec<u8>) -> (Inbound, Vec<Vec<u8>>) {
        let mut cmds = Vec::new();
        if f.is_empty() {
            self.decode_errors += 1;
            return (Inbound::control(f, "empty_frame"), cmds);
        }
        let code = f[0];
        let inb = match code {
            RESP_SELF_INFO => self.self_info(f),
            RESP_DEVICE_INFO => {
                self.version = f.get(1).copied();
                Inbound::control(f, "device_info")
            }
            RESP_CONTACTS_START => Inbound::control(f, "contacts_start"),
            RESP_END_OF_CONTACTS => Inbound::control(f, "contacts_end"),
            RESP_OK => Inbound::control(f, "ok"),
            RESP_ERR => Inbound::control(f, "error"),
            RESP_SENT => {
                // 06 | route flag | expected_ack u32 | suggested timeout u32
                if f.len() >= 10 {
                    let ack = u32le(&f[2..]);
                    let mut i = Inbound::control(f, "sent");
                    i.event = Event::Sent { expected_ack: ack };
                    i
                } else {
                    self.bad(f, "SENT too short")
                }
            }
            RESP_CONTACT | PUSH_NEW_ADVERT => self.contact(f),
            RESP_CONTACT_MSG_RECV | RESP_CONTACT_MSG_RECV_V3 => {
                self.draining = true;
                cmds.push(sync_next_message());
                self.contact_msg(f)
            }
            RESP_CHANNEL_MSG_RECV | RESP_CHANNEL_MSG_RECV_V3 => {
                self.draining = true;
                cmds.push(sync_next_message());
                self.channel_msg(f)
            }
            RESP_NO_MORE_MESSAGES => {
                self.draining = false;
                Inbound::control(f, "no_more_messages")
            }
            PUSH_MSG_WAITING => {
                if !self.draining {
                    cmds.push(sync_next_message());
                }
                Inbound::control(f, "messages_waiting")
            }
            PUSH_SEND_CONFIRMED => {
                let id = f.get(1..5).map(u32le).unwrap_or(0);
                let mut i = Inbound::control(f, "send_confirmed");
                i.event = Event::Ack { request_id: id, error: None };
                i
            }
            PUSH_ADVERT => Inbound::control(f, "advert"),
            other => Inbound::control(f, &format!("code_{other:#04x}")),
        };
        (inb, cmds)
    }

    fn bad(&mut self, raw: Vec<u8>, why: &str) -> Inbound {
        self.decode_errors += 1;
        let mut i = Inbound::control(raw, "undecodable");
        i.event = Event::Opaque { port: None, note: why.into() };
        i
    }

    fn self_info(&mut self, f: Vec<u8>) -> Inbound {
        // 05 | adv_type | tx_power | max_tx_power | pubkey(32) | lat i32 | lon i32 | ...
        if f.len() < 4 + 32 + 8 {
            return self.bad(f, "SELF_INFO too short");
        }
        let prefix = to_hex(&f[4..10]);
        self.self_prefix = Some(prefix.clone());
        let mut i = Inbound::control(f.clone(), "self_info");
        i.from = Some(prefix);
        let (lat, lon) = (i32le(&f[36..]), i32le(&f[40..]));
        if lat != 0 || lon != 0 {
            i.position = Some(Position { lat: Some(f64::from(lat) / 1e6), lon: Some(f64::from(lon) / 1e6), source: Some("packet".into()), ..Default::default() });
        }
        i
    }

    fn contact(&mut self, f: Vec<u8>) -> Inbound {
        // code | pubkey 32 | type | flags | out_path_len | out_path 64 | name 32 | last_advert u32 | lat i32 | lon i32 | lastmod u32
        if f.len() < 148 {
            return self.bad(f, "CONTACT too short");
        }
        let prefix = to_hex(&f[1..7]);
        let name_bytes = &f[100..132];
        let name = String::from_utf8_lossy(name_bytes.split(|b| *b == 0).next().unwrap_or(&[])).into_owned();
        let last_advert = u32le(&f[132..]);
        let (lat, lon) = (i32le(&f[136..]), i32le(&f[140..]));
        let position = (lat != 0 || lon != 0).then(|| Position {
            lat: Some(f64::from(lat) / 1e6),
            lon: Some(f64::from(lon) / 1e6),
            time: Some(u64::from(last_advert)).filter(|t| *t != 0),
            source: Some("packet".into()),
            ..Default::default()
        });
        let ev = match &position {
            Some(p) if f[0] == PUSH_NEW_ADVERT => Event::Position(p.clone()),
            _ => Event::NodeInfo { long_name: name, short_name: String::new(), hw_model: Some(u32::from(f[33])), position: position.clone() },
        };
        Inbound {
            raw: f.clone(),
            event: ev,
            from: Some(prefix),
            to: None,
            channel: None,
            packet_id: None,
            radio_ts: Some(u64::from(last_advert)).filter(|t| *t != 0),
            signal: Signal::default(),
            position,
            broadcast: f[0] == PUSH_NEW_ADVERT,
            trust: None,
        }
    }

    fn contact_msg(&mut self, f: Vec<u8>) -> Inbound {
        // v3: 10 | snr i8*4 | 2 reserved | prefix 6 | path_len | txt_type | ts u32 | [sig 4 if txt_type==2] | text
        // v2: 07 |                         prefix 6 | path_len | txt_type | ts u32 | ...
        let v3 = f[0] == RESP_CONTACT_MSG_RECV_V3;
        let mut o = 1;
        let mut snr = None;
        if v3 {
            if f.len() < 4 {
                return self.bad(f, "contact message too short");
            }
            snr = Some(f32::from(f[1] as i8) / 4.0);
            o = 4;
        }
        if f.len() < o + 6 + 1 + 1 + 4 {
            return self.bad(f, "contact message too short");
        }
        let prefix = to_hex(&f[o..o + 6]);
        let plen = f[o + 6];
        let txt_type = f[o + 7];
        let ts = u32le(&f[o + 8..]);
        let mut p = o + 12;
        if txt_type == 2 {
            p += 4;
        }
        let text = String::from_utf8_lossy(f.get(p..).unwrap_or(&[])).into_owned();
        Inbound {
            raw: f,
            event: Event::Text { text },
            from: Some(prefix),
            to: self.self_prefix.clone(),
            channel: None,
            packet_id: None,
            radio_ts: Some(u64::from(ts)),
            signal: Signal { snr, hops: path_len(plen), ..Default::default() },
            position: None,
            broadcast: false,
            trust: if txt_type == 2 { Some("signed".into()) } else { None },
        }
    }

    fn channel_msg(&mut self, f: Vec<u8>) -> Inbound {
        // v3: 11 | snr | 2 reserved | channel | path_len | txt_type | ts u32 | text
        // v2: 08 |                   channel | path_len | txt_type | ts u32 | text
        let v3 = f[0] == RESP_CHANNEL_MSG_RECV_V3;
        let mut o = 1;
        let mut snr = None;
        if v3 {
            if f.len() < 4 {
                return self.bad(f, "channel message too short");
            }
            snr = Some(f32::from(f[1] as i8) / 4.0);
            o = 4;
        }
        if f.len() < o + 7 {
            return self.bad(f, "channel message too short");
        }
        let channel = u32::from(f[o]);
        let plen = f[o + 1];
        let ts = u32le(&f[o + 3..]);
        let text = String::from_utf8_lossy(f.get(o + 7..).unwrap_or(&[])).into_owned();
        Inbound {
            raw: f,
            event: Event::Text { text },
            // a channel message carries no sender key; the text usually
            // starts with the sender's name
            from: None,
            to: None,
            channel: Some(channel),
            packet_id: None,
            radio_ts: Some(u64::from(ts)),
            signal: Signal { snr, hops: path_len(plen), ..Default::default() },
            position: None,
            broadcast: true,
            trust: Some("claimed".into()),
        }
    }
}
