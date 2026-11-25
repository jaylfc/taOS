// SPDX-License-Identifier: AGPL-3.0-or-later
//! Local API: newline-delimited JSON over a unix socket. See docs/API.md.
//!
//! Request:  `{"id": 1, "method": "messages", "principal": "owner", "params": {...}}`
//! Response: `{"id": 1, "ok": true, "result": ...}` or
//!           `{"id": 1, "ok": false, "error": {"code": "...", "message": "..."}}`
//! `subscribe` keeps the connection open and streams one `{"event":"record","record":{...}}` per line.
//!
//! The socket is created with mode 0600 in the owner's runtime directory.
//! The `principal` is asserted by the caller: the taOS service bus (which
//! authenticates agents and people) is the party that sets it for agents.
//! Owner-only methods refuse any other principal.

use crate::discovery;
use crate::engine::{Engine, EngineError, Filter, Session};
use crate::grants::{Grant, OWNER};
use crate::link::{Link, SerialLink, TcpLink};
use serde::Deserialize;
use serde_json::{json, Value};
use std::io::{BufRead, BufReader, Write};
use std::os::unix::fs::PermissionsExt;
use std::os::unix::net::{UnixListener, UnixStream};
use std::path::Path;
use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::Arc;
use std::time::Duration;

#[derive(Deserialize)]
struct Request {
    #[serde(default)]
    id: Value,
    method: String,
    #[serde(default = "owner_default")]
    principal: String,
    #[serde(default)]
    params: Value,
}

fn owner_default() -> String {
    OWNER.to_string()
}

pub fn err(id: &Value, code: &str, msg: &str) -> Value {
    json!({"id": id, "ok": false, "error": {"code": code, "message": msg}})
}

fn eng_err(id: &Value, e: &EngineError) -> Value {
    err(id, e.code(), &e.to_string())
}

fn s<'a>(p: &'a Value, k: &str) -> Option<&'a str> {
    p.get(k).and_then(|v| v.as_str())
}

fn need<'a>(p: &'a Value, k: &str) -> Result<&'a str, String> {
    s(p, k).ok_or_else(|| format!("missing parameter {k}"))
}

pub struct Daemon {
    pub engine: Arc<Engine>,
    pub receive_only: bool,
    pub stop: Arc<AtomicBool>,
}

impl Daemon {
    /// Open a radio on a link and run its session on a thread.
    pub fn attach(&self, link: Box<dyn Link>, network: &str, prefeed: Vec<u8>) -> Result<String, EngineError> {
        let mut sess = Session::new(self.engine.clone(), link, network)?;
        sess.prefeed(prefeed);
        sess.transmit_enabled = !self.receive_only;
        let name = sess.radio_id().to_string();
        let stop = self.stop.clone();
        std::thread::spawn(move || {
            while !stop.load(Ordering::Relaxed) {
                match sess.pump() {
                    Ok(_) => {}
                    Err(EngineError::NoRadio(_)) => return, // link gone; state shows it
                    Err(_) => std::thread::sleep(Duration::from_millis(500)), // store failure: keep retrying, error is in health
                }
            }
        });
        Ok(name)
    }

    pub fn connect_spec(&self, spec: &str, network: Option<&str>) -> Result<Value, String> {
        let mut link: Box<dyn Link> = if let Some(a) = spec.strip_prefix("tcp:") {
            Box::new(TcpLink::connect(a).map_err(|e| e.to_string())?)
        } else {
            Box::new(SerialLink::open(spec).map_err(|e| format!("{spec}: {e}"))?)
        };
        let (net, pre) = match network {
            Some(n) => (n.to_string(), vec![]),
            None => {
                let p = discovery::probe(link.as_mut(), Duration::from_millis(2500));
                (p.network.clone().ok_or_else(|| format!("{spec}: {}", p.detail))?, p.received)
            }
        };
        // the probe's bytes go into the session first: nothing the radio
        // already handed over is dropped
        let name = self.attach(link, &net, pre).map_err(|e| e.to_string())?;
        Ok(json!({"radio": name, "network": net}))
    }

