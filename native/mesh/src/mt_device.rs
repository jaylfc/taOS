// SPDX-License-Identifier: AGPL-3.0-or-later
//! What a Meshtastic radio says about itself during the handshake (firmware, board, region, channels, names) and the
//! admin writes that change it. Field numbers: meshtastic/protobufs `mesh.proto` (FromRadio, DeviceMetadata, User,
//! MeshPacket), `config.proto` (Config, LoRaConfig, DeviceConfig, PositionConfig), `channel.proto`, `admin.proto`
//! (AdminMessage) and `portnums.proto` (ADMIN_APP = 6). Checked against the firmware's AdminModule.cpp: an admin
//! message from the local host link (`from` unset) needs no session passkey; the radio saves once on
//! `commit_edit_settings`, and may reboot if the change needs it.
//!
//! The radio's own bytes are kept and patched, never rebuilt from a model: a field this code does not know survives
//! a write untouched.

use crate::meshtastic_enums::{HW_MODELS, MODEM_PRESETS, REGIONS, ROLES};
use crate::proto::{Fields, Val, Writer};
use serde_json::{json, Map, Value};
use std::collections::BTreeMap;

const ADMIN_APP: u64 = 6;
const PRIORITY_RELIABLE: u64 = 70;

/// A friendly board name from the hardware enum: HELTEC_V3 is "Heltec V3".
pub fn hw_label(n: u32) -> String {
    let name = HW_MODELS.iter().find(|(k, _)| *k == n).map(|(_, v)| *v);
    let Some(name) = name else { return format!("Unknown board ({n})") };
    if name == "UNSET" {
        return "Unknown board".into();
    }
    name.split('_')
        .map(|w| {
            if w.len() <= 2 || w.chars().any(|c| c.is_ascii_digit()) {
                w.to_string()
            } else {
                let mut c = w.chars();
                c.next().map(|f| f.to_string() + &c.as_str().to_lowercase()).unwrap_or_default()
            }
        })
        .collect::<Vec<_>>()
        .join(" ")
}

fn name_of(t: &[(u32, &str)], n: u32) -> Option<String> {
    t.iter().find(|(k, _)| *k == n).map(|(_, v)| (*v).to_string())
}
fn num_of(t: &[(u32, &str)], s: &str) -> Option<u32> {
    t.iter().find(|(_, v)| *v == s).map(|(k, _)| *k)
}

pub fn b64(b: &[u8], url: bool) -> String {
    let t: &[u8; 64] = if url {
        b"ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-_"
    } else {
        b"ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/"
    };
    let mut o = String::new();
    for c in b.chunks(3) {
        let n = (u32::from(c[0]) << 16) | (u32::from(*c.get(1).unwrap_or(&0)) << 8) | u32::from(*c.get(2).unwrap_or(&0));
        o.push(t[(n >> 18) as usize & 63] as char);
        o.push(t[(n >> 12) as usize & 63] as char);
        if c.len() > 1 {
            o.push(t[(n >> 6) as usize & 63] as char);
        } else if !url {
            o.push('=');
        }
        if c.len() > 2 {
            o.push(t[n as usize & 63] as char);
        } else if !url {
            o.push('=');
        }
    }
    o
}

// ---- patching raw protobuf messages -------------------------------------------------------------------------------

pub enum Edit {
    Varint(u32, u64),
    Bytes(u32, Vec<u8>),
    /// edit a nested message (created empty when absent)
    Nested(u32, Vec<Edit>),
}

