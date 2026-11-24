// SPDX-License-Identifier: AGPL-3.0-or-later
//! Client for the taos-input control socket (JSON line request, one JSON line reply).

use serde_json::{json, Value};
use std::io::{BufRead, BufReader, Write};
use std::os::unix::net::UnixStream;
use std::path::PathBuf;
use std::process::{Child, Command, Stdio};
use std::time::{Duration, Instant};

pub fn sock_path() -> Result<PathBuf, String> {
    if let Ok(p) = std::env::var("TAOS_INPUT_CONTROL") {
        return Ok(PathBuf::from(p));
    }
    match std::env::var("XDG_RUNTIME_DIR") {
        Ok(d) if !d.is_empty() => Ok(PathBuf::from(d).join("taos-input/control.sock")),
        _ => Err("XDG_RUNTIME_DIR is not set".into()),
    }
}

pub fn request(op: Value) -> Result<Value, String> {
    let p = sock_path()?;
    let mut s = UnixStream::connect(&p).map_err(|e| format!("taos-input control socket: {e}"))?;
    let t = Some(Duration::from_secs(3));
    s.set_read_timeout(t).ok();
    s.set_write_timeout(t).ok();
    writeln!(s, "{op}").map_err(|e| e.to_string())?;
    let mut line = String::new();
    BufReader::new(s)
        .read_line(&mut line)
        .map_err(|e| format!("taos-input reply: {e}"))?;
    let v: Value = serde_json::from_str(&line).map_err(|e| format!("taos-input reply: {e}"))?;
    if v.get("ok").and_then(Value::as_bool) == Some(true) {
        Ok(v)
    } else {
        Err(format!(
            "taos-input refused: {}",
            v.get("error")
                .and_then(Value::as_str)
                .unwrap_or("unknown error")
        ))
    }
}

pub fn pair() -> Result<Value, String> {
    request(json!({"op": "pair"}))
}

pub fn running() -> bool {
    request(json!({"op": "status"})).is_ok()
}

/// Spawn `taos-input serve` (next to this binary, else on PATH) and wait for its socket.
pub fn spawn() -> Result<Child, String> {
    let exe = std::env::current_exe()
        .ok()
        .and_then(|e| e.parent().map(|d| d.join("taos-input")))
        .filter(|p| p.exists())
        .unwrap_or_else(|| PathBuf::from("taos-input"));
    let child = Command::new("setsid")
        .arg(exe)
        .arg("serve")
        .stdin(Stdio::null())
        .stdout(Stdio::null())
        .stderr(Stdio::null())
        .spawn()
        .map_err(|e| format!("start taos-input: {e}"))?;
    let t0 = Instant::now();
    while t0.elapsed() < Duration::from_secs(5) {
        if running() {
            return Ok(child);
        }
        std::thread::sleep(Duration::from_millis(100));
    }
    let mut child = child;
    let _ = child.kill();
    let _ = child.wait();
    Err("taos-input did not come up in 5 s".into())
}
