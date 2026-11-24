// SPDX-License-Identifier: AGPL-3.0-or-later
//! A very small sway IPC client: run one command.

use std::io::{Read, Write};
use std::os::unix::net::UnixStream;
use std::path::PathBuf;
use std::time::Duration;

const MAGIC: &[u8; 6] = b"i3-ipc";
const RUN_COMMAND: u32 = 0;

/// The sway socket: $SWAYSOCK if it works, else the newest sway-ipc socket in
/// the runtime directory (the compositor may have restarted under a new name).
fn connect() -> Result<UnixStream, String> {
    let mut tried = Vec::new();
    if let Some(p) = std::env::var_os("SWAYSOCK") {
        tried.push(PathBuf::from(p));
    }
    if let Some(dir) = std::env::var_os("XDG_RUNTIME_DIR") {
        if let Ok(rd) = std::fs::read_dir(dir) {
            let mut found: Vec<(std::time::SystemTime, PathBuf)> = rd
                .filter_map(|e| e.ok())
                .filter(|e| e.file_name().to_string_lossy().starts_with("sway-ipc."))
                .filter_map(|e| Some((e.metadata().ok()?.modified().ok()?, e.path())))
                .collect();
            found.sort();
            found.reverse();
            tried.extend(found.into_iter().map(|(_, p)| p));
        }
    }
    for p in tried {
        if let Ok(s) = UnixStream::connect(&p) {
            return Ok(s);
        }
    }
    Err("no sway socket".into())
}

/// Frame a request: magic, payload length, message type (little endian).
pub fn frame(kind: u32, payload: &[u8]) -> Vec<u8> {
    let mut v = Vec::with_capacity(14 + payload.len());
    v.extend_from_slice(MAGIC);
    v.extend_from_slice(&(payload.len() as u32).to_le_bytes());
    v.extend_from_slice(&kind.to_le_bytes());
    v.extend_from_slice(payload);
    v
}

pub fn run_command(cmd: &str) -> Result<(), String> {
    let mut s = connect()?;
    s.set_read_timeout(Some(Duration::from_secs(2))).ok();
    s.set_write_timeout(Some(Duration::from_secs(2))).ok();
    s.write_all(&frame(RUN_COMMAND, cmd.as_bytes()))
        .map_err(|e| e.to_string())?;
    let mut head = [0u8; 14];
    s.read_exact(&mut head).map_err(|e| e.to_string())?;
    if &head[..6] != MAGIC {
        return Err("bad sway reply".into());
    }
    let len = u32::from_le_bytes([head[6], head[7], head[8], head[9]]) as usize;
    if len > 1 << 20 {
        return Err("sway reply too large".into());
    }
    let mut body = vec![0u8; len];
    s.read_exact(&mut body).map_err(|e| e.to_string())?;
    let v: serde_json::Value = serde_json::from_slice(&body).map_err(|e| e.to_string())?;
    match v.as_array().and_then(|a| a.first()) {
        Some(r) if r.get("success").and_then(|b| b.as_bool()) == Some(true) => Ok(()),
        Some(r) => Err(r
            .get("error")
            .and_then(|e| e.as_str())
            .unwrap_or("sway refused")
            .to_string()),
        None => Err("empty sway reply".into()),
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn frames_are_magic_length_type_payload() {
        let f = frame(0, b"workspace next");
        assert_eq!(&f[..6], b"i3-ipc");
        assert_eq!(u32::from_le_bytes([f[6], f[7], f[8], f[9]]), 14);
        assert_eq!(u32::from_le_bytes([f[10], f[11], f[12], f[13]]), 0);
        assert_eq!(&f[14..], b"workspace next");
    }
}