/// Apply edits to a serialised message: every other field is copied through, edited fields are replaced (a zero
/// varint is dropped, as proto3 does), and the result is in field order.
pub fn patch(raw: &[u8], edits: &[Edit]) -> Vec<u8> {
    let mut fields: Vec<(u32, Field)> = vec![];
    for (n, v) in Fields::new(raw).flatten() {
        fields.push((
            n,
            match v {
                Val::Varint(x) => Field::Varint(x),
                Val::Fixed64(x) => Field::Fixed64(x),
                Val::Fixed32(x) => Field::Fixed32(x),
                Val::Len(b) => Field::Len(b.to_vec()),
            },
        ));
    }
    for e in edits {
        let (n, new) = match e {
            Edit::Varint(n, v) => (*n, (*v != 0).then_some(Field::Varint(*v))),
            Edit::Bytes(n, b) => (*n, Some(Field::Len(b.clone()))),
            Edit::Nested(n, inner) => {
                let old = fields.iter().find(|(k, _)| k == n).and_then(|(_, f)| match f {
                    Field::Len(b) => Some(b.clone()),
                    _ => None,
                });
                (*n, Some(Field::Len(patch(&old.unwrap_or_default(), inner))))
            }
        };
        fields.retain(|(k, _)| *k != n);
        if let Some(f) = new {
            fields.push((n, f));
        }
    }
    fields.sort_by_key(|(n, _)| *n);
    let mut w = Writer::new();
    for (n, f) in fields {
        match f {
            Field::Varint(v) => w.varint(n, v),
            Field::Fixed32(v) => w.fixed32(n, v),
            Field::Fixed64(v) => {
                // field key (wire type 1) then 8 bytes
                w.buf.push(((n << 3) | 1) as u8);
                w.buf.extend_from_slice(&v.to_le_bytes());
            }
            Field::Len(b) => w.bytes(n, &b),
        }
    }
    w.finish()
}

enum Field {
    Varint(u64),
    Fixed64(u64),
    Fixed32(u32),
    Len(Vec<u8>),
}

fn get_len(raw: &[u8], field: u32) -> Option<Vec<u8>> {
    Fields::new(raw).flatten().find(|(n, _)| *n == field).and_then(|(_, v)| match v {
        Val::Len(b) => Some(b.to_vec()),
        _ => None,
    })
}
fn get_varint(raw: &[u8], field: u32) -> Option<u64> {
    Fields::new(raw).flatten().find(|(n, _)| *n == field).map(|(_, v)| v.as_u64())
}
fn get_str(raw: &[u8], field: u32) -> Option<String> {
    get_len(raw, field).map(|b| String::from_utf8_lossy(&b).into_owned())
}

// ---- what the radio told us ---------------------------------------------------------------------------------------

#[derive(Debug, Clone, Default, PartialEq)]
pub struct DeviceState {
    pub my_node: Option<u32>,
    pub firmware: Option<String>,
    pub hw_model: Option<u32>,
    pub lora: Option<Vec<u8>>,
    pub device: Option<Vec<u8>>,
    pub position: Option<Vec<u8>>,
    pub channels: BTreeMap<u32, Vec<u8>>,
    pub long_name: Option<String>,
    pub short_name: Option<String>,
    pub complete: bool,
}

impl DeviceState {
    /// Look at one FromRadio payload. `my_node` is the radio's own node number once my_info has been seen.
    pub fn observe(&mut self, payload: &[u8]) {
        for (n, v) in Fields::new(payload).flatten() {
            let Val::Len(b) = v else {
                if n == 7 {
                    self.complete = true;
                }
                continue;
            };
            match n {
                3 => self.my_node = get_varint(b, 1).map(|x| x as u32),
                4 => {
                    let num = get_varint(b, 1).map(|x| x as u32);
                    if num.is_some() && num == self.my_node {
                        if let Some(u) = get_len(b, 2) {
                            self.long_name = get_str(&u, 2).filter(|s| !s.is_empty());
                            self.short_name = get_str(&u, 3).filter(|s| !s.is_empty());
                            if self.hw_model.is_none() {
                                self.hw_model = get_varint(&u, 5).map(|x| x as u32).filter(|x| *x != 0);
                            }
                        }
                    }
                }
                5 => {
                    for (cn, cv) in Fields::new(b).flatten() {
                        if let Val::Len(c) = cv {
                            match cn {
                                1 => self.device = Some(c.to_vec()),
                                2 => self.position = Some(c.to_vec()),
                                6 => self.lora = Some(c.to_vec()),
                                _ => {}
                            }
                        }
                    }
                }
                10 => {
                    let idx = get_varint(b, 1).unwrap_or(0) as u32;
                    self.channels.insert(idx, b.to_vec());
                }
                13 => {
                    self.firmware = get_str(b, 1).filter(|s| !s.is_empty());
                    if let Some(h) = get_varint(b, 9).filter(|x| *x != 0) {
                        self.hw_model = Some(h as u32);
                    }
                }
                _ => {}
            }
        }
    }

