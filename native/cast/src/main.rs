// SPDX-License-Identifier: AGPL-3.0-or-later
//! taos-cast: cast the taOS desktop to a TV and always put the monitor back.

mod cli;
mod codeview;
mod engine;
mod restore;
mod slrw;
mod state;
mod supervise;
mod sway;

use cli::{Cmd, StartArgs};
use serde_json::{json, Value};
use std::fs;
use std::process::{Command, Stdio};
use std::time::{Duration, Instant};

fn emit(json_mode: bool, v: Value, human: &str) {
    if json_mode {
        println!("{v}");
    } else {
        println!("{human}");
    }
}

fn fail(json_mode: bool, msg: &str) -> ! {
    if json_mode {
        println!("{}", json!({"ok": false, "error": msg}));
    } else {
        eprintln!("taos-cast: {msg}");
    }
    std::process::exit(1)
}

fn describe(s: &state::State, up: bool) -> (Value, String) {
    let v = json!({
        "ok": true, "status": s.status, "alive": up, "receiver": s.receiver,
        "output": s.virtual_output, "size": s.size, "fps": s.fps,
        "url": s.url, "code": s.code, "reason": s.reason,
    });
    let mut h = format!("{}: casting to {} ({})", s.status, s.receiver, s.size);
    if let Some(u) = &s.url {
        h += &format!("\ncontroller: {u}");
    }
    if let Some(c) = &s.code {
        h += &format!("\npairing code: {c}");
    }
    if !up {
        h += "\nthe supervisor is not running: run `taos-cast restore`";
    }
    (v, h)
}

fn last_path(d: &std::path::Path) -> std::path::PathBuf {
    d.join("last.json")
}

fn do_start(a: StartArgs, j: bool) -> Result<(), String> {
    let d = state::dir()?;
    if let Some(s) = state::read(&state::state_path(&d))? {
        if s.supervisor.as_ref().is_some_and(state::alive) {
            return Err("already casting; run `taos-cast stop` first".into());
        }
        restore::restore(&d).map_err(|e| format!("a stale cast could not be restored: {e}"))?;
    }
    if state::restoring_path(&d).exists() {
        restore::restore(&d).map_err(|e| format!("an earlier restore is unfinished: {e}"))?;
    }
    let active: Vec<String> = sway::outputs()?
        .into_iter()
        .filter(|o| o.active)
        .map(|o| o.name)
        .collect();
    if active.is_empty() {
        return Err("sway reports no active output".into());
    }
    let _ = fs::remove_file(last_path(&d));
    let st = state::State {
        receiver: a.to.clone(),
        size: a.size.clone(),
        fps: a.fps,
        prior_outputs: active,
        status: "starting".into(),
        unlock_cmd: a.unlock_cmd.clone(),
        ..Default::default()
    };
    state::write(&d, &st)?; // before any change to the desktop
    let exe = std::env::current_exe().map_err(|e| e.to_string())?;
    let log = fs::File::create(d.join("supervisor.log")).map_err(|e| e.to_string())?;
    let mut c = Command::new("setsid");
    c.arg(exe).arg("_supervise").args([
        "--to",
        &a.to,
        "--size",
        &a.size,
        "--fps",
        &a.fps.to_string(),
        "--tvpipe",
        &a.tvpipe,
    ]);
    if a.no_input {
        c.arg("--no-input");
    }
    if let Some(x) = &a.lock_cmd {
        c.args(["--lock-cmd", x]);
    }
    if let Some(x) = &a.unlock_cmd {
        c.args(["--unlock-cmd", x]);
    }
    c.stdin(Stdio::null())
        .stdout(log.try_clone().map_err(|e| e.to_string())?)
        .stderr(log)
        .spawn()
        .map_err(|e| format!("start supervisor: {e}"))?;
    let t0 = Instant::now();
    while t0.elapsed() < Duration::from_secs(40) {
        if let Ok(Some(l)) =
            fs::read(last_path(&d)).map(|b| serde_json::from_slice::<Value>(&b).ok())
        {
            return Err(l["reason"]
                .as_str()
                .unwrap_or("cast ended during start")
                .to_string());
        }
        if let Some(s) = state::read(&state::state_path(&d))? {
            if s.status == "running" {
                let (v, h) = describe(&s, true);
                emit(j, v, &h);
                return Ok(());
            }
        }
        std::thread::sleep(Duration::from_millis(100));
    }
    Err("the cast did not start in 40 s; run `taos-cast restore` to put everything back".into())
}