    pub fn handle(&self, line: &str) -> Value {
        let req: Request = match serde_json::from_str(line) {
            Ok(r) => r,
            Err(e) => return err(&Value::Null, "bad_request", &e.to_string()),
        };
        let id = req.id.clone();
        let p = &req.params;
        let who = req.principal.as_str();
        let owner_only = |id: &Value| -> Option<Value> { (who != OWNER).then(|| err(id, "forbidden", "owner only")) };
        macro_rules! try_p {
            ($e:expr) => {
                match $e {
                    Ok(v) => v,
                    Err(m) => return err(&id, "invalid", &m),
                }
            };
        }
        macro_rules! ok {
            ($v:expr) => {
                json!({"id": id, "ok": true, "result": $v})
            };
        }
        macro_rules! eng {
            ($e:expr) => {
                match $e {
                    Ok(v) => v,
                    Err(e) => return eng_err(&id, &e),
                }
            };
        }
        match req.method.as_str() {
            "health" => {
                // what the app needs to decide whether it may offer sending and configuration writes
                let mut h = self.engine.health();
                h["receive_only"] = json!(self.receive_only);
                h["device_config"] = json!(false);
                h["device_config_write"] = json!(false);
                ok!(h)
            }
            "radios" => ok!(self.engine.radios()),
            "discover" => {
                if let Some(e) = owner_only(&id) {
                    return e;
                }
                let serial = discovery::scan_serial(Path::new("/sys"), Path::new("/dev"));
                let ble = discovery::scan_ble(&discovery::BluezBusctl);
                ok!(json!({"serial": serial, "ble": ble}))
            }
            "connect" => {
                if let Some(e) = owner_only(&id) {
                    return e;
                }
                let spec = try_p!(need(p, "device"));
                match self.connect_spec(spec, s(p, "network")) {
                    Ok(v) => ok!(v),
                    Err(m) => err(&id, "connect_failed", &m),
                }
            }
            "app.radios" => ok!(eng!(crate::appview::radios(&self.engine))),
            "app.identities" => ok!(eng!(crate::appview::identities(&self.engine))),
            "app.messages" => ok!(eng!(crate::appview::messages(&self.engine, s(p, "thread")))),
            "app.send" => {
                let thread = try_p!(need(p, "thread"));
                let text = try_p!(need(p, "text"));
                ok!(crate::appview::send(&self.engine, who, thread, text))
            }
            "messages" => {
                let f: Filter = try_p!(serde_json::from_value(p.get("filter").cloned().unwrap_or(json!({}))).map_err(|e| e.to_string()));
                // an owner may read as a person (the app does this on a
                // person's behalf); anyone else reads as themselves
                let viewer = if who == OWNER { s(p, "as").unwrap_or(OWNER) } else { who };
                ok!(eng!(self.engine.messages(viewer, &f)))
            }
            "last_positions" => {
                let viewer = if who == OWNER { s(p, "as").unwrap_or(OWNER) } else { who };
                ok!(eng!(self.engine.last_positions(viewer)))
            }
            "send" => {
                let text = try_p!(need(p, "text"));
                let network = try_p!(need(p, "network"));
                let ch = p.get("channel").and_then(|c| c.as_u64()).map(|c| c as u32);
                ok!(eng!(self.engine.send_text(who, network, s(p, "radio"), s(p, "to"), ch, text)))
            }
            "identities.list" => {
                if let Some(e) = owner_only(&id) {
                    return e;
                }
                ok!(self.engine.identities())
            }
            "identities.assign" => {
                let node = try_p!(need(p, "node"));
                let ident = s(p, "identity");
                let name = s(p, "name");
                eng!(self.engine.with_ids(who, |i| i.assign(node, ident, name, OWNER)));
                ok!(json!({"node": node, "identity": ident}))
            }
            "contacts.create" => {
                let name = try_p!(need(p, "name"));
                let cid = eng!(self.engine.with_ids(who, |i| i.create_contact(name, s(p, "notes"), OWNER)));
                ok!(json!({"contact": cid}))
            }
            "contacts.update" => {
                let cid = try_p!(need(p, "contact"));
                eng!(self.engine.with_ids(who, |i| i.update_contact(cid, s(p, "name"), s(p, "notes"), OWNER)));
                ok!(json!({"contact": cid}))
            }
            "contacts.told" => {
                let cid = try_p!(need(p, "contact"));
                let told = p.get("told").and_then(|t| t.as_bool()).unwrap_or(true);
                eng!(self.engine.with_ids(who, |i| i.set_told(cid, told, s(p, "date"), OWNER)));
                ok!(json!({"contact": cid, "told": told}))
            }
            "link" => {
                let cid = try_p!(need(p, "contact"));
                let acct = try_p!(need(p, "account"));
                eng!(self.engine.with_ids(who, |i| i.link(cid, acct, OWNER)));
                ok!(json!({"contact": cid, "account": acct}))
            }
            "locations.hide" => {
                // the linked account hides its own location history
                let cid = try_p!(need(p, "contact"));
                eng!(self.engine.hide_locations(who, cid));
                ok!(json!({"contact": cid}))
            }
            "grants.list" => {
                if let Some(e) = owner_only(&id) {
                    return e;
                }
                ok!(self.engine.grants())
            }
            "grants.add" => {
                let g: Result<Grant, _> = serde_json::from_value(json!({
                    "id": "", "kind": s(p, "kind").unwrap_or("send"), "agent": s(p, "agent").unwrap_or(""),
                    "network": s(p, "network").unwrap_or(""), "channel": p.get("channel").cloned().unwrap_or(Value::Null),
                    "max_msgs_per_hour": p.get("max_msgs_per_hour").cloned().unwrap_or(Value::Null),
                    "max_bytes_per_hour": p.get("max_bytes_per_hour").cloned().unwrap_or(Value::Null),
                    "expires_ms": p.get("expires_ms").cloned().unwrap_or(Value::Null),
                    "created_ms": 0, "created_by": ""
                }));
                let g = try_p!(g.map_err(|e| e.to_string()));
                ok!(eng!(self.engine.grant_add(who, g)))
            }
            "grants.revoke" => {
                let gid = try_p!(need(p, "id"));
                eng!(self.engine.grant_revoke(who, gid));
                ok!(json!({"revoked": gid}))
            }
            other => err(&id, "unknown_method", &format!("no method {other}")),
        }
    }

