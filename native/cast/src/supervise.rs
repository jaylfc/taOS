// SPDX-License-Identifier: AGPL-3.0-or-later
//! The supervisor (owns the pipeline) and the watchdog (restores if the supervisor dies).

use crate::cli::StartArgs;
use crate::state::{self, Proc, State};
use crate::{codeview, engine, restore, slrw, sway};
use serde_json::json;
use std::io::Read;
use std::path::Path;
use std::process::{Child, Command, Stdio};
use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::Arc;
use std::time::Duration;

fn update(dir: &Path, f: impl FnOnce(&mut State)) -> Result<(), String> {
    let mut s = state::read(&state::state_path(dir))?.ok_or("state file is gone")?;
    f(&mut s);
    state::write(dir, &s)
}

fn proc_of(role: &str, c: &Child) -> Proc {
    Proc {
        role: role.into(),
        pid: c.id(),
        start: state::proc_start(c.id()).unwrap_or(0),
    }
}

pub fn recorder_args(output: &str, fps: u32) -> Vec<String> {
    let g = fps * 2; // a keyframe every two seconds
    let x264 = format!("x264-params=keyint={g}:min-keyint={g}:scenecut=0:aud=1:colorprim=bt709:transfer=bt709:colormatrix=bt709");
    let mut a: Vec<String> = ["-o", output, "-D", "-r"].map(String::from).to_vec();
    a.push(fps.to_string());
    for s in [
        "-c",
        "libx264",
        "-x",
        "yuv420p",
        "-F",
        "scale=out_color_matrix=bt709:out_range=full",
        "-p",
        "preset=veryfast",
        "-p",
        "tune=zerolatency",
        "-p",
        "crf=20",
        "-p",
        "maxrate=12000k",
        "-p",
        "bufsize=6000k",
        "-p",
        "bf=0",
        "-p",
    ] {
        a.push(s.into());
    }
    a.push(format!("g={g}"));
    a.push("-p".into());
    a.push(x264);
    for s in ["-m", "h264", "-f", "/dev/stdout"] {
        a.push(s.into());
    }
    a
}

pub fn tvpipe_args(to: &str, size: &str, fps: u32) -> Vec<String> {
    [
        "play",
        "--to",
        to,
        "--no-audio",
        "--latency",
        "300",
        "--raw-video",
    ]
    .map(String::from)
    .into_iter()
    .chain([format!("{size}@{fps}:h264"), "-".into()])
    .collect()
}

fn wallpaper_path(size: &str) -> Option<String> {
    let o = Command::new("taos-wallpaper")
        .args(["path", "--size", size])
        .output()
        .ok()?;
    let p = String::from_utf8_lossy(&o.stdout).trim().to_string();
    (o.status.success() && !p.is_empty()).then_some(p)
}

fn end(dir: &Path, status: &str, reason: &str) {
    let _ = std::fs::write(
        dir.join("last.json"),
        json!({"status": status, "reason": reason}).to_string(),
    );
}

pub fn supervise(a: StartArgs) -> Result<(), String> {
    let dir = state::dir()?;
    let term = Arc::new(AtomicBool::new(false));
    for sig in [
        signal_hook::consts::SIGTERM,
        signal_hook::consts::SIGINT,
        signal_hook::consts::SIGHUP,
    ] {
        signal_hook::flag::register(sig, term.clone()).map_err(|e| e.to_string())?;
    }
    update(&dir, |s| s.supervisor = Some(state::me("supervisor")))?;
    // The watchdog starts before the first change, in its own session.
    let exe = std::env::current_exe().map_err(|e| e.to_string())?;
    let mut wd = Command::new("setsid")
        .arg(exe)
        .arg("_watchdog")
        .stdin(Stdio::piped())
        .stdout(Stdio::null())
        .stderr(Stdio::null())
        .spawn()
        .map_err(|e| format!("start watchdog: {e}"))?;
    let wd_in = wd.stdin.take();

    let mut kids: Vec<(String, Child)> = vec![];
    let result = run(&dir, &a, &term, &mut kids);
    for (_, c) in kids.iter_mut() {
        let _ = c.kill();
        let _ = c.wait();
    }
    let (status, reason) = match &result {
        Ok(r) => ("ended", r.clone()),
        Err(e) => ("failed", e.clone()),
    };
    end(&dir, status, &reason);
    let restored = restore::restore(&dir);
    if let Err(e) = &restored {
        eprintln!("restore failed, the watchdog will retry: {e}");
        end(&dir, "failed", &format!("{reason}; restore failed: {e}"));
    }
    drop(wd_in); // EOF tells the watchdog we are finished
    let _ = wd.wait();
    result.map(|_| ()).and(restored.map(|_| ()))
}