    pub fn firmware(&self) -> Option<String> {
        self.firmware.clone()
    }
    pub fn hardware(&self) -> Option<String> {
        self.hw_model.map(hw_label)
    }

    fn channel_json(&self, idx: u32, raw: &[u8]) -> Option<Value> {
        let role = get_varint(raw, 3).unwrap_or(0);
        if role == 0 {
            return None;
        }
        let settings = get_len(raw, 2).unwrap_or_default();
        Some(json!({
            "index": idx,
            "name": get_str(&settings, 3).unwrap_or_default(),
            "role": if role == 1 { "PRIMARY" } else { "SECONDARY" },
            "psk_b64": b64(&get_len(&settings, 2).unwrap_or_default(), false),
        }))
    }

    fn share_url(&self) -> Option<String> {
        let lora = self.lora.as_ref()?;
        let mut w = Writer::new();
        let mut any = false;
        for raw in self.channels.values() {
            if get_varint(raw, 3).unwrap_or(0) != 0 {
                w.bytes(1, &get_len(raw, 2).unwrap_or_default());
                any = true;
            }
        }
        if !any {
            return None;
        }
        w.bytes(2, lora);
        Some(format!("https://meshtastic.org/e/#{}", b64(&w.finish(), true)))
    }

    /// The `device.config` values (docs/DEVICE-SETTINGS.md). A section the radio did not send is absent.
    pub fn snapshot(&self) -> Value {
        let mut m = Map::new();
        m.insert("network".into(), json!("meshtastic"));
        if let Some(f) = self.firmware() {
            m.insert("firmware".into(), json!(f));
        }
        if let Some(h) = self.hardware() {
            m.insert("hardware".into(), json!(h));
        }
        if self.long_name.is_some() || self.short_name.is_some() {
            let mut o = Map::new();
            if let Some(l) = &self.long_name {
                o.insert("long_name".into(), json!(l));
            }
            if let Some(s) = &self.short_name {
                o.insert("short_name".into(), json!(s));
            }
            m.insert("owner".into(), Value::Object(o));
        }
        if let Some(l) = &self.lora {
            m.insert(
                "lora".into(),
                json!({
                    "region": name_of(REGIONS, get_varint(l, 7).unwrap_or(0) as u32),
                    "modem_preset": name_of(MODEM_PRESETS, get_varint(l, 2).unwrap_or(0) as u32),
                    "hop_limit": get_varint(l, 8).unwrap_or(0),
                    "tx_power": get_varint(l, 10).unwrap_or(0),
                }),
            );
        }
        if let Some(d) = &self.device {
            m.insert("device".into(), json!({"role": name_of(ROLES, get_varint(d, 1).unwrap_or(0) as u32)}));
        }
        if let Some(p) = &self.position {
            m.insert("position".into(), json!({"broadcast_secs": get_varint(p, 1).unwrap_or(0)}));
        }
        let mut chans: Vec<Value> = self.channels.iter().filter_map(|(i, r)| self.channel_json(*i, r)).collect();
        if let Some(u) = self.share_url() {
            for c in chans.iter_mut() {
                c["share_url"] = json!(u);
            }
        }
        m.insert("channels".into(), Value::Array(chans));
        m.insert(
            "options".into(),
            json!({
                "region": REGIONS.iter().map(|(_, v)| *v).collect::<Vec<_>>(),
                "modem_preset": MODEM_PRESETS.iter().map(|(_, v)| *v).collect::<Vec<_>>(),
                "role": ROLES.iter().map(|(_, v)| *v).collect::<Vec<_>>(),
            }),
        );
        Value::Object(m)
    }

