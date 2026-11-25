// SPDX-License-Identifier: AGPL-3.0-or-later
//! The thin view the Survival Studio app calls. Field names and meanings
//! match its `MeshBackend` interface (radios, identities, messages, send),
//! so the C++ side maps one-to-one. Values the engine does not know are `-1`
//! or empty, never a guess.
//!
//! Threads: a direct conversation is the other party's node id; a channel is
//! `ch:<network>:<index>`.

use crate::engine::{Engine, EngineError, Filter};
use crate::model::*;
use serde_json::{json, Value};
use std::collections::BTreeMap;

pub fn thread_of(r: &Record) -> Option<String> {
    if r.kind != "text" {
        return None;
    }
    if r.broadcast {
        return Some(format!("ch:{}:{}", r.network, r.channel.unwrap_or(0)));
    }
    let peer = if r.dir == "out" {
        r.to.clone()
    } else {
        r.from.clone()
    };
    peer
}

pub fn radios(e: &Engine) -> Result<Value, EngineError> {
    let recs = e.messages(
        "owner",
        &Filter {
            kind: Some("telemetry".into()),
            ..Default::default()
        },
    )?;
    let own_info = e.messages(
        "owner",
        &Filter {
            kind: Some("nodeinfo".into()),
            ..Default::default()
        },
    )?;
    let mut out = vec![];
    for r in e.radios() {
        // battery: the radio's own newest device telemetry
        let battery = recs
            .iter()
            .rev()
            .find(|x| {
                x.from.as_deref() == Some(&r.id)
                    && x.detail
                        .as_ref()
                        .is_some_and(|d| d["telemetry"] == "device")
            })
            .and_then(|x| {
                x.detail
                    .as_ref()
                    .and_then(|d| d["values"]["battery_level"].as_u64())
            })
            .map(|b| b as i64)
            .unwrap_or(-1);
        let name = own_info
            .iter()
            .rev()
            .find(|x| x.from.as_deref() == Some(&r.id))
            .and_then(|x| {
                x.detail
                    .as_ref()
                    .and_then(|d| d["long_name"].as_str().map(String::from))
            })
            .filter(|n| !n.is_empty())
            .or_else(|| r.identity.as_ref().map(|i| i.name.clone()))
            .unwrap_or_else(|| r.id.clone());
        // the app knows connected | connecting | offline; a link that is
        // being retried is "connecting"
        let state = match r.state.as_str() {
            "connected" => "connected",
            "offline" => "offline",
            _ => "connecting",
        };
        out.push(json!({
            "id": r.id, "name": name, "transport": r.network, "link": r.link_kind, "state": state,
            "firmware": "", "batteryPercent": battery,
        }));
    }
    Ok(Value::Array(out))
}

pub fn identities(e: &Engine) -> Result<Value, EngineError> {
    let recs = e.messages("owner", &Filter::default())?;
    #[derive(Default)]
    struct N {
        callsign: String,
        owner: String,
        lat: f64,
        lon: f64,
        has_pos: bool,
        heard: u64,
        is_self: bool,
    }
    let mut nodes: BTreeMap<String, N> = BTreeMap::new();
    for r in &recs {
        if r.dir == "in" || r.dir == "out" {
            nodes.entry(r.radio.clone()).or_default().is_self = true;
        }
        if r.dir != "in" {
            continue;
        }
        let Some(f) = &r.from else { continue };
        let n = nodes.entry(f.clone()).or_default();
        n.heard = n.heard.max(r.host_ts_ms);
        if let Some(d) = &r.detail {
            if let Some(l) = d["long_name"].as_str().filter(|s| !s.is_empty()) {
                n.callsign = l.to_string();
            }
        }
        if let Some(p) = &r.position {
            if p.has_fix() && p.source.as_deref() != Some("last_known") {
                n.lat = p.lat.unwrap();
                n.lon = p.lon.unwrap();
                n.has_pos = true;
            }
        }
        if let Some(s) = &r.sender {
            n.callsign = s.name.clone();
            // a contact is not a taOS identity: owner stays empty
            if !s.identity.starts_with("contact:") {
                n.owner = s.identity.clone();
            }
        }
    }
    let out: Vec<Value> = nodes
        .into_iter()
        .map(|(id, n)| {
            json!({"id": id, "callsign": n.callsign, "owner": n.owner, "isSelf": n.is_self, "hasPosition": n.has_pos,
                   "lon": n.lon, "lat": n.lat, "lastHeardMs": n.heard})
        })
        .collect();
    Ok(Value::Array(out))
}

pub fn messages(e: &Engine, thread: Option<&str>) -> Result<Value, EngineError> {
    let recs = e.messages(
        "owner",
        &Filter {
            kind: Some("text".into()),
            ..Default::default()
        },
    )?;
    let out: Vec<Value> = recs
        .iter()
        .filter(|r| r.dir == "in" || r.dir == "out")
        .filter_map(|r| {
            let t = thread_of(r)?;
            if thread.is_some_and(|w| !w.is_empty() && w != t) {
                return None;
            }
            Some(json!({
                "id": r.id, "threadId": t, "fromId": r.from, "toId": r.to, "text": r.text, "timeMs": r.host_ts_ms,
                "outgoing": r.dir == "out", "delivery": r.delivery.clone().unwrap_or_default(),
                "trust": r.trust.clone().unwrap_or_default(),
            }))
        })
        .collect();
    Ok(Value::Array(out))
}

pub fn send(e: &Engine, principal: &str, thread: &str, text: &str) -> Value {
    let (network, to, channel): (String, Option<String>, Option<u32>) = if let Some(rest) =
        thread.strip_prefix("ch:")
    {
        let mut it = rest.splitn(2, ':');
        let (Some(net), Some(ch)) = (it.next(), it.next().and_then(|c| c.parse().ok())) else {
            return json!({"accepted": false, "messageId": "", "error": "bad channel thread id"});
        };
        (net.to_string(), None, Some(ch))
    } else {
        let net = if thread.starts_with('!') {
            NETWORK_MESHTASTIC
        } else {
            NETWORK_MESHCORE
        };
        (net.to_string(), Some(thread.to_string()), Some(0))
    };
    match e.send_text(principal, &network, None, to.as_deref(), channel, text) {
        Ok(r) => json!({"accepted": true, "messageId": r.id, "error": ""}),
        Err(err) => json!({"accepted": false, "messageId": "", "error": err.to_string()}),
    }
}