    fn serve_conn(self: &Arc<Self>, stream: UnixStream) {
        let Ok(rd) = stream.try_clone() else { return };
        let mut w = stream;
        for line in BufReader::new(rd).lines() {
            let Ok(line) = line else { return };
            if line.trim().is_empty() {
                continue;
            }
            if let Ok(v) = serde_json::from_str::<Value>(&line) {
                if v.get("method").and_then(|m| m.as_str()) == Some("subscribe") {
                    let who = v.get("principal").and_then(|x| x.as_str()).unwrap_or(OWNER).to_string();
                    if who != OWNER {
                        let _ = writeln!(w, "{}", err(&v["id"], "forbidden", "owner only"));
                        continue;
                    }
                    let rx = self.engine.subscribe();
                    let _ = writeln!(w, "{}", json!({"id": v["id"], "ok": true, "result": {"subscribed": true}}));
                    while let Ok(rec) = rx.recv() {
                        if writeln!(w, "{}", json!({"event": "record", "record": rec})).is_err() {
                            return;
                        }
                    }
                    return;
                }
            }
            let resp = self.handle(&line);
            if writeln!(w, "{resp}").is_err() {
                return;
            }
        }
    }

    pub fn serve(self: &Arc<Self>, socket: &Path) -> std::io::Result<()> {
        if socket.exists() {
            // only remove a socket nobody answers on
            if UnixStream::connect(socket).is_ok() {
                return Err(std::io::Error::new(std::io::ErrorKind::AddrInUse, "another taos-mesh is serving this socket"));
            }
            std::fs::remove_file(socket)?;
        }
        let l = UnixListener::bind(socket)?;
        std::fs::set_permissions(socket, std::fs::Permissions::from_mode(0o600))?;
        for c in l.incoming() {
            if self.stop.load(Ordering::Relaxed) {
                break;
            }
            if let Ok(c) = c {
                let d = self.clone();
                std::thread::spawn(move || d.serve_conn(c));
            }
        }
        Ok(())
    }
}

/// Blocking client: one request, one response.
pub fn call(socket: &Path, method: &str, principal: &str, params: Value) -> Result<Value, String> {
    let mut s = UnixStream::connect(socket).map_err(|e| format!("cannot reach taos-mesh at {}: {e}", socket.display()))?;
    let req = json!({"id": 1, "method": method, "principal": principal, "params": params});
    writeln!(s, "{req}").map_err(|e| e.to_string())?;
    let mut line = String::new();
    BufReader::new(s).read_line(&mut line).map_err(|e| e.to_string())?;
    serde_json::from_str(&line).map_err(|e| format!("bad reply: {e}"))
}