    /// Settings this code can write. Anything else is refused by name.
    pub const WRITABLE: &'static [&'static str] = &[
        "owner.long_name",
        "owner.short_name",
        "lora.region",
        "lora.modem_preset",
        "device.role",
        "position.broadcast_secs",
        "channels.<n>.name",
    ];

    /// Turn requested changes into ToRadio payloads (unframed): begin, the sets, commit. Nothing is sent here.
    /// `next_id` supplies packet ids. Errors say what is wrong in plain words; no partial plan is returned.
    pub fn plan_apply(&self, changes: &Map<String, Value>, mut next_id: impl FnMut() -> u32) -> Result<(Vec<Vec<u8>>, Vec<String>), String> {
        let my = self.my_node.ok_or("the radio has not said who it is yet")?;
        if !self.complete {
            return Err("the radio has not finished sending its settings yet".into());
        }
        let mut lora: Vec<Edit> = vec![];
        let mut dev: Vec<Edit> = vec![];
        let mut pos: Vec<Edit> = vec![];
        let (mut long, mut short) = (None::<String>, None::<String>);
        let mut chans: BTreeMap<u32, String> = BTreeMap::new();
        let mut applied = vec![];
        for (path, val) in changes {
            let s = val.as_str();
            match path.as_str() {
                "owner.long_name" | "owner.short_name" => {
                    let t = s.map(str::trim).filter(|t| !t.is_empty()).ok_or(format!("{path}: a name cannot be empty"))?;
                    let (max, field) = if path.ends_with("long_name") { (39, &mut long) } else { (4, &mut short) };
                    if t.len() > max {
                        return Err(format!("{path}: at most {max} bytes"));
                    }
                    *field = Some(t.to_string());
                }
                "lora.region" => {
                    let n = s.and_then(|x| num_of(REGIONS, x)).ok_or(format!("{path}: not a region this firmware knows"))?;
                    if self.lora.is_none() {
                        return Err("the radio has not sent its radio settings yet".into());
                    }
                    lora.push(Edit::Varint(7, u64::from(n)));
                }
                "lora.modem_preset" => {
                    let n = s.and_then(|x| num_of(MODEM_PRESETS, x)).ok_or(format!("{path}: not a modem preset this firmware knows"))?;
                    if self.lora.is_none() {
                        return Err("the radio has not sent its radio settings yet".into());
                    }
                    // a preset only counts when use_preset is on
                    lora.push(Edit::Varint(1, 1));
                    lora.push(Edit::Varint(2, u64::from(n)));
                }
                "device.role" => {
                    let n = s.and_then(|x| num_of(ROLES, x)).ok_or(format!("{path}: not a role this firmware knows"))?;
                    if self.device.is_none() {
                        return Err("the radio has not sent its device settings yet".into());
                    }
                    dev.push(Edit::Varint(1, u64::from(n)));
                }
                "position.broadcast_secs" => {
                    let n = val.as_u64().or_else(|| s.and_then(|x| x.trim().parse().ok())).filter(|n| *n <= 86_400 * 7).ok_or(format!("{path}: a whole number of seconds"))?;
                    if self.position.is_none() {
                        return Err("the radio has not sent its position settings yet".into());
                    }
                    pos.push(Edit::Varint(1, n));
                }
                p if p.starts_with("channels.") && p.ends_with(".name") => {
                    let idx: u32 = p["channels.".len()..p.len() - ".name".len()].parse().map_err(|_| format!("{p}: not a channel number"))?;
                    let name = s.ok_or(format!("{p}: a name"))?.trim();
                    if name.len() > 11 {
                        return Err(format!("{p}: at most 11 bytes"));
                    }
                    if !self.channels.get(&idx).is_some_and(|r| get_varint(r, 3).unwrap_or(0) != 0) {
                        return Err(format!("{p}: that channel is not set up on this radio"));
                    }
                    chans.insert(idx, name.to_string());
                }
                other => return Err(format!("{other}: this setting cannot be written yet (writable: {})", Self::WRITABLE.join(", "))),
            }
            applied.push(path.clone());
        }
        if applied.is_empty() {
            return Err("nothing to change".into());
        }
        let mut out = vec![toradio_admin(my, next_id(), &admin_flag(64))];
        if long.is_some() || short.is_some() {
            let mut u = Writer::new();
            if let Some(l) = &long {
                u.bytes(2, l.as_bytes());
            }
            if let Some(s) = &short {
                u.bytes(3, s.as_bytes());
            }
            let mut a = Writer::new();
            a.bytes(32, &u.finish());
            out.push(toradio_admin(my, next_id(), &a.finish()));
        }
        for (sect, edits, base) in [(1u32, &dev, &self.device), (2, &pos, &self.position), (6, &lora, &self.lora)] {
            if edits.is_empty() {
                continue;
            }
            let cfg = patch(base.as_deref().unwrap_or_default(), edits);
            let mut c = Writer::new();
            c.bytes(sect, &cfg);
            let mut a = Writer::new();
            a.bytes(34, &c.finish());
            out.push(toradio_admin(my, next_id(), &a.finish()));
        }
        for (idx, name) in chans {
            let raw = &self.channels[&idx];
            let new = patch(raw, &[Edit::Nested(2, vec![Edit::Bytes(3, name.into_bytes())])]);
            let mut a = Writer::new();
            a.bytes(33, &new);
            out.push(toradio_admin(my, next_id(), &a.finish()));
        }
        out.push(toradio_admin(my, next_id(), &admin_flag(65)));
        Ok((out, applied))
    }
}

