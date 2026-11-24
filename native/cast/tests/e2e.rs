// SPDX-License-Identifier: AGPL-3.0-or-later
//! End to end in a PRIVATE headless sway: two headless outputs stand in for the
//! physical monitors; `wf-recorder` and `tvpipe` are stub scripts first in PATH.
//! Nothing here touches a real session or contacts a receiver.
//! Set TAOS_E2E=1 to make a missing sway a failure instead of a skip.

use serde_json::Value;
use std::fs;
use std::io::{BufRead, BufReader, Write};
use std::os::unix::fs::PermissionsExt;
use std::os::unix::net::UnixListener;
use std::path::{Path, PathBuf};
use std::process::{Child, Command, Stdio};
use std::time::{Duration, Instant};

struct Rig {
    root: PathBuf,
    sway: Child,
    sock: PathBuf,
}

impl Drop for Rig {
    fn drop(&mut self) {
        let _ = self.sway.kill(); // the exact child we started
        let _ = self.sway.wait();
        let _ = fs::remove_dir_all(&self.root);
    }
}

fn wait_until(secs: u64, mut f: impl FnMut() -> bool) -> bool {
    let t0 = Instant::now();
    while t0.elapsed() < Duration::from_secs(secs) {
        if f() {
            return true;
        }
        std::thread::sleep(Duration::from_millis(100));
    }
    false
}

fn script(p: &Path, body: &str) {
    fs::write(p, format!("#!/bin/bash\n{body}\n")).unwrap();
    fs::set_permissions(p, fs::Permissions::from_mode(0o755)).unwrap();
}

fn rig(name: &str) -> Option<Rig> {
    if Command::new("sway").arg("--version").output().is_err() {
        assert!(
            std::env::var("TAOS_E2E").is_err(),
            "TAOS_E2E=1 but sway is missing"
        );
        eprintln!("skipping {name}: sway is not installed");
        return None;
    }
    let root = std::env::temp_dir().join(format!("tcast-{name}-{}", std::process::id()));
    let _ = fs::remove_dir_all(&root);
    let run = root.join("run");
    fs::create_dir_all(root.join("bin")).unwrap();
    fs::create_dir_all(root.join("stub")).unwrap();
    fs::create_dir_all(&run).unwrap();
    fs::set_permissions(&run, fs::Permissions::from_mode(0o700)).unwrap();
    fs::write(root.join("sway.conf"), "").unwrap();
    script(
        &root.join("bin/wf-recorder"),
        "echo $$ > \"$STUB_DIR/rec.pid\"\necho \"$@\" > \"$STUB_DIR/rec.args\"\nprintf 'status line\\n'\nprintf '\\x00\\x00\\x00\\x01\\x09\\xf0\\x00\\x00\\x01\\x65IDR'\nwhile true; do printf '\\x00\\x00\\x00\\x01\\x09\\xf0\\x00\\x00\\x01\\x41PPP'; sleep 0.03; done",
    );
    script(
        &root.join("bin/tvpipe"),
        "echo $$ > \"$STUB_DIR/tv.pid\"\necho \"$@\" > \"$STUB_DIR/tv.args\"\ncat >> \"$STUB_DIR/tv.stdin\"",
    );
    let sway = Command::new("sway")
        .args(["-c"])
        .arg(root.join("sway.conf"))
        .env_clear()
        .env("PATH", std::env::var("PATH").unwrap())
        .env("XDG_RUNTIME_DIR", &run)
        .env("TMPDIR", &root)
        .env("WLR_BACKENDS", "headless")
        .env("WLR_RENDERER", "pixman")
        .env("WLR_LIBINPUT_NO_DEVICES", "1")
        .env("WLR_HEADLESS_OUTPUTS", "2")
        .stdout(Stdio::null())
        .stderr(Stdio::null())
        .spawn()
        .expect("start private sway");
    let mut r = Rig {
        root,
        sway,
        sock: PathBuf::new(),
    };
    let ok = wait_until(15, || {
        let found = fs::read_dir(&run).ok().and_then(|d| {
            d.flatten().map(|e| e.path()).find(|p| {
                p.file_name()
                    .unwrap()
                    .to_string_lossy()
                    .starts_with("sway-ipc")
            })
        });
        if let Some(s) = found {
            r.sock = s;
            return outputs(&r).len() == 2;
        }
        false
    });
    assert!(ok, "private sway did not come up with two outputs");
    Some(r)
}

