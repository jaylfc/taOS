// SPDX-License-Identifier: AGPL-3.0-or-later
//! Idempotent restore: stop what we started, put the monitors back, remove the state.

use crate::state::{self, Proc, State};
use crate::sway;
use std::fs;
use std::path::Path;
use std::process::Command;
use std::time::{Duration, Instant};

const STALE_AFTER: Duration = Duration::from_secs(600);

pub fn signal(p: &Proc, sig: &str) {
    if state::alive(p) {
        let _ = Command::new("kill")
            .arg(format!("-{sig}"))
            .arg(p.pid.to_string())
            .status();
    }
}

/// Stop one process we started: TERM, then KILL after two seconds.
pub fn stop_proc(p: &Proc) {
    if p.pid == std::process::id() || !state::alive(p) {
        return;
    }
    signal(p, "TERM");
    let t0 = Instant::now();
    while state::alive(p) && t0.elapsed() < Duration::from_secs(2) {
        std::thread::sleep(Duration::from_millis(50));
    }
    signal(p, "KILL");
}

pub fn run_hook(cmd: &str) {
    let _ = Command::new("sh").arg("-c").arg(cmd).status();
}

/// Returns Ok(true) when something was restored, Ok(false) when there was nothing to do.
/// On failure the state is kept (as state.restoring) so a later call can finish the job.
pub fn restore(dir: &Path) -> Result<bool, String> {
    let live = state::state_path(dir);
    let held = state::restoring_path(dir);
    // Only one restorer wins the rename; a leftover from a failed run is taken over.
    let _ = fs::rename(&live, &held);
    // A leftover from a restorer that died long ago and keeps failing must not
    // refuse every later start: after ten minutes it is dropped with a warning.
    let stale = fs::metadata(&held)
        .and_then(|m| m.modified())
        .ok()
        .and_then(|t| t.elapsed().ok())
        .is_some_and(|age| age > STALE_AFTER);
    let st: State = match state::read(&held) {
        Ok(Some(s)) => s,
        Ok(None) => return Ok(false),
        Err(e) if stale => {
            eprintln!(
                "taos-cast: dropping an unreadable restore record older than ten minutes: {e}"
            );
            let _ = fs::remove_file(&held);
            return Ok(false);
        }
        Err(e) => return Err(e),
    };

    for p in &st.procs {
        stop_proc(p);
    }
    crate::codeview::remove(dir);
    let mut errors = vec![];
    // A monitor that was unplugged during the cast no longer exists: there is
    // nothing to put back, so it counts as restored (and is reported).
    let mut missing: Vec<String> = vec![];
    match sway::outputs() {
        Ok(list) => {
            for name in &st.prior_outputs {
                if !list.iter().any(|o| &o.name == name) {
                    missing.push(name.clone());
                }
            }
        }
        Err(e) => errors.push(e),
    }
    for name in st.prior_outputs.iter().filter(|n| !missing.contains(n)) {
        if let Err(e) = sway::enable(name) {
            errors.push(e);
        }
    }
    for name in &missing {
        eprintln!("taos-cast: output {name} is no longer present; nothing to restore for it");
    }
    if let Some(v) = &st.virtual_output {
        // Already gone is fine; it is only an error if it is still listed.
        let _ = sway::unplug(v);
    }
    match sway::outputs() {
        Ok(list) => {
            for name in st.prior_outputs.iter().filter(|n| !missing.contains(n)) {
                if !list.iter().any(|o| &o.name == name && o.active) {
                    errors.push(format!("output {name} is not active again"));
                }
            }
            if let Some(v) = &st.virtual_output {
                if list.iter().any(|o| &o.name == v) {
                    errors.push(format!("virtual output {v} is still present"));
                }
            }
        }
        Err(e) => errors.push(e),
    }
    if let Some(u) = &st.unlock_cmd {
        run_hook(u);
    }
    if errors.is_empty() {
        let _ = fs::remove_file(&held);
        Ok(true)
    } else if stale {
        let _ = fs::remove_file(&held);
        Err(format!(
            "{} (record older than ten minutes dropped)",
            errors.join("; ")
        ))
    } else {
        Err(errors.join("; "))
    }
}
