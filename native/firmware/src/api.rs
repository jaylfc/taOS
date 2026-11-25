// SPDX-License-Identifier: AGPL-3.0-or-later
//! The local JSON API: HTTP/1.1 on 127.0.0.1 with a bearer token kept in the data directory.
use crate::boards::{Network, BOARDS};
use crate::detect;
use crate::flash::{self, Ctx, Request};
use crate::library::{self, RefreshOpts};
use crate::net::Curl;
use crate::store::{self, Consent, Licences, Manifest, Paths};
use crate::tools::System;
use serde_json::{json, Value};
use std::fs;
use std::io::{Read, Write};
use std::net::{TcpListener, TcpStream};
use std::sync::{Arc, Mutex};
use std::time::Duration;

pub struct App {
    pub paths: Paths,
    pub cfg: detect::Cfg,
    pub verify_wait: Duration,
    pub token: String,
    pub port: u16,
    /// Held while a flash or a refresh runs; a second one is refused rather than queued.
    busy: Mutex<()>,
    nonces: Arc<crate::owner::Nonces>,
}

pub fn verify_wait_from_env() -> Duration {
    let ms = std::env::var("TAOS_FW_VERIFY_WAIT_MS")
        .ok()
        .and_then(|v| v.parse().ok())
        .unwrap_or(25_000);
    Duration::from_millis(ms)
}

fn make_token() -> Result<String, String> {
    let mut b = [0u8; 32];
    fs::File::open("/dev/urandom")
        .and_then(|mut f| f.read_exact(&mut b))
        .map_err(|e| format!("random token: {e}"))?;
    Ok(b.iter().map(|x| format!("{x:02x}")).collect())
}

/// Read the token, creating it (mode 0600) on first use.
pub fn load_or_create_token(p: &Paths) -> Result<String, String> {
    use std::os::unix::fs::{OpenOptionsExt, PermissionsExt};
    p.ensure()?;
    if let Ok(t) = fs::read_to_string(p.token()) {
        let t = t.trim().to_string();
        if t.len() >= 32 {
            return Ok(t);
        }
    }
    let t = make_token()?;
    let mut f = fs::OpenOptions::new()
        .write(true)
        .create(true)
        .truncate(true)
        .mode(0o600)
        .open(p.token())
        .map_err(|e| format!("token file: {e}"))?;
    f.write_all(t.as_bytes()).map_err(|e| e.to_string())?;
    fs::set_permissions(p.token(), fs::Permissions::from_mode(0o600)).map_err(|e| e.to_string())?;
    Ok(t)
}

pub fn serve(paths: Paths, port: u16) -> Result<(), String> {
    let token = load_or_create_token(&paths)?;
    let nonces: Arc<crate::owner::Nonces> = Arc::default();
    crate::owner::listen(&paths, nonces.clone())?;
    let l = TcpListener::bind(("127.0.0.1", port))
        .map_err(|e| format!("bind 127.0.0.1:{port}: {e}"))?;
    let port = l.local_addr().map_err(|e| e.to_string())?.port();
    let app = Arc::new(App {
        paths,
        cfg: detect::Cfg::from_env(),
        verify_wait: verify_wait_from_env(),
        token,
        port,
        busy: Mutex::new(()),
        nonces: nonces.clone(),
    });
    println!(
        "{}",
        json!({"ok":true,"listening":format!("127.0.0.1:{port}"),"port":port})
    );
    let _ = std::io::stdout().flush();
    for s in l.incoming().flatten() {
        let app = app.clone();
        std::thread::spawn(move || handle(&app, s));
    }
    Ok(())
}

struct Req {
    method: String,
    path: String,
    query: Vec<(String, String)>,
    headers: Vec<(String, String)>,
    body: Vec<u8>,
}