fn msg(r: &Rig, args: &[&str]) -> String {
    let o = Command::new("swaymsg")
        .args(args)
        .env("SWAYSOCK", &r.sock)
        .output()
        .unwrap();
    String::from_utf8_lossy(&o.stdout).to_string()
}

/// (name, active, width, height)
fn outputs(r: &Rig) -> Vec<(String, bool, u64, u64)> {
    let v: Value =
        serde_json::from_str(&msg(r, &["-t", "get_outputs", "-r"])).unwrap_or(Value::Null);
    v.as_array()
        .map(|a| {
            a.iter()
                .map(|o| {
                    (
                        o["name"].as_str().unwrap().to_string(),
                        o["active"].as_bool().unwrap_or(false),
                        o["current_mode"]["width"].as_u64().unwrap_or(0),
                        o["current_mode"]["height"].as_u64().unwrap_or(0),
                    )
                })
                .collect()
        })
        .unwrap_or_default()
}

fn cast(r: &Rig, args: &[&str]) -> (bool, Value) {
    let path = format!(
        "{}:{}",
        r.root.join("bin").display(),
        std::env::var("PATH").unwrap()
    );
    let o = Command::new(env!("CARGO_BIN_EXE_taos-cast"))
        .arg("--json")
        .args(args)
        .env("PATH", path)
        .env("SWAYSOCK", &r.sock)
        .env("XDG_RUNTIME_DIR", r.root.join("run"))
        .env("TMPDIR", &r.root)
        .env("TAOS_INPUT_CONTROL", r.root.join("control.sock"))
        .env("STUB_DIR", r.root.join("stub"))
        .output()
        .unwrap();
    let out = String::from_utf8_lossy(&o.stdout).to_string();
    let v = serde_json::from_str(out.trim())
        .unwrap_or_else(|_| panic!("not json: {out} {}", String::from_utf8_lossy(&o.stderr)));
    (o.status.success(), v)
}

fn pid_of(r: &Rig, f: &str) -> u32 {
    fs::read_to_string(r.root.join("stub").join(f))
        .unwrap()
        .trim()
        .parse()
        .unwrap()
}
fn pid_alive(p: u32) -> bool {
    fs::read_to_string(format!("/proc/{p}/stat"))
        .map(|s| !s.contains(") Z"))
        .unwrap_or(false)
}

/// A stand-in for the taos-input control socket (answers status and pair).
fn fake_engine(r: &Rig) {
    let l = UnixListener::bind(r.root.join("control.sock")).unwrap();
    std::thread::spawn(move || {
        for s in l.incoming().flatten() {
            let mut line = String::new();
            BufReader::new(&s).read_line(&mut line).unwrap();
            let reply = if line.contains("pair") {
                r#"{"ok":true,"code":"482913","expires_in":300,"url":"http://controller.test:7981/"}"#
            } else {
                r#"{"ok":true,"url":"http://controller.test:7981/"}"#
            };
            let mut s = s;
            writeln!(s, "{reply}").unwrap();
        }
    });
}

fn start(r: &Rig, extra: &[&str]) -> Value {
    let mut a = vec!["start", "--to", "Test TV", "--size", "1280x720"];
    a.extend_from_slice(extra);
    let (ok, v) = cast(r, &a);
    assert!(ok, "start failed: {v}");
    v
}

fn assert_restored(r: &Rig) {
    let o = outputs(r);
    assert_eq!(o.len(), 2, "virtual output removed: {o:?}");
    assert!(o.iter().all(|x| x.1), "monitors active again: {o:?}");
    assert!(
        wait_until(5, || !r.root.join("run/taos-cast/state.json").exists()
            && !r.root.join("run/taos-cast/state.restoring").exists()),
        "state removed"
    );
}