fn do_stop(j: bool) -> Result<(), String> {
    let d = state::dir()?;
    let Some(s) = state::read(&state::state_path(&d))? else {
        if state::restoring_path(&d).exists() {
            restore::restore(&d)?;
            emit(j, json!({"ok": true, "status": "restored"}), "restored");
        } else {
            emit(j, json!({"ok": true, "status": "idle"}), "not casting");
        }
        return Ok(());
    };
    if let Some(p) = s.supervisor.as_ref().filter(|p| state::alive(p)) {
        {
            restore::signal(p, "TERM");
            let t0 = Instant::now();
            while state::alive(p) && t0.elapsed() < Duration::from_secs(20) {
                std::thread::sleep(Duration::from_millis(100));
            }
            if state::alive(p) {
                return Err("the supervisor did not stop in 20 s".into());
            }
        }
    }
    // The supervisor restores itself; finish anything it left (also the crash case).
    restore::restore(&d)?;
    emit(
        j,
        json!({"ok": true, "status": "stopped"}),
        "stopped, monitor restored",
    );
    Ok(())
}

fn do_status(j: bool) -> Result<(), String> {
    let d = state::dir()?;
    if let Some(s) = state::read(&state::state_path(&d))? {
        let up = s.supervisor.as_ref().is_some_and(state::alive);
        let (v, h) = describe(&s, up);
        emit(j, v, &h);
    } else if let Ok(b) = fs::read(last_path(&d)) {
        let v: Value = serde_json::from_slice(&b).map_err(|e| e.to_string())?;
        let h = format!("idle; last cast {}: {}", v["status"], v["reason"]);
        emit(j, json!({"ok": true, "status": "idle", "last": v}), &h);
    } else {
        emit(j, json!({"ok": true, "status": "idle"}), "idle");
    }
    Ok(())
}

fn do_pair(j: bool) -> Result<(), String> {
    let v = engine::pair()?;
    let h = format!(
        "controller: {}\npairing code: {}",
        v["url"].as_str().unwrap_or("?"),
        v["code"].as_str().unwrap_or("?")
    );
    emit(j, v, &h);
    Ok(())
}

fn main() {
    let args: Vec<String> = std::env::args().skip(1).collect();
    let cli = match cli::parse(&args) {
        Ok(c) => c,
        Err(e) => fail(
            args.iter().any(|a| a == "--json"),
            &format!("{e}\n{}", cli::USAGE),
        ),
    };
    let j = cli.json;
    let r = match cli.cmd {
        Cmd::Help => {
            println!("{}", cli::USAGE);
            Ok(())
        }
        Cmd::Start(a) => do_start(a, j),
        Cmd::Stop => do_stop(j),
        Cmd::Status => do_status(j),
        Cmd::Pair => do_pair(j),
        Cmd::Restore => state::dir().and_then(|d| restore::restore(&d)).map(|done| {
            emit(
                j,
                json!({"ok": true, "restored": done}),
                if done {
                    "restored"
                } else {
                    "nothing to restore"
                },
            )
        }),
        Cmd::Supervise(a) => supervise::supervise(a),
        Cmd::Watchdog => supervise::watchdog(),
        Cmd::ShowCode(f) => codeview::show(std::path::Path::new(&f)),
    };
    if let Err(e) = r {
        fail(j, &e);
    }
}