fn run(
    dir: &Path,
    a: &StartArgs,
    term: &AtomicBool,
    kids: &mut Vec<(String, Child)>,
) -> Result<String, String> {
    let out = sway::create_output()?;
    update(dir, |s| s.virtual_output = Some(out.clone()))?;
    sway::set_mode(&out, &a.size, a.fps)?;
    sway::set_wallpaper(&out, wallpaper_path(&a.size).as_deref());
    let (w, h) = a.size.split_once('x').unwrap_or(("0", "0"));
    let ok = (0..50).any(|_| {
        std::thread::sleep(Duration::from_millis(100));
        sway::outputs().is_ok_and(|l| {
            l.iter().any(|o| {
                Some(o.name.as_str()) == Some(out.as_str())
                    && o.active
                    && o.width.to_string() == w
                    && o.height.to_string() == h
            })
        })
    });
    if !ok {
        return Err(format!(
            "virtual output {out} did not become active at {}",
            a.size
        ));
    }

    let (mut url, mut code) = (None, None);
    let mut expires_in = 300u64;
    let mut devices_before = None;
    if !a.no_input {
        let mut started = false;
        if !engine::running() {
            match engine::spawn() {
                Ok(c) => {
                    let p = proc_of("engine", &c);
                    update(dir, |s| {
                        s.procs.push(p);
                        s.input_started_by_us = true;
                    })?;
                    kids.push(("engine".into(), c));
                    started = true;
                }
                Err(e) => eprintln!("controller not available: {e}"),
            }
        }
        if started || engine::running() {
            if let Ok(v) = engine::pair() {
                url = v["url"].as_str().map(String::from);
                code = v["code"].as_str().map(String::from);
                expires_in = v["expires_in"].as_u64().unwrap_or(300);
            }
        }
        if let (Some(u), Some(c)) = (&url, &code) {
            let ttl = Duration::from_secs(expires_in);
            if let Ok(file) = codeview::write(dir, u, c, ttl) {
                // A terminal window on the cast screen; the code is read from
                // the 0600 file, never passed as an argument.
                if let Ok(exe) = std::env::current_exe() {
                    if let Ok(n) = Command::new("foot")
                        .args(["-a", "taos-cast-code", "-T", "taOS controller", "--"])
                        .arg(exe)
                        .arg("_show-code")
                        .arg(&file)
                        .stdin(Stdio::null())
                        .stdout(Stdio::null())
                        .stderr(Stdio::null())
                        .spawn()
                    {
                        let p = proc_of("codeview", &n);
                        update(dir, |s| s.procs.push(p))?;
                        kids.push(("codeview".into(), n));
                        let o = out.clone();
                        std::thread::spawn(move || {
                            for _ in 0..30 {
                                std::thread::sleep(Duration::from_millis(100));
                                let c = format!(
                                    "[app_id=\"taos-cast-code\"] move container to output {o}"
                                );
                                if sway::command(&c).is_ok() {
                                    break;
                                }
                            }
                        });
                        devices_before = engine::device_count();
                    }
                }
            }
        }
    }

    // Only now that the virtual output works is the monitor switched off.
    let prior = state::read(&state::state_path(dir))?
        .map(|s| s.prior_outputs)
        .unwrap_or_default();
    for name in prior.iter().filter(|n| **n != out) {
        sway::disable(name)?;
    }
    if let Some(l) = &a.lock_cmd {
        restore::run_hook(l);
    }

    let mut rec = Command::new("wf-recorder")
        .args(recorder_args(&out, a.fps))
        .stdin(Stdio::null())
        .stdout(Stdio::piped())
        .stderr(Stdio::null())
        .spawn()
        .map_err(|e| format!("start wf-recorder: {e}"))?;
    let rec_out = rec.stdout.take().ok_or("recorder stdout")?;
    let rp = proc_of("recorder", &rec);
    update(dir, |s| s.procs.push(rp))?;
    kids.push(("recorder".into(), rec));
    let mut tv = Command::new(&a.tvpipe)
        .args(tvpipe_args(&a.to, &a.size, a.fps))
        .stdin(Stdio::piped())
        .stdout(Stdio::null())
        .stderr(
            std::fs::File::create(dir.join("tvpipe.log"))
                .map(Stdio::from)
                .unwrap_or_else(|_| Stdio::null()),
        )
        .spawn()
        .map_err(|e| format!("start tvpipe: {e}"))?;
    let tv_in = tv.stdin.take().ok_or("tvpipe stdin")?;
    let tp = proc_of("tvpipe", &tv);
    update(dir, |s| s.procs.push(tp))?;
    kids.push(("tvpipe".into(), tv));
    let fps = f64::from(a.fps);
    let copier = std::thread::spawn(move || slrw::run(rec_out, tv_in, fps));

    update(dir, |s| {
        s.status = "running".into();
        s.url = url.clone();
        s.code = code.clone();
    })?;
    let mut last_poll = std::time::Instant::now();
    let reason = loop {
        if term.load(Ordering::Relaxed) {
            break "stopped".to_string();
        }
        let mut gone = None;
        for (role, c) in kids.iter_mut() {
            if role == "codeview" {
                continue; // closed by the person or expired: not an end of the cast
            }
            if let Ok(Some(st)) = c.try_wait() {
                gone = Some(format!("{role} exited ({st})"));
                break;
            }
        }
        if let Some(g) = gone {
            break g;
        }
        if let Some(before) = devices_before {
            if last_poll.elapsed() >= Duration::from_secs(1) {
                last_poll = std::time::Instant::now();
                let paired = engine::device_count().is_some_and(|n| n > before);
                let closed = kids
                    .iter_mut()
                    .find(|(r, _)| r == "codeview")
                    .is_none_or(|(_, c)| matches!(c.try_wait(), Ok(Some(_))));
                if paired || closed {
                    close_code(dir, kids);
                    devices_before = None;
                }
            }
        }
        std::thread::sleep(Duration::from_millis(100));
    };
    close_code(dir, kids);
    for (_, c) in kids.iter_mut() {
        let _ = c.kill();
        let _ = c.wait();
    }
    let _ = copier.join();
    Ok(reason)
}

