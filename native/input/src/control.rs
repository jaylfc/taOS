// SPDX-License-Identifier: AGPL-3.0-or-later
//! The local control socket: the CLI and the taOS agent talk to the running
//! engine here. It is a unix socket in the person's own runtime directory
//! (mode 0600 inside a 0700 directory), one JSON object per line each way.
//! Pairing codes can only be minted here, never over the network.
//!
//! Requests:
//!   {"op":"status"}
//!   {"op":"pair"}                      -> a fresh pairing code
//!   {"op":"devices"}
//!   {"op":"revoke","id":"<id>"}  or  {"op":"revoke","all":true}
//!   {"op":"inject","event":{...}}      -> the same events a phone sends
//!   {"op":"stop"}
//! Every reply has "ok": true/false and, on failure, "error".

use crate::engine::Engine;
use crate::proto::{ClientMsg, Session};
use serde_json::{json, Value};
use std::io::{BufRead, BufReader, Write};
use std::os::unix::fs::{DirBuilderExt, PermissionsExt};
use std::os::unix::net::{UnixListener, UnixStream};
use std::path::Path;
use std::sync::Arc;
use std::time::Duration;

pub fn bind(path: &Path) -> Result<UnixListener, String> {
    if let Some(dir) = path.parent() {
        std::fs::DirBuilder::new()
            .recursive(true)
            .mode(0o700)
            .create(dir)
            .map_err(|e| format!("{}: {e}", dir.display()))?;
        std::fs::set_permissions(dir, std::fs::Permissions::from_mode(0o700))
            .map_err(|e| e.to_string())?;
    }
    // A socket left by a dead engine is replaced; one that still answers
    // means an engine is already running.
    if path.exists() {
        if UnixStream::connect(path).is_ok() {
            return Err(format!("an engine is already running ({})", path.display()));
        }
        std::fs::remove_file(path).map_err(|e| format!("{}: {e}", path.display()))?;
    }
    let l = UnixListener::bind(path).map_err(|e| format!("{}: {e}", path.display()))?;
    std::fs::set_permissions(path, std::fs::Permissions::from_mode(0o600))
        .map_err(|e| e.to_string())?;
    Ok(l)
}

pub fn serve(engine: Arc<Engine>, listener: UnixListener) {
    for conn in listener.incoming() {
        if engine.stopping() {
            break;
        }
        let Ok(s) = conn else { continue };
        let e = engine.clone();
        std::thread::spawn(move || client(&e, s));
    }
}

fn client(engine: &Arc<Engine>, s: UnixStream) {
    let _ = s.set_read_timeout(Some(Duration::from_secs(30)));
    let Ok(mut w) = s.try_clone() else { return };
    let mut session = Session::new();
    for line in BufReader::new(s).lines() {
        let Ok(line) = line else { return };
        if line.len() > 65536 {
            return;
        }
        let reply = match serde_json::from_str::<Value>(&line) {
            Ok(v) => handle(engine, &mut session, &v),
            Err(e) => json!({"ok": false, "error": format!("not JSON: {e}")}),
        };
        if writeln!(w, "{reply}").is_err() {
            return;
        }
        if engine.stopping() {
            return;
        }
    }
}

fn err(msg: impl std::fmt::Display) -> Value {
    json!({"ok": false, "error": msg.to_string()})
}

pub fn handle(engine: &Arc<Engine>, session: &mut Session, req: &Value) -> Value {
    match req.get("op").and_then(|o| o.as_str()).unwrap_or("") {
        "status" => {
            let connected = engine.connected_device();
            let devices: Vec<Value> = engine
                .auth
                .devices()
                .iter()
                .map(|d| {
                    json!({"id": d.id, "name": d.name, "created": d.created, "last_seen": d.last_seen,
                           "connected": connected.as_deref() == Some(d.id.as_str())})
                })
                .collect();
            json!({"ok": true, "engine": "taos-input", "version": env!("CARGO_PKG_VERSION"), "url": engine.url,
                   "compositor": engine.injector.compositor_up(), "uptime_ms": engine.now_ms(), "devices": devices})
        }
        "pair" => match engine.auth.new_code() {
            Ok((code, ttl)) => {
                json!({"ok": true, "code": code, "expires_in": ttl.as_secs(), "url": engine.url})
            }
            Err(e) => err(e),
        },
        "devices" => {
            let v: Vec<Value> = engine
                .auth
                .devices()
                .iter()
                .map(|d| json!({"id": d.id, "name": d.name, "created": d.created, "last_seen": d.last_seen}))
                .collect();
            json!({"ok": true, "devices": v})
        }
        "revoke" => {
            let all = req.get("all").and_then(|a| a.as_bool()).unwrap_or(false);
            let id = req.get("id").and_then(|i| i.as_str());
            if !all && id.is_none() {
                return err("revoke needs an id or all:true");
            }
            if all {
                for d in engine.auth.devices() {
                    engine.kick_device(&d.id);
                }
            } else if let Some(i) = id {
                engine.kick_device(i);
            }
            match engine.auth.revoke(if all { None } else { id }) {
                Ok(n) => json!({"ok": true, "removed": n}),
                Err(e) => err(e),
            }
        }
        "inject" => match req
            .get("event")
            .map(|e| serde_json::from_value::<ClientMsg>(e.clone()))
        {
            Some(Ok(msg)) => {
                let fx = session.on_message(msg, engine.now_ms());
                for c in fx.cmds {
                    engine.injector.send(c);
                }
                if let Some(d) = fx.workspace {
                    engine.workspace(d);
                }
                json!({"ok": true, "compositor": engine.injector.compositor_up()})
            }
            Some(Err(e)) => err(format!("bad event: {e}")),
            None => err("inject needs an event"),
        },
        "stop" => {
            engine.request_stop();
            json!({"ok": true})
        }
        other => err(format!("unknown op: {other}")),
    }
}

/// Client side, for the CLI: send one request, return the reply line.
pub fn request(path: &Path, req: &Value) -> Result<Value, String> {
    let mut s =
        UnixStream::connect(path).map_err(|e| format!("no engine at {}: {e}", path.display()))?;
    s.set_read_timeout(Some(Duration::from_secs(10))).ok();
    writeln!(s, "{req}").map_err(|e| e.to_string())?;
    let mut line = String::new();
    BufReader::new(s)
        .read_line(&mut line)
        .map_err(|e| e.to_string())?;
    serde_json::from_str(&line).map_err(|e| format!("bad reply: {e}"))
}
