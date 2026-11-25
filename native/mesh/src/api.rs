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
use crate::link::{Connector, Link, SerialConnector, TcpConnector};
use crate::supervisor;
use serde::Deserialize;
use serde_json::{json, Value};
use std::io::{BufRead, BufReader, Write};
use std::os::unix::fs::PermissionsExt;
use std::os::unix::net::{UnixListener, UnixStream};
use std::path::Path;
use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::{Arc, Mutex};
use std::time::Duration;

/// Where the radios the owner asked the engine to open are remembered, so they are opened again after a restart.
/// Set by the daemon to `<data-dir>/radios.json`; unset (nothing is remembered) in tests that do not care.
static RADIOS_FILE: Mutex<Option<std::path::PathBuf>> = Mutex::new(None);

pub fn set_radios_file(p: Option<std::path::PathBuf>) {
    *RADIOS_FILE.lock().unwrap() = p;
}

/// The remembered radios: `[{"device": "/dev/ttyACM0", "network": null}]`.
pub fn saved_radios() -> Vec<Value> {
    let Some(p) = RADIOS_FILE.lock().unwrap().clone() else {
        return vec![];
    };
    std::fs::read_to_string(p)
        .ok()
        .and_then(|s| serde_json::from_str::<Value>(&s).ok())
        .and_then(|v| v.get("devices").and_then(|d| d.as_array().cloned()))
        .unwrap_or_default()
}

fn write_saved(list: &[Value]) -> Result<(), String> {
    let Some(p) = RADIOS_FILE.lock().unwrap().clone() else {
        return Ok(());
    };
    let tmp = p.with_extension("json.tmp");
    std::fs::write(
        &tmp,
        serde_json::to_string_pretty(&json!({"devices": list})).unwrap(),
    )
    .map_err(|e| format!("cannot save the radio list: {e}"))?;
    std::fs::rename(&tmp, &p).map_err(|e| format!("cannot save the radio list: {e}"))
}

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

/// (stop flag, alive flag) of one supervised radio.
type Supervision = (Arc<AtomicBool>, Arc<AtomicBool>);

pub struct Daemon {
    pub engine: Arc<Engine>,
    pub receive_only: bool,
    pub stop: Arc<AtomicBool>,
    /// The uid allowed to connect (the owner's).
    pub owner_uid: u32,
    /// Verifies capability tokens for non-owner principals; None refuses them all.
    pub verifier: Option<crate::auth::TokenVerifier>,
    /// One stop flag per supervised radio, by the connector's label: `radios.release` sets it.
    releases: std::sync::Mutex<std::collections::HashMap<String, Supervision>>,
    /// Ports a `connect` is working on right now (the probe takes seconds): a release waits for them.
    connecting: std::sync::Mutex<std::collections::HashSet<String>>,
}

impl Daemon {
    /// A daemon for the current user: connections must come from this uid
    /// and no non-owner principal is accepted until a verifier is set.
    pub fn new(engine: Arc<Engine>, receive_only: bool) -> Self {
        Daemon {
            engine,
            receive_only,
            stop: Arc::new(AtomicBool::new(false)),
            owner_uid: nix::unistd::geteuid().as_raw(),
            verifier: None,
            releases: Default::default(),
            connecting: Default::default(),
        }
    }