fn parse_request(s: &mut TcpStream) -> Result<Req, String> {
    s.set_read_timeout(Some(Duration::from_secs(15))).ok();
    let mut buf = vec![];
    let mut tmp = [0u8; 4096];
    let head_end = loop {
        if let Some(i) = buf.windows(4).position(|w| w == b"\r\n\r\n") {
            break i;
        }
        if buf.len() > 16 * 1024 {
            return Err("headers too large".into());
        }
        let n = s.read(&mut tmp).map_err(|e| e.to_string())?;
        if n == 0 {
            return Err("closed".into());
        }
        buf.extend_from_slice(&tmp[..n]);
    };
    let head = String::from_utf8_lossy(&buf[..head_end]).into_owned();
    let mut lines = head.lines();
    let first = lines.next().ok_or("empty request")?;
    let mut it = first.split_whitespace();
    let method = it.next().ok_or("no method")?.to_string();
    let target = it.next().ok_or("no target")?;
    let (path, q) = target.split_once('?').unwrap_or((target, ""));
    let query = q
        .split('&')
        .filter(|x| !x.is_empty())
        .map(|kv| {
            let (k, v) = kv.split_once('=').unwrap_or((kv, ""));
            (k.to_string(), v.to_string())
        })
        .collect();
    let headers: Vec<(String, String)> = lines
        .filter_map(|l| {
            l.split_once(':')
                .map(|(k, v)| (k.trim().to_ascii_lowercase(), v.trim().to_string()))
        })
        .collect();
    let len: usize = headers
        .iter()
        .find(|(k, _)| k == "content-length")
        .and_then(|(_, v)| v.parse().ok())
        .unwrap_or(0);
    if len > 64 * 1024 {
        return Err("body too large".into());
    }
    let mut body = buf[head_end + 4..].to_vec();
    while body.len() < len {
        let n = s.read(&mut tmp).map_err(|e| e.to_string())?;
        if n == 0 {
            return Err("closed".into());
        }
        body.extend_from_slice(&tmp[..n]);
    }
    body.truncate(len);
    Ok(Req {
        method,
        path: path.to_string(),
        query,
        headers,
        body,
    })
}

fn ct_eq(a: &str, b: &str) -> bool {
    a.len() == b.len()
        && a.bytes()
            .zip(b.bytes())
            .fold(0u8, |acc, (x, y)| acc | (x ^ y))
            == 0
}

fn respond(s: &mut TcpStream, status: u16, v: &Value) {
    let reason = match status {
        200 => "OK",
        400 => "Bad Request",
        401 => "Unauthorized",
        403 => "Forbidden",
        404 => "Not Found",
        405 => "Method Not Allowed",
        409 => "Conflict",
        _ => "Error",
    };
    let body = v.to_string();
    let _ = write!(
        s,
        "HTTP/1.1 {status} {reason}\r\nContent-Type: application/json\r\nContent-Length: {}\r\nCache-Control: no-store\r\nConnection: close\r\n\r\n{body}",
        body.len()
    );
}

fn handle(app: &App, mut s: TcpStream) {
    let req = match parse_request(&mut s) {
        Ok(r) => r,
        Err(e) => return respond(&mut s, 400, &json!({"ok":false,"error":e})),
    };
    let hdr = |k: &str| {
        req.headers
            .iter()
            .find(|(h, _)| h == k)
            .map(|(_, v)| v.as_str())
    };
    // Not for web pages: no cross-origin callers, and only the loopback host name.
    let host_ok = hdr("host").is_some_and(|h| {
        h == format!("127.0.0.1:{}", app.port) || h == format!("localhost:{}", app.port)
    });
    if hdr("origin").is_some() || !host_ok {
        return respond(&mut s, 403, &json!({"ok":false,"error":"forbidden"}));
    }
    let given = hdr("authorization")
        .and_then(|v| v.strip_prefix("Bearer "))
        .unwrap_or("");
    if !ct_eq(given, &app.token) {
        return respond(
            &mut s,
            401,
            &json!({"ok":false,"error":"missing or wrong bearer token"}),
        );
    }
    let (status, v) = route(app, &req);
    respond(&mut s, status, &v);
}

/// Licence acceptance, consent and flashing need the owner's single-use nonce for that exact action.
fn need_owner(app: &App, body: &Value, action: &str, subject: &str) -> Option<(u16, Value)> {
    let n = body["owner_nonce"].as_str().unwrap_or("");
    if app.nonces.consume(n, action, subject) {
        None
    } else {
        Some((
            403,
            json!({"ok":false,"code":"owner_required","message":format!(
                "{action} needs the owner's confirmation: mint a nonce on the owner socket after the person acts on screen")}),
        ))
    }
}