fn admin_flag(field: u32) -> Vec<u8> {
    let mut w = Writer::new();
    w.varint(field, 1);
    w.finish()
}

/// `ToRadio { packet: MeshPacket { to: my node, decoded: { ADMIN_APP, admin }, id, priority: RELIABLE } }`, the shape the
/// reference client sends to its own radio. `from` stays unset: that marks it as coming over the host link.
pub fn toradio_admin(to: u32, id: u32, admin: &[u8]) -> Vec<u8> {
    let mut data = Writer::new();
    data.varint(1, ADMIN_APP);
    data.bytes(2, admin);
    let mut pkt = Writer::new();
    pkt.fixed32(2, to);
    pkt.bytes(4, &data.finish());
    pkt.fixed32(6, id);
    pkt.varint(11, PRIORITY_RELIABLE);
    let mut tr = Writer::new();
    tr.bytes(1, &pkt.finish());
    tr.finish()
}

// ---- helpers for tests: compare writes without the engine's own packet ids ----------------------------------------

/// A ToRadio payload with the MeshPacket id removed.
pub fn without_packet_id(toradio: &[u8]) -> Vec<u8> {
    let Some(pkt) = get_len(toradio, 1) else { return toradio.to_vec() };
    let mut w = Writer::new();
    w.bytes(1, &patch_remove(&pkt, 6));
    w.finish()
}

fn patch_remove(raw: &[u8], field: u32) -> Vec<u8> {
    let mut w = Writer::new();
    for (n, v) in Fields::new(raw).flatten() {
        if n == field {
            continue;
        }
        match v {
            Val::Varint(x) => w.varint(n, x),
            Val::Fixed32(x) => w.fixed32(n, x),
            Val::Len(b) => w.bytes(n, b),
            Val::Fixed64(_) => {}
        }
    }
    w.finish()
}

/// A written byte stream (framed ToRadio messages) with every packet id removed, re-framed.
pub fn strip_ids_from_stream(stream: &[u8]) -> Vec<u8> {
    let mut d = crate::meshtastic::StreamDecoder::new();
    let mut out = vec![];
    for p in d.push(stream) {
        out.extend(crate::meshtastic::frame(&without_packet_id(&p)));
    }
    out
}
