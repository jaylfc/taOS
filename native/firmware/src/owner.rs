// SPDX-License-Identifier: AGPL-3.0-or-later
//! Owner credential. Licence acceptance, consent grants and every flash need a single-use nonce
//! that is minted over a 0600 unix socket in the 0700 data directory, after the kernel-reported
//! peer uid is checked to be the owner's. The app mints one when the person acts on screen, scoped to
//! that one action and subject; the bearer token alone cannot do any of these.
//!
//! Limit: a process running as the same user can still connect to the socket. Keeping an agent away
//! from it takes a separate uid or a sandbox that hides the data directory.
use crate::store::Paths;
use crate::timefmt;
use serde_json::{json, Value};
use std::fs;
use std::io::{BufRead, BufReader, Read, Write};
use std::os::unix::fs::PermissionsExt;
use std::os::unix::net::{UnixListener, UnixStream};
use std::path::PathBuf;
use std::sync::{Arc, Mutex};

pub const ACTIONS: [&str; 3] = ["licence.accept", "consent.grant", "flash.run"];
const LIFETIME: u64 = 300;

struct Nonce {
    value: String,
    action: String,
    subject: String,
    expires: u64,
}

#[derive(Default)]
pub struct Nonces(Mutex<Vec<Nonce>>);

fn random_hex() -> Result<String, String> {
    let mut b = [0u8; 24];
    fs::File::open("/dev/urandom")
        .and_then(|mut f| f.read_exact(&mut b))
        .map_err(|e| format!("random: {e}"))?;
    Ok(b.iter().map(|x| format!("{x:02x}")).collect())
}

fn ct_eq(a: &str, b: &str) -> bool {
    a.len() == b.len() && a.bytes().zip(b.bytes()).fold(0u8, |r, (x, y)| r | (x ^ y)) == 0
}

impl Nonces {
    pub fn mint(&self, action: &str, subject: &str) -> Result<String, String> {
        if !ACTIONS.contains(&action) || subject.is_empty() || subject.len() > 200 {
            return Err("unknown action or empty subject".into());
        }
        let v = random_hex()?;
        let now = timefmt::now_secs();
        let mut g = self.0.lock().map_err(|_| "lock")?;
        g.retain(|n| n.expires > now);
        g.push(Nonce {
            value: v.clone(),
            action: action.into(),
            subject: subject.into(),
            expires: now + LIFETIME,
        });
        Ok(v)
    }
    /// Uses the nonce up, whether or not the action that follows succeeds.
    pub fn consume(&self, value: &str, action: &str, subject: &str) -> bool {
        let now = timefmt::now_secs();
        let Ok(mut g) = self.0.lock() else {
            return false;
        };
        g.retain(|n| n.expires > now);
        let Some(i) = g.iter().position(|n| ct_eq(&n.value, value)) else {
            return false;
        };
        let n = g.remove(i);
        n.action == action && n.subject == subject
    }
}

pub fn socket_path(p: &Paths) -> PathBuf {
    p.root.join("owner.sock")
}

fn handle(nonces: &Nonces, s: UnixStream) {
    let ok_peer = rustix::net::sockopt::socket_peercred(&s)
        .map(|c| c.uid == rustix::process::geteuid())
        .unwrap_or(false);
    let mut out = s.try_clone().ok();
    let reply = |o: &mut Option<UnixStream>, v: Value| {
        if let Some(w) = o.as_mut() {
            let _ = writeln!(w, "{v}");
        }
    };
    if !ok_peer {
        return reply(&mut out, json!({"ok":false,"error":"not the owner"}));
    }
    s.set_read_timeout(Some(std::time::Duration::from_secs(10)))
        .ok();
    let mut line = String::new();
    if BufReader::new(s.take(4096)).read_line(&mut line).is_err() {
        return;
    }
    let req: Value = serde_json::from_str(&line).unwrap_or(Value::Null);
    let r = nonces.mint(
        req["action"].as_str().unwrap_or(""),
        req["subject"].as_str().unwrap_or(""),
    );
    match r {
        Ok(n) => reply(&mut out, json!({"ok":true,"nonce":n,"expires_in":LIFETIME})),
        Err(e) => reply(&mut out, json!({"ok":false,"error":e})),
    }
}

pub fn listen(p: &Paths, nonces: Arc<Nonces>) -> Result<(), String> {
    let path = socket_path(p);
    let _ = fs::remove_file(&path);
    let l = UnixListener::bind(&path).map_err(|e| format!("owner socket: {e}"))?;
    fs::set_permissions(&path, fs::Permissions::from_mode(0o600)).map_err(|e| e.to_string())?;
    std::thread::spawn(move || {
        for s in l.incoming().flatten() {
            let n = nonces.clone();
            std::thread::spawn(move || handle(&n, s));
        }
    });
    Ok(())
}

/// Client side (used by `taos-firmware owner mint` and the tests).
pub fn mint_via_socket(p: &Paths, action: &str, subject: &str) -> Result<String, String> {
    let mut s = UnixStream::connect(socket_path(p)).map_err(|e| format!("owner socket: {e}"))?;
    writeln!(s, "{}", json!({"action":action,"subject":subject})).map_err(|e| e.to_string())?;
    let mut line = String::new();
    BufReader::new(s)
        .read_line(&mut line)
        .map_err(|e| e.to_string())?;
    let v: Value = serde_json::from_str(&line).map_err(|e| e.to_string())?;
    v["nonce"]
        .as_str()
        .map(String::from)
        .ok_or_else(|| v["error"].as_str().unwrap_or("refused").to_string())
}