    /// Open a radio on a link and run its session on a thread.
    pub fn attach(
        &self,
        link: Box<dyn Link>,
        network: &str,
        prefeed: Vec<u8>,
    ) -> Result<String, EngineError> {
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

    /// Run a radio under the supervisor: it reconnects with back-off when
    /// the link drops. `first` is an already open link (the probe's) with
    /// the bytes it has received.
    pub fn attach_supervised(
        &self,
        connector: Arc<dyn Connector>,
        network: &str,
        first: Option<(Box<dyn Link>, Vec<u8>)>,
    ) -> String {
        let label = connector.describe();
        // its own stop flag, so one radio can be released without stopping the daemon; the daemon's flag is
        // copied into it by a small watcher
        let own = Arc::new(AtomicBool::new(false));
        {
            let (global, own2) = (self.stop.clone(), own.clone());
            std::thread::spawn(move || {
                while !own2.load(Ordering::Relaxed) {
                    if global.load(Ordering::Relaxed) {
                        own2.store(true, Ordering::Relaxed);
                    }
                    std::thread::sleep(Duration::from_millis(200));
                }
            });
        }
        let alive = Arc::new(AtomicBool::new(true));
        self.releases
            .lock()
            .unwrap_or_else(|p| p.into_inner())
            .insert(label.clone(), (own.clone(), alive.clone()));
        let sup = supervisor::Supervised {
            engine: self.engine.clone(),
            connector,
            network: network.to_string(),
            transmit: !self.receive_only,
            stop: own,
            backoff: supervisor::Backoff::standard(),
            first,
        };
        let stop = sup.stop.clone();
        std::thread::spawn(move || {
            supervisor::run(sup, &|d| supervisor::sleep_unless_stopped(&stop, d));
            alive.store(false, Ordering::SeqCst); // the link is dropped: the port is closed
        });
        label
    }

    /// Close a radio's link and stop reconnecting it, so another program (the firmware flasher) can have the port.
    /// The saved entry goes too: it is not opened again at the next start.
    pub fn release_spec(&self, spec: &str) -> Result<Value, String> {
        // a connect that is probing this port right now would open it after we return: wait for it to end first
        let end = std::time::Instant::now() + Duration::from_secs(15);
        while self
            .connecting
            .lock()
            .unwrap_or_else(|p| p.into_inner())
            .contains(spec)
        {
            if std::time::Instant::now() > end {
                return Err(format!("{spec}: a connect is still in progress, try again"));
            }
            std::thread::sleep(Duration::from_millis(50));
        }
        let entry = self
            .releases
            .lock()
            .unwrap_or_else(|p| p.into_inner())
            .remove(spec);
        let was_open = entry.is_some();
        if let Some((stop, _)) = &entry {
            stop.store(true, Ordering::Relaxed);
        }
        let list: Vec<Value> = saved_radios()
            .into_iter()
            .filter(|d| d["device"] != spec)
            .collect();
        write_saved(&list)?;
        if let Some((_, alive)) = &entry {
            // the supervisor thread ends only after its session (and so the port) is dropped
            let end = std::time::Instant::now() + Duration::from_secs(10);
            while alive.load(Ordering::SeqCst) {
                if std::time::Instant::now() > end {
                    return Err(format!("{spec}: the radio did not close in time"));
                }
                std::thread::sleep(Duration::from_millis(50));
            }
        }
        let gone = self.engine.forget_radio(spec);
        Ok(json!({"device": spec, "released": true, "was_open": was_open, "entries_removed": gone}))
    }

    /// `spec` is a serial path, `tcp:host:port` or `ble:ADDRESS`.
    pub fn connect_spec(&self, spec: &str, network: Option<&str>) -> Result<Value, String> {
        struct Busy<'a>(&'a Daemon, String);
        impl Drop for Busy<'_> {
            fn drop(&mut self) {
                self.0
                    .connecting
                    .lock()
                    .unwrap_or_else(|p| p.into_inner())
                    .remove(&self.1);
            }
        }
        self.connecting
            .lock()
            .unwrap_or_else(|p| p.into_inner())
            .insert(spec.to_string());
        let _busy = Busy(self, spec.to_string());
        self.connect_spec_inner(spec, network)
    }

    fn connect_spec_inner(&self, spec: &str, network: Option<&str>) -> Result<Value, String> {
        if let Some(addr) = spec.strip_prefix("ble:") {
            let net = match network {
                Some(n) => n.to_string(),
                None => match discovery::scan_ble(&discovery::BluezBusctl) {
                    discovery::BleScan::Devices { devices } => devices
                        .iter()
                        .find(|d| d.address.eq_ignore_ascii_case(addr))
                        .map(|d| d.network.clone())
                        .ok_or_else(|| {
                            format!("{spec}: not seen advertising a mesh service; name the network")
                        })?,
                    discovery::BleScan::NoAdapter { why } => return Err(format!("{spec}: {why}")),
                },
            };
            let c = Arc::new(crate::ble::BleConnector {
                address: addr.to_string(),
                network: net.clone(),
            });
            let name = self.attach_supervised(c, &net, None);
            return Ok(json!({"radio": name, "network": net}));
        }
        // already open and still supervised: opening it again would open the port twice and replace the release flag
        let alive = self
            .releases
            .lock()
            .unwrap_or_else(|p| p.into_inner())
            .get(spec)
            .is_some_and(|(stop, _)| !stop.load(Ordering::Relaxed));
        if alive {
            let net = self
                .engine
                .radios()
                .iter()
                .find(|r| r.link == spec)
                .map(|r| r.network.clone());
            return Ok(json!({"radio": spec, "network": net, "already_open": true}));
        }
        let connector: Arc<dyn Connector> = if let Some(a) = spec.strip_prefix("tcp:") {
            Arc::new(TcpConnector(a.to_string()))
        } else {
            Arc::new(SerialConnector(spec.to_string()))
        };
        let mut link = connector.connect().map_err(|e| format!("{spec}: {e}"))?;
        let (net, pre) = match network {
            Some(n) => (n.to_string(), vec![]),
            None => {
                let p = discovery::probe(link.as_mut(), Duration::from_millis(2500));
                (
                    p.network
                        .clone()
                        .ok_or_else(|| format!("{spec}: {}", p.detail))?,
                    p.received,
                )
            }
        };
        // the probe's bytes go into the first session: nothing the radio
        // already handed over is dropped
        let name = self.attach_supervised(connector, &net, Some((link, pre)));
        Ok(json!({"radio": name, "network": net}))
    }

    /// Handle a request whose principal has already been authenticated
    /// (the connection layer does that: see `auth`).
    pub fn handle_trusted(&self, line: &str) -> Value {
        let req: Request = match serde_json::from_str(line) {
            Ok(r) => r,
            Err(e) => return err(&Value::Null, "bad_request", &e.to_string()),
        };
        let id = req.id.clone();
        let p = &req.params;
        let who = req.principal.as_str();
        let owner_only = |id: &Value| -> Option<Value> {
            (who != OWNER).then(|| err(id, "forbidden", "owner only"))
        };
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
                h["device_config"] = json!(true);
                h["device_config_write"] = json!(!self.receive_only);
                h["radios_connect"] = json!(true);
                ok!(h)
            }
            "radios" => ok!(if self.engine.may_see_radios(who) {
                self.engine.radios()
            } else {
                vec![]
            }),
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
            "radios.connect" => {
                if let Some(e) = owner_only(&id) {
                    return e;
                }
                let spec = try_p!(need(p, "device"));
                if spec.starts_with("ble:") {
                    return err(
                        &id,
                        "connect_failed",
                        "Bluetooth radios cannot be opened by this engine yet",
                    );
                }
                match self.connect_spec(spec, s(p, "network")) {
                    Ok(v) => {
                        // remembered only after it opened: a port that failed is not retried at every start
                        let mut list = saved_radios();
                        if !list.iter().any(|d| d["device"] == spec) {
                            list.push(json!({"device": spec, "network": s(p, "network")}));
                            if let Err(m) = write_saved(&list) {
                                return err(&id, "store_error", &m);
                            }
                        }
                        ok!(v)
                    }
                    Err(m) => err(&id, "connect_failed", &m),
                }
            }
            "radios.disconnect" => {
                if let Some(e) = owner_only(&id) {
                    return e;
                }
                let spec = try_p!(need(p, "device"));
                let list: Vec<Value> = saved_radios()
                    .into_iter()
                    .filter(|d| d["device"] != spec)
                    .collect();
                if let Err(m) = write_saved(&list) {
                    return err(&id, "store_error", &m);
                }
                // forgetting stops it being opened again; the open session ends when the engine restarts
                ok!(
                    json!({"device": spec, "forgotten": true, "closes": "at the next engine start"})
                )
            }
            "radios.release" => {
                if let Some(e) = owner_only(&id) {
                    return e;
                }
                let spec = try_p!(need(p, "device"));
                match self.release_spec(spec) {
                    Ok(v) => ok!(v),
                    Err(m) => err(&id, "connect_failed", &m),
                }
            }
            "radios.saved" => {
                if let Some(e) = owner_only(&id) {
                    return e;
                }
                ok!(saved_radios())
            }
            "device.config" => {
                if let Some(e) = owner_only(&id) {
                    return e;
                }
                let radio = try_p!(need(p, "radio"));
                ok!(eng!(self.engine.device_config(radio)))
            }
            "device.apply" => {
                if let Some(e) = owner_only(&id) {
                    return e;
                }
                let radio = try_p!(need(p, "radio"));
                let Some(changes) = p.get("changes").and_then(|c| c.as_object()) else {
                    return err(
                        &id,
                        "invalid",
                        "changes: an object of setting path to new value",
                    );
                };
                ok!(eng!(self.engine.device_apply(
                    radio,
                    changes,
                    self.receive_only,
                    p.get("confirm_region").and_then(|v| v.as_bool()) == Some(true)
                )))
            }
            "app.radios" => ok!(eng!(crate::appview::radios(&self.engine, who))),
            "app.identities" => ok!(eng!(crate::appview::identities(&self.engine, who))),
            "app.messages" => ok!(eng!(crate::appview::messages(
                &self.engine,
                who,
                s(p, "thread")
            ))),
            "app.send" => {
                let thread = try_p!(need(p, "thread"));
                let text = try_p!(need(p, "text"));
                ok!(crate::appview::send(&self.engine, who, thread, text))
            }
            "messages" => {
                let f: Filter = try_p!(serde_json::from_value(
                    p.get("filter").cloned().unwrap_or(json!({}))
                )
                .map_err(|e| e.to_string()));
                // an owner may read as a person (the app does this on a
                // person's behalf); anyone else reads as themselves
                let viewer = if who == OWNER {
                    s(p, "as").unwrap_or(OWNER)
                } else {
                    who
                };
                ok!(eng!(self.engine.messages(viewer, &f)))
            }
            "last_positions" => {
                let viewer = if who == OWNER {
                    s(p, "as").unwrap_or(OWNER)
                } else {
                    who
                };
                ok!(eng!(self.engine.last_positions(viewer)))
            }
            "send" => {
                let text = try_p!(need(p, "text"));
                let network = try_p!(need(p, "network"));
                let ch = p.get("channel").and_then(|c| c.as_u64()).map(|c| c as u32);
                ok!(eng!(self.engine.send_text(
                    who,
                    network,
                    s(p, "radio"),
                    s(p, "to"),
                    ch,
                    text
                )))
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
                eng!(self
                    .engine
                    .with_ids(who, |i| i.assign(node, ident, name, OWNER)));
                ok!(json!({"node": node, "identity": ident}))
            }
            "contacts.create" => {
                let name = try_p!(need(p, "name"));
                let cid = eng!(self.engine.with_ids(who, |i| i.create_contact(
                    name,
                    s(p, "notes"),
                    OWNER
                )));
                ok!(json!({"contact": cid}))
            }
            "contacts.update" => {
                let cid = try_p!(need(p, "contact"));
                eng!(self.engine.with_ids(who, |i| i.update_contact(
                    cid,
                    s(p, "name"),
                    s(p, "notes"),
                    OWNER
                )));
                ok!(json!({"contact": cid}))
            }
            "contacts.told" => {
                let cid = try_p!(need(p, "contact"));
                let told = p.get("told").and_then(|t| t.as_bool()).unwrap_or(true);
                eng!(self
                    .engine
                    .with_ids(who, |i| i.set_told(cid, told, s(p, "date"), OWNER)));
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

    /// The connection layer for a request line: token check for any
    /// principal but the owner, then the request. This is the entry point
    /// for everything that arrives from outside the process.
    pub fn handle_request(&self, line: &str) -> Value {
        let Ok(v) = serde_json::from_str::<Value>(line) else {
            return err(&Value::Null, "bad_request", "not JSON");
        };
        let id = v.get("id").cloned().unwrap_or(Value::Null);
        let who = v.get("principal").and_then(|x| x.as_str()).unwrap_or(OWNER);
        if who != OWNER {
            let method = v.get("method").and_then(|x| x.as_str()).unwrap_or("");
            let Some(ver) = &self.verifier else {
                return err(
                    &id,
                    "forbidden",
                    "only the owner may use this socket directly: other principals come through the taOS controller with a token, and none is configured",
                );
            };
            let tok = v.get("token").and_then(|x| x.as_str()).unwrap_or("");
            if let Err(m) = ver.verify(tok, who, method, crate::model::now_ms()) {
                return err(&id, "forbidden", &m);
            }
        }
        self.handle_trusted(line)
    }

    fn serve_conn(self: &Arc<Self>, stream: UnixStream) {
        // the connecting process must be the owner
        match peer_uid(&stream) {
            Ok(u) if u == self.owner_uid => {}
            Ok(_) | Err(_) => {
                let _ = writeln!(
                    &stream,
                    "{}",
                    err(&Value::Null, "forbidden", "not the owner of this engine")
                );
                return;
            }
        }
        let Ok(rd) = stream.try_clone() else { return };
        let mut w = stream;
        for line in BufReader::new(rd).lines() {
            let Ok(line) = line else { return };
            if line.trim().is_empty() {
                continue;
            }
            if let Ok(v) = serde_json::from_str::<Value>(&line) {
                if v.get("method").and_then(|m| m.as_str()) == Some("subscribe") {
                    let who = v
                        .get("principal")
                        .and_then(|x| x.as_str())
                        .unwrap_or(OWNER)
                        .to_string();
                    if who != OWNER {
                        let _ = writeln!(w, "{}", err(&v["id"], "forbidden", "owner only"));
                        continue;
                    }
                    let rx = self.engine.subscribe();
                    let _ = writeln!(
                        w,
                        "{}",
                        json!({"id": v["id"], "ok": true, "result": {"subscribed": true}})
                    );
                    while let Ok(rec) = rx.recv() {
                        if writeln!(w, "{}", json!({"event": "record", "record": rec})).is_err() {
                            return;
                        }
                    }
                    return;
                }
            }
            let resp = self.handle_request(&line);
            if writeln!(w, "{resp}").is_err() {
                return;
            }
        }
    }

    pub fn serve(self: &Arc<Self>, socket: &Path) -> std::io::Result<()> {
        use std::io::{Error, ErrorKind};
        use std::os::unix::fs::{DirBuilderExt, FileTypeExt, MetadataExt};
        let euid = nix::unistd::geteuid().as_raw();
        let parent = socket
            .parent()
            .filter(|p| !p.as_os_str().is_empty())
            .unwrap_or(Path::new("."));
        let pm = std::fs::metadata(parent)?;
        if pm.uid() != euid || pm.mode() & 0o002 != 0 {
            return Err(Error::new(
                ErrorKind::PermissionDenied,
                format!(
                    "{} must belong to you and not be writable by everyone",
                    parent.display()
                ),
            ));
        }
        if let Ok(m) = std::fs::symlink_metadata(socket) {
            if m.uid() != euid {
                return Err(Error::new(
                    ErrorKind::PermissionDenied,
                    "that socket path belongs to another user: not replacing it",
                ));
            }
            if !m.file_type().is_socket() {
                return Err(Error::new(
                    ErrorKind::AlreadyExists,
                    "that path exists and is not a socket: not replacing it",
                ));
            }
            // only remove a socket nobody answers on
            if UnixStream::connect(socket).is_ok() {
                return Err(Error::new(
                    ErrorKind::AddrInUse,
                    "another taos-mesh is serving this socket",
                ));
            }
            std::fs::remove_file(socket)?;
        }
        // Bind inside a fresh 0700 directory and move the socket into place,
        // so it is never reachable with a wider mode, not even briefly.
        let tmpdir = parent.join(format!(
            ".taos-mesh-bind-{}-{}",
            std::process::id(),
            crate::model::now_ms()
        ));
        std::fs::DirBuilder::new().mode(0o700).create(&tmpdir)?;
        let tmp = tmpdir.join("s");
        let bound = UnixListener::bind(&tmp).and_then(|l| {
            std::fs::set_permissions(&tmp, std::fs::Permissions::from_mode(0o600))?;
            std::fs::rename(&tmp, socket)?;
            Ok(l)
        });
        let _ = std::fs::remove_dir(&tmpdir);
        let l = bound?;
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

fn peer_uid(s: &UnixStream) -> std::io::Result<u32> {
    use nix::sys::socket::{getsockopt, sockopt::PeerCredentials};
    use std::os::fd::AsRawFd;
    getsockopt(s.as_raw_fd(), PeerCredentials)
        .map(|c| c.uid())
        .map_err(std::io::Error::from)
}

/// Blocking client: one request, one response.
pub fn call(socket: &Path, method: &str, principal: &str, params: Value) -> Result<Value, String> {
    call_with_token(socket, method, principal, None, params)
}

/// Like `call`, with the capability token the controller issued for a
/// non-owner principal.
pub fn call_with_token(
    socket: &Path,
    method: &str,
    principal: &str,
    token: Option<&str>,
    params: Value,
) -> Result<Value, String> {
    let mut s = UnixStream::connect(socket)
        .map_err(|e| format!("cannot reach taos-mesh at {}: {e}", socket.display()))?;
    let req = json!({"id": 1, "method": method, "principal": principal, "token": token, "params": params});
    writeln!(s, "{req}").map_err(|e| e.to_string())?;
    let mut line = String::new();
    BufReader::new(s)
        .read_line(&mut line)
        .map_err(|e| e.to_string())?;
    serde_json::from_str(&line).map_err(|e| format!("bad reply: {e}"))
}