/// Take the pairing code off the screen and off disk.
fn close_code(dir: &Path, kids: &mut [(String, Child)]) {
    for (r, c) in kids.iter_mut() {
        if r == "codeview" {
            let _ = c.kill();
            let _ = c.wait();
        }
    }
    codeview::remove(dir);
}

/// Waits for EOF on stdin (the supervisor closed or lost its end) and restores.
pub fn watchdog() -> Result<(), String> {
    let dir = state::dir()?;
    let mut sink = [0u8; 64];
    let mut stdin = std::io::stdin();
    while stdin.read(&mut sink).map(|n| n > 0).unwrap_or(false) {}
    for _ in 0..10 {
        match restore::restore(&dir) {
            Ok(_) => return Ok(()),
            Err(e) => {
                eprintln!("watchdog restore: {e}");
                std::thread::sleep(Duration::from_secs(2));
            }
        }
    }
    Err("watchdog could not restore".into())
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn recorder_uses_the_proven_settings() {
        let a = recorder_args("HEADLESS-9", 30).join(" ");
        assert!(a.starts_with("-o HEADLESS-9 -D -r 30 -c libx264 -x yuv420p"));
        assert!(
            a.contains("crf=20") && a.contains("maxrate=12000k") && a.contains("bufsize=6000k")
        );
        assert!(a.contains("g=60") && a.contains("keyint=60:min-keyint=60:scenecut=0:aud=1"));
        assert!(a.ends_with("-m h264 -f /dev/stdout"));
    }

    #[test]
    fn tvpipe_gets_raw_h264_on_stdin() {
        assert_eq!(
            tvpipe_args("Bedroom TV", "1920x1080", 30).join("|"),
            "play|--to|Bedroom TV|--no-audio|--latency|300|--raw-video|1920x1080@30:h264|-"
        );
    }
}