fn bad(msg: &str) -> (u16, Value) {
    (400, json!({"ok":false,"error":msg}))
}

fn s_field(b: &Value, k: &str) -> String {
    b[k].as_str().unwrap_or("").to_string()
}

fn q<'a>(r: &'a Req, k: &str) -> Option<&'a str> {
    r.query
        .iter()
        .find(|(a, _)| a == k)
        .map(|(_, v)| v.as_str())
}

fn route(app: &App, r: &Req) -> (u16, Value) {
    let body: Value = if r.body.is_empty() {
        json!({})
    } else {
        match serde_json::from_slice(&r.body) {
            Ok(v) => v,
            Err(_) => return bad("body is not JSON"),
        }
    };
    let parts: Vec<&str> = r.path.trim_matches('/').split('/').collect();
    let m = r.method.as_str();
    let p = &app.paths;
    let sys = System;
    let ctx = Ctx {
        paths: p,
        runner: &sys,
        cfg: &app.cfg,
        verify_wait: app.verify_wait,
    };
    let err = |e: String| (400, json!({"ok":false,"error":e}));
    match (m, parts.as_slice()) {
        ("GET", ["v1", "status"]) => {
            let n = Manifest::load(p).map(|m| m.entries.len()).unwrap_or(0);
            (
                200,
                json!({"ok":true,"service":"taos-firmware","version":env!("CARGO_PKG_VERSION"),"library_entries":n}),
            )
        }
        ("GET", ["v1", "boards"]) => {
            let b: Vec<Value> = BOARDS
                .iter()
                .map(|b| json!({"id":b.id,"name":b.name,"family":b.family,"chip":b.chip,
                    "networks":b.networks().iter().map(|n| n.id()).collect::<Vec<_>>(),"recovery":b.recovery}))
                .collect();
            (200, json!({"ok":true,"boards":b}))
        }
        ("GET", ["v1", "library"]) => match Manifest::load(p) {
            Ok(man) => {
                let e: Vec<_> = man
                    .entries
                    .iter()
                    .filter(|e| {
                        q(r, "board").is_none_or(|b| b == e.board)
                            && q(r, "network").is_none_or(|n| n == e.network)
                    })
                    .collect();
                (200, json!({"ok":true,"entries":e}))
            }
            Err(e) => err(e),
        },
        ("POST", ["v1", "library", "refresh"]) => {
            let Ok(_g) = app.busy.try_lock() else {
                return (
                    409,
                    json!({"ok":false,"code":"busy","error":"a flash or refresh is already running"}),
                );
            };
            let f = match Curl::from_env(p.tmp()) {
                Ok(f) => f,
                Err(e) => return err(e),
            };
            let o = RefreshOpts {
                force: body["force"].as_bool().unwrap_or(false),
                network: body["network"].as_str().and_then(Network::parse),
                board: body["board"].as_str().map(String::from),
            };
            (
                200,
                json!({"ok":true,"reports":library::refresh(p, &f, &o)}),
            )
        }
        ("GET", ["v1", "licences"]) => match Licences::load(p) {
            Ok(l) => {
                let pending: Vec<Value> = crate::boards::NETWORKS
                    .iter()
                    .filter(|n| l.get(n.id()).is_none())
                    .map(|n| json!({"network":n.id(),"name":n.display(),"licence_id":n.licence_id(),"licence_url":n.licence_url()}))
                    .collect();
                (
                    200,
                    json!({"ok":true,"accepted":l.accepted,"pending":pending}),
                )
            }
            Err(e) => err(e),
        },
        ("POST", ["v1", "licences", "accept"]) => {
            let Some(n) = Network::parse(&s_field(&body, "network")) else {
                return bad("network must be meshtastic, meshcore or rnode");
            };
            if let Some(r) = need_owner(app, &body, "licence.accept", n.id()) {
                return r;
            }
            match Licences::accept(p, n, &s_field(&body, "by"), "") {
                Ok((a, new)) => (200, json!({"ok":true,"newly_accepted":new,"acceptance":a})),
                Err(e) => err(e),
            }
        }
        ("GET", ["v1", "devices"]) => {
            // A probe resets boards, so it never runs while a flash is writing (and a flash cannot
            // start while one runs): the same lock covers both.
            let want = q(r, "probe") == Some("1");
            let guard = if want { app.busy.try_lock().ok() } else { None };
            let probed = want && guard.is_some();
            (
                200,
                json!({"ok":true,"probed":probed,"probe_skipped_busy":want && !probed,
                    "devices":detect::detect(&app.cfg, &sys, probed)}),
            )
        }
        ("GET", ["v1", "devices", id, "firmware"]) => {
            let want = q(r, "probe") == Some("1");
            let guard = if want { app.busy.try_lock().ok() } else { None };
            let devs = detect::detect(&app.cfg, &sys, want && guard.is_some());
            let Some(d) = devs.iter().find(|d| d.id == *id) else {
                return (404, json!({"ok":false,"error":"no such device"}));
            };
            match flash::compatible(p, d) {
                Ok(v) => (200, json!({"ok":true,"device":d,"firmware":v})),
                Err(e) => err(e),
            }
        }
        ("POST", ["v1", "flash", "plan"]) => {
            let Ok(_g) = app.busy.try_lock() else {
                return (
                    409,
                    json!({"ok":false,"code":"busy","message":"a flash or refresh is already running"}),
                );
            };
            match flash::plan(&ctx, &s_field(&body, "device"), &s_field(&body, "entry")) {
                Ok(pl) => (200, json!({"ok":true,"plan":pl})),
                Err(rf) => (200, json!({"ok":false,"code":rf.code,"message":rf.message})),
            }
        }
        ("POST", ["v1", "flash", "run"]) => {
            let Ok(_g) = app.busy.try_lock() else {
                return (
                    409,
                    json!({"ok":false,"code":"busy","message":"a flash or refresh is already running"}),
                );
            };
            let subject = format!("{}|{}", s_field(&body, "device"), s_field(&body, "entry"));
            if let Some(r) = need_owner(app, &body, "flash.run", &subject) {
                return r;
            }
            let req = Request {
                device_id: s_field(&body, "device"),
                entry_id: s_field(&body, "entry"),
                confirm: body["confirm"].as_str().map(String::from),
                actor: body["actor"].as_str().unwrap_or("person").to_string(),
                allow_no_backup: body["allow_no_backup"].as_bool().unwrap_or(false),
                erase: body["erase"].as_bool().unwrap_or(false),
                ack_unsigned: body["ack_unsigned"].as_bool().unwrap_or(false),
                restore_from: body["restore_from"].as_str().map(String::from),
            };
            (200, flash::run(&ctx, &req))
        }
        ("GET", ["v1", "backups"]) => (200, json!({"ok":true,"backups":flash::list_backups(p)})),
        ("GET", ["v1", "log"]) => {
            let n = q(r, "limit").and_then(|v| v.parse().ok()).unwrap_or(50);
            (200, json!({"ok":true,"log":store::read_audit(p, n)}))
        }
        ("GET", ["v1", "consent"]) => match Consent::load(p) {
            Ok(c) => (200, json!({"ok":true,"grants":c.grants})),
            Err(e) => err(e),
        },
        ("POST", ["v1", "consent", "grant"]) => {
            if let Some(r) = need_owner(app, &body, "consent.grant", &s_field(&body, "agent")) {
                return r;
            }
            match Consent::grant(
                p,
                &s_field(&body, "agent"),
                &s_field(&body, "by"),
                body["minutes"].as_u64().unwrap_or(30),
            ) {
                Ok(g) => (200, json!({"ok":true,"grant":g})),
                Err(e) => err(e),
            }
        }
        ("POST", ["v1", "consent", "revoke"]) => match Consent::revoke(p, &s_field(&body, "agent"))
        {
            Ok(c) => (200, json!({"ok":true,"revoked":c})),
            Err(e) => err(e),
        },
        (_, ["v1", ..]) => (404, json!({"ok":false,"error":"no such endpoint"})),
        _ => (404, json!({"ok":false,"error":"not found"})),
    }
}