#[test]
fn start_runs_pipeline_and_stop_restores() {
    let Some(r) = rig("a") else { return };
    fake_engine(&r);
    let v = start(&r, &[]);
    assert_eq!(v["status"], "running");
    assert_eq!(v["code"], "482913");
    assert_eq!(v["url"], "http://controller.test:7981/");
    let o = outputs(&r);
    assert_eq!(o.len(), 3);
    let virt = v["output"].as_str().unwrap();
    assert!(
        o.iter()
            .any(|x| x.0 == virt && x.1 && x.2 == 1280 && x.3 == 720),
        "{o:?}"
    );
    assert!(
        o.iter().filter(|x| x.0 != virt).all(|x| !x.1),
        "monitors off: {o:?}"
    );
    let args = fs::read_to_string(r.root.join("stub/tv.args")).unwrap();
    assert_eq!(
        args.trim(),
        "play --to Test TV --no-audio --latency 300 --raw-video 1280x720@30:h264 -"
    );
    assert!(fs::read_to_string(r.root.join("stub/rec.args"))
        .unwrap()
        .starts_with(&format!("-o {virt} -D -r 30")));
    assert!(wait_until(5, || fs::metadata(r.root.join("stub/tv.stdin"))
        .map(|m| m.len() > 100)
        .unwrap_or(false)));
    let sent = fs::read(r.root.join("stub/tv.stdin")).unwrap();
    assert_eq!(&sent[..4], b"SLRW");
    assert_eq!(sent[4], 3, "first record is an encoded frame");
    assert_eq!(cast(&r, &["status"]).1["status"], "running");
    assert!(!cast(&r, &["start", "--to", "x"]).0, "second start refused");

    let (rec, tv) = (pid_of(&r, "rec.pid"), pid_of(&r, "tv.pid"));
    let (ok, v) = cast(&r, &["stop"]);
    assert!(ok, "{v}");
    assert_restored(&r);
    assert!(
        wait_until(3, || !pid_alive(rec) && !pid_alive(tv)),
        "pipeline stopped"
    );
    assert_eq!(cast(&r, &["status"]).1["status"], "idle");
}

#[test]
fn killing_the_supervisor_still_restores() {
    let Some(r) = rig("b") else { return };
    start(&r, &["--no-input"]);
    assert_eq!(outputs(&r).iter().filter(|x| x.1).count(), 1);
    let st: Value =
        serde_json::from_slice(&fs::read(r.root.join("run/taos-cast/state.json")).unwrap())
            .unwrap();
    let sup = st["supervisor"]["pid"].as_u64().unwrap();
    let (rec, tv) = (pid_of(&r, "rec.pid"), pid_of(&r, "tv.pid"));
    assert!(Command::new("kill")
        .args(["-9", &sup.to_string()])
        .status()
        .unwrap()
        .success());
    assert!(
        wait_until(15, || {
            let o = outputs(&r);
            o.len() == 2
                && o.iter().all(|x| x.1)
                && !r.root.join("run/taos-cast/state.json").exists()
        }),
        "watchdog restored: {:?}",
        outputs(&r)
    );
    assert_restored(&r);
    assert!(
        wait_until(5, || !pid_alive(rec) && !pid_alive(tv)),
        "pipeline stopped by the watchdog"
    );
}

#[test]
fn recorder_dying_ends_the_cast_and_restores() {
    let Some(r) = rig("c") else { return };
    start(&r, &["--no-input"]);
    let rec = pid_of(&r, "rec.pid");
    assert!(Command::new("kill")
        .args(["-9", &rec.to_string()])
        .status()
        .unwrap()
        .success());
    assert!(wait_until(15, || {
        let o = outputs(&r);
        o.len() == 2 && o.iter().all(|x| x.1)
    }));
    assert_restored(&r);
    let (_, s) = cast(&r, &["status"]);
    assert_eq!(s["status"], "idle");
    assert!(
        s["last"]["reason"].as_str().unwrap().contains("recorder"),
        "{s}"
    );
}

#[test]
fn failed_start_leaves_the_monitors_alone() {
    let Some(r) = rig("d") else { return };
    let (ok, v) = cast(
        &r,
        &[
            "start",
            "--to",
            "TV",
            "--no-input",
            "--tvpipe",
            "/nonexistent/tvpipe",
        ],
    );
    assert!(!ok, "{v}");
    assert!(wait_until(10, || outputs(&r).len() == 2));
    assert_restored(&r);
}
