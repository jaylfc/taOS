// SPDX-License-Identifier: AGPL-3.0-or-later
//! The restore-state file. It is written before the first change to the
//! desktop and removed only after everything has been put back.

use serde::{Deserialize, Serialize};
use std::fs;
use std::io::Write;
use std::os::unix::fs::{OpenOptionsExt, PermissionsExt};
use std::path::{Path, PathBuf};

/// A process we started, identified by pid and kernel start time so a
/// recycled pid is never signalled.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct Proc {
    pub role: String,
    pub pid: u32,
    pub start: u64,
}

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize, Default)]
pub struct State {
    pub receiver: String,
    pub size: String,
    pub fps: u32,
    /// Outputs that were active before we changed anything.
    pub prior_outputs: Vec<String>,
    pub virtual_output: Option<String>,
    pub procs: Vec<Proc>,
    pub supervisor: Option<Proc>,
    /// starting, running, ended, failed
    pub status: String,
    pub reason: Option<String>,
    pub unlock_cmd: Option<String>,
    pub url: Option<String>,
    pub code: Option<String>,
    pub input_started_by_us: bool,
}

pub fn dir() -> Result<PathBuf, String> {
    if let Ok(d) = std::env::var("TAOS_CAST_DIR") {
        return Ok(PathBuf::from(d));
    }
    match std::env::var("XDG_RUNTIME_DIR") {
        Ok(d) if !d.is_empty() => Ok(PathBuf::from(d).join("taos-cast")),
        _ => Err("XDG_RUNTIME_DIR is not set and TAOS_CAST_DIR is not given".into()),
    }
}

pub fn ensure_dir(d: &Path) -> Result<(), String> {
    fs::create_dir_all(d).map_err(|e| format!("create {}: {e}", d.display()))?;
    fs::set_permissions(d, fs::Permissions::from_mode(0o700)).map_err(|e| e.to_string())
}

pub fn state_path(d: &Path) -> PathBuf {
    d.join("state.json")
}
pub fn restoring_path(d: &Path) -> PathBuf {
    d.join("state.restoring")
}

pub fn write(d: &Path, s: &State) -> Result<(), String> {
    ensure_dir(d)?;
    let tmp = d.join("state.json.tmp");
    let body = serde_json::to_vec_pretty(s).map_err(|e| e.to_string())?;
    let mut f = fs::OpenOptions::new()
        .write(true)
        .create(true)
        .truncate(true)
        .mode(0o600)
        .open(&tmp)
        .map_err(|e| format!("write state: {e}"))?;
    f.write_all(&body)
        .map_err(|e| format!("write state: {e}"))?;
    f.sync_all().map_err(|e| format!("write state: {e}"))?;
    fs::rename(&tmp, state_path(d)).map_err(|e| format!("write state: {e}"))
}

pub fn read(p: &Path) -> Result<Option<State>, String> {
    match fs::read(p) {
        Ok(b) => serde_json::from_slice(&b)
            .map(Some)
            .map_err(|e| format!("state file {} is damaged: {e}", p.display())),
        Err(e) if e.kind() == std::io::ErrorKind::NotFound => Ok(None),
        Err(e) => Err(format!("read state: {e}")),
    }
}

/// Kernel start time (clock ticks since boot) of a pid, if it exists.
pub fn proc_start(pid: u32) -> Option<u64> {
    let s = fs::read_to_string(format!("/proc/{pid}/stat")).ok()?;
    let rest = &s[s.rfind(')')? + 1..];
    let f: Vec<&str> = rest.split_whitespace().collect();
    if f.first().copied() == Some("Z") {
        return None; // a zombie is gone for our purposes
    }
    f.get(19)?.parse().ok()
}

pub fn alive(p: &Proc) -> bool {
    proc_start(p.pid) == Some(p.start)
}

pub fn me(role: &str) -> Proc {
    let pid = std::process::id();
    Proc {
        role: role.into(),
        pid,
        start: proc_start(pid).unwrap_or(0),
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn round_trip_and_mode() {
        let d = std::env::temp_dir().join(format!("taos-cast-t-{}", std::process::id()));
        let s = State {
            receiver: "Bedroom TV".into(),
            size: "1920x1080".into(),
            fps: 30,
            prior_outputs: vec!["A-1".into(), "B-2".into()],
            virtual_output: Some("HEADLESS-3".into()),
            procs: vec![me("recorder")],
            status: "running".into(),
            ..Default::default()
        };
        write(&d, &s).unwrap();
        let mode = fs::metadata(state_path(&d)).unwrap().permissions().mode() & 0o777;
        assert_eq!(mode, 0o600);
        assert_eq!(read(&state_path(&d)).unwrap().unwrap(), s);
        assert!(alive(&s.procs[0]));
        let mut gone = s.procs[0].clone();
        gone.start += 1;
        assert!(!alive(&gone), "a pid with another start time is not ours");
        fs::remove_dir_all(&d).unwrap();
        assert_eq!(read(&state_path(&d)).unwrap(), None);
    }

    #[test]
    fn damaged_state_is_an_error_not_empty() {
        let d = std::env::temp_dir().join(format!("taos-cast-d-{}", std::process::id()));
        ensure_dir(&d).unwrap();
        fs::write(state_path(&d), b"{not json").unwrap();
        assert!(read(&state_path(&d)).is_err());
        fs::remove_dir_all(&d).unwrap();
    }
}
