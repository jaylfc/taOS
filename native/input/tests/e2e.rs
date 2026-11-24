// SPDX-License-Identifier: AGPL-3.0-or-later
//! End to end: a real engine, a real controller connection, a real (private,
//! headless) sway, and test clients that look at what the compositor
//! delivered. Nothing here touches a real screen, a receiver or the person's
//! own session: every process gets its own runtime directory under the
//! system temp dir and a pixman renderer (no GPU is opened).
//!
//! Needs `sway`, `swaymsg` and (for the typing tests) `foot`. Set
//! TAOS_E2E=1 to make a missing tool a failure instead of a skip; CI does.

use serde_json::{json, Value};
use socket2::{Domain, Socket, Type};
use std::fs;
use std::io::{BufRead, BufReader, Read, Write};
use std::net::{IpAddr, SocketAddr, TcpStream};
use std::os::unix::fs::PermissionsExt;
use std::path::{Path, PathBuf};
use std::process::{Child, Command, Stdio};
use std::time::{Duration, Instant, SystemTime, UNIX_EPOCH};
use taos_input::client::{self, Conn, Creds};

const ENGINE: &str = env!("CARGO_BIN_EXE_taos-input");

fn have(tool: &str) -> bool {
    Command::new(tool)
        .arg("--version")
        .stdout(Stdio::null())
        .stderr(Stdio::null())
        .status()
        .is_ok()
}

/// True when the tools are there; a failure when TAOS_E2E=1 says they must be.
fn need(tools: &[&str], test: &str) -> bool {
    for t in tools {
        if !have(t) {
            assert!(
                std::env::var("TAOS_E2E").map(|v| v != "1").unwrap_or(true),
                "TAOS_E2E=1 but {t} is missing"
            );
            eprintln!("skipping {test}: {t} is not installed");
            return false;
        }
    }
    true
}

fn wait_until(secs: u64, mut f: impl FnMut() -> bool) -> bool {
    let end = Instant::now() + Duration::from_secs(secs);
    while Instant::now() < end {
        if f() {
            return true;
        }
        std::thread::sleep(Duration::from_millis(25));
    }
    f()
}

fn now_us() -> u64 {
    SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .unwrap()
        .as_micros() as u64
}

struct Env {
    root: PathBuf,
    rt: PathBuf,
    children: Vec<Child>,
    display: std::cell::RefCell<String>,
}

impl Env {
    fn new(name: &str) -> Env {
        let root =
            std::env::temp_dir().join(format!("taos-input-e2e-{}-{name}", std::process::id()));
        let _ = fs::remove_dir_all(&root);
        let rt = root.join("rt");
        fs::create_dir_all(&rt).unwrap();
        fs::set_permissions(&rt, fs::Permissions::from_mode(0o700)).unwrap();
        for d in ["home", "state", "cfg", "data", "cache", "tmp"] {
            fs::create_dir_all(root.join(d)).unwrap();
        }
        Env {
            root,
            rt,
            children: Vec::new(),
            display: std::cell::RefCell::new("wayland-0".into()),
        }
    }

    fn cmd(&self, prog: impl AsRef<std::ffi::OsStr>) -> Command {
        let mut c = Command::new(prog);
        c.env_clear()
            .env("PATH", std::env::var_os("PATH").unwrap_or_default())
            .env("HOME", self.root.join("home"))
            .env("XDG_RUNTIME_DIR", &self.rt)
            .env("XDG_STATE_HOME", self.root.join("state"))
            .env("XDG_CONFIG_HOME", self.root.join("cfg"))
            .env("XDG_DATA_HOME", self.root.join("data"))
            .env("XDG_CACHE_HOME", self.root.join("cache"))
            .env("TMPDIR", self.root.join("tmp"))
            .env("LANG", "C.UTF-8")
            .env("WAYLAND_DISPLAY", self.display.borrow().as_str());
        if let Some(s) = self.sway_sock() {
            c.env("SWAYSOCK", s);
        }
        c
    }

    fn sway_sock(&self) -> Option<PathBuf> {
        let mut found: Vec<_> = fs::read_dir(&self.rt)
            .ok()?
            .filter_map(|e| e.ok())
            .filter(|e| e.file_name().to_string_lossy().starts_with("sway-ipc."))
            .filter_map(|e| Some((e.metadata().ok()?.modified().ok()?, e.path())))
            .collect();
        found.sort();
        found.pop().map(|(_, p)| p)
    }

    /// Start a private headless sway and return the exact child.
    fn start_sway(&mut self) -> Sway {
        assert!(
            self.rt.starts_with(std::env::temp_dir()),
            "never run against a real runtime dir"
        );
        let conf = self.root.join("sway.conf");
        fs::write(
            &conf,
            format!(
                "xwayland disable\ndefault_border none\nbindsym Mod4+F9 exec touch {r}/super-ok\n\
                 bindsym Mod4+F10 exec touch {r}/super-sticky\nfor_window [app_id=\"taos-probe\"] fullscreen enable\n",
                r = self.root.display()
            ),
        )
        .unwrap();
        // The previous compositor's socket files may still be there.
        for e in fs::read_dir(&self.rt).unwrap().flatten() {
            let n = e.file_name().to_string_lossy().to_string();
            if n.starts_with("sway-ipc.") {
                let _ = fs::remove_file(e.path());
            }
        }
        let mut c = self.cmd("sway");
        c.env_remove("SWAYSOCK")
            .env_remove("WAYLAND_DISPLAY")
            .env("WLR_BACKENDS", "headless")
            .env("WLR_RENDERER", "pixman")
            .env("WLR_LIBINPUT_NO_DEVICES", "1")
            .arg("-c")
            .arg(&conf)
            .stdout(Stdio::null())
            .stderr(Stdio::null());
        let guard = Sway(Some(c.spawn().expect("start private sway")));
        let rt = self.rt.clone();
        let display = || -> Option<String> {
            fs::read_dir(&rt)
                .ok()?
                .flatten()
                .map(|e| e.file_name().to_string_lossy().to_string())
                .find(|n| {
                    n.strip_prefix("wayland-")
                        .map(|d| !d.is_empty() && d.bytes().all(|b| b.is_ascii_digit()))
                        .unwrap_or(false)
                })
        };
        let ok = wait_until(20, || {
            display().is_some()
                && fs::read_dir(&rt)
                    .unwrap()
                    .flatten()
                    .any(|e| e.file_name().to_string_lossy().starts_with("sway-ipc."))
        });
        assert!(ok, "private sway did not come up");
        *self.display.borrow_mut() = display().unwrap();
        assert!(
            wait_until(10, || self
                .swaymsg(&["-t", "get_outputs", "-r"])
                .contains("HEADLESS")),
            "no headless output"
        );
        guard
    }

    fn swaymsg(&self, args: &[&str]) -> String {
        let o = self.cmd("swaymsg").args(args).output().expect("swaymsg");
        String::from_utf8_lossy(&o.stdout).to_string()
    }

    fn spawn_tracked(&mut self, mut c: Command) -> usize {
        let child = c.spawn().expect("spawn");
        self.children.push(child);
        self.children.len() - 1
    }
}

impl Drop for Env {
    fn drop(&mut self) {
        for c in self.children.iter_mut() {
            let _ = c.kill(); // exact children we started
            let _ = c.wait();
        }
        let _ = fs::remove_dir_all(&self.root);
    }
}

struct Sway(Option<Child>);
impl Drop for Sway {
    fn drop(&mut self) {
        if let Some(c) = self.0.as_mut() {
            let _ = c.kill();
            let _ = c.wait();
        }
    }
}

struct Engine {
    child: Child,
    port: u16,
    control: PathBuf,
}

impl Drop for Engine {
    fn drop(&mut self) {
        let _ = self.child.kill();
        let _ = self.child.wait();
    }
}

fn start_engine(env: &Env, extra: &[&str]) -> Engine {
    let control = env.root.join("ctl.sock");
    let mut c = env.cmd(ENGINE);
    c.args([
        "serve",
        "--bind",
        "127.0.0.1",
        "--port",
        "0",
        "--allow-net",
        "127.0.0.1/32",
        "--control",
    ])
    .arg(&control)
    .arg("--state-dir")
    .arg(env.root.join("engine-state"))
    .args(extra)
    .stderr(Stdio::piped());
    let mut child = c.spawn().expect("start engine");
    let mut line = String::new();
    let mut err = BufReader::new(child.stderr.take().unwrap());
    err.read_line(&mut line).expect("engine banner");
    // Keep reading so the engine never sees a closed stderr; the rest is its log.
    let log = env.root.join("engine.log");
    std::thread::spawn(move || {
        let mut f = fs::File::create(log).ok();
        for l in err.lines().map_while(Result::ok) {
            if let Some(f) = f.as_mut() {
                let _ = writeln!(f, "{l}");
            }
        }
    });
    let port: u16 = line
        .split("http://127.0.0.1:")
        .nth(1)
        .and_then(|r| r.split('/').next())
        .and_then(|p| p.parse().ok())
        .unwrap_or_else(|| panic!("no port in banner: {line:?}"));
    Engine {
        child,
        port,
        control,
    }
}

fn ctl(e: &Engine, req: Value) -> Value {
    taos_input::control::request(&e.control, &req).expect("control request")
}

fn http(
    port: u16,
    method: &str,
    path: &str,
    body: &str,
    src: Option<IpAddr>,
) -> Result<(u16, String), String> {
    http_with(
        port,
        method,
        path,
        body,
        src,
        &[("Content-Type", "application/json")],
    )
}

fn http_with(
    port: u16,
    method: &str,
    path: &str,
    body: &str,
    src: Option<IpAddr>,
    extra: &[(&str, &str)],
) -> Result<(u16, String), String> {
    let mut s = connect_from(port, src).map_err(|e| e.to_string())?;
    s.set_read_timeout(Some(Duration::from_secs(5))).ok();
    let mut head = format!(
        "{method} {path} HTTP/1.1\r\nConnection: close\r\nContent-Length: {}\r\n",
        body.len()
    );
    if !extra.iter().any(|(k, _)| k.eq_ignore_ascii_case("host")) {
        head.push_str(&format!("Host: 127.0.0.1:{port}\r\n"));
    }
    for (k, v) in extra {
        head.push_str(&format!("{k}: {v}\r\n"));
    }
    write!(s, "{head}\r\n{body}").map_err(|e| e.to_string())?;
    let mut raw = String::new();
    s.read_to_string(&mut raw).map_err(|e| e.to_string())?;
    let status = raw
        .split(' ')
        .nth(1)
        .and_then(|c| c.parse().ok())
        .ok_or_else(|| format!("no status in {raw:?}"))?;
    Ok((
        status,
        raw.split("\r\n\r\n").nth(1).unwrap_or("").to_string(),
    ))
}

fn connect_from(port: u16, src: Option<IpAddr>) -> std::io::Result<TcpStream> {
    let sock = Socket::new(Domain::IPV4, Type::STREAM, None)?;
    if let Some(ip) = src {
        sock.bind(&SocketAddr::new(ip, 0).into())?;
    }
    sock.connect(&SocketAddr::new("127.0.0.1".parse().unwrap(), port).into())?;
    Ok(sock.into())
}

/// Mint a code through the control socket and pair as a phone.
fn pair(e: &Engine, name: &str) -> Creds {
    pair_from(e, name, None)
}

fn pair_from(e: &Engine, name: &str, src: Option<IpAddr>) -> Creds {
    let code = ctl(e, json!({"op": "pair"}))["code"]
        .as_str()
        .unwrap()
        .to_string();
    pair_code(e, &code, name, src).expect("pairing")
}

fn pair_code(e: &Engine, code: &str, name: &str, src: Option<IpAddr>) -> Result<Creds, String> {
    let port = e.port;
    client::pair(
        || connect_from(port, src),
        &format!("127.0.0.1:{port}"),
        code,
        name,
    )
}

type Ws = Conn;

fn ws_try(
    port: u16,
    creds: &Creds,
    origin: Option<&str>,
    src: Option<IpAddr>,
) -> Result<Ws, String> {
    let stream = connect_from(port, src).map_err(|e| e.to_string())?;
    client::connect(stream, &format!("127.0.0.1:{port}"), origin, creds)
        .map_err(|e| format!("{e:?}"))
}

/// An authenticated connection: the engine has answered "hello" over the channel.
fn ws(port: u16, creds: &Creds) -> Ws {
    let mut c = ws_try(port, creds, None, None).expect("handshake");
    assert_eq!(c.recv(3000).expect("hello")["t"], "hello");
    c
}

/// Did the engine accept this credential? (It sends "hello" only then.)
fn accepted(port: u16, creds: &Creds) -> bool {
    match ws_try(port, creds, None, None) {
        Ok(mut c) => c.recv(2000).map(|m| m["t"] == "hello").unwrap_or(false),
        Err(_) => false,
    }
}

fn send(ws: &mut Ws, v: Value) {
    ws.send(&v).expect("send");
}

fn recv(ws: &mut Ws, ms: u64) -> Option<Value> {
    ws.recv(ms)
}

/// Page clock for touch events.
struct Clock(Instant);
impl Clock {
    fn ts(&self) -> f64 {
        self.0.elapsed().as_secs_f64() * 1000.0
    }
}

fn touch(ws: &mut Ws, c: &Clock, p: &str, i: u32, x: f64, y: f64) {
    send(
        ws,
        json!({"t": "touch", "p": p, "i": i, "x": x, "y": y, "ts": c.ts()}),
    );
}

struct Probe {
    log: PathBuf,
}

impl Probe {
    fn start(env: &mut Env, name: &str) -> Probe {
        let log = env.root.join(format!("{name}.log"));
        // Examples are built next to the binaries by `cargo test`.
        let probe = Path::new(ENGINE)
            .parent()
            .unwrap()
            .join("examples")
            .join("probe");
        assert!(
            probe.exists(),
            "build the probe first: cargo build --examples ({probe:?})"
        );
        let mut c = env.cmd(&probe);
        c.arg(&log).stdout(Stdio::null()).stderr(Stdio::null());
        env.spawn_tracked(c);
        let p = Probe { log };
        assert!(
            wait_until(20, || p.events().iter().any(|e| e["e"] == "ready")),
            "probe never became ready"
        );
        p
    }

    fn events(&self) -> Vec<Value> {
        fs::read_to_string(&self.log)
            .unwrap_or_default()
            .lines()
            .filter_map(|l| serde_json::from_str(l).ok())
            .collect()
    }

    fn last_pos(&self) -> Option<(f64, f64)> {
        self.events()
            .iter()
            .rev()
            .find(|e| e["e"] == "motion" || e["e"] == "enter")
            .map(|e| (e["x"].as_f64().unwrap(), e["y"].as_f64().unwrap()))
    }

    fn buttons(&self) -> Vec<(u64, bool)> {
        self.events()
            .iter()
            .filter(|e| e["e"] == "button")
            .map(|e| (e["b"].as_u64().unwrap(), e["down"].as_bool().unwrap()))
            .collect()
    }
}

/// Wait until the engine is connected to a compositor.
fn wait_compositor(e: &Engine, up: bool) {
    assert!(
        wait_until(15, || ctl(e, json!({"op": "status"}))["compositor"]
            == json!(up)),
        "compositor state never became {up}: {}",
        ctl(e, json!({"op": "status"}))
    );
}

/// Move the pointer a little so the probe has an entered, known position.
fn settle(ws: &mut Ws, probe: &Probe) -> (f64, f64) {
    // The window may not have pointer focus yet: nudge until it reports motion.
    let end = Instant::now() + Duration::from_secs(15);
    let mut sign = 1.0;
    while !probe.events().iter().any(|e| e["e"] == "motion") {
        assert!(
            Instant::now() < end,
            "pointer never reached the probe window"
        );
        send(ws, json!({"t": "ptr", "dx": 3.0 * sign, "dy": 3.0 * sign}));
        sign = -sign;
        std::thread::sleep(Duration::from_millis(200));
    }
    std::thread::sleep(Duration::from_millis(200));
    probe.last_pos().unwrap()
}

fn near(a: (f64, f64), b: (f64, f64), tol: f64) -> bool {
    (a.0 - b.0).abs() <= tol && (a.1 - b.1).abs() <= tol
}

#[test]
fn pointer_motion_buttons_and_scroll_arrive() {
    if !need(&["sway", "swaymsg"], "pointer") {
        return;
    }
    let mut env = Env::new("pointer");
    let _sway = env.start_sway();
    let eng = start_engine(&env, &[]);
    let probe = Probe::start(&mut env, "probe");
    wait_compositor(&eng, true);
    let creds = pair(&eng, "Test phone");
    let mut ws = ws(eng.port, &creds);

    // Relative motion lands exactly where it was told to.
    let base = settle(&mut ws, &probe);
    send(&mut ws, json!({"t": "ptr", "dx": 50, "dy": -20}));
    assert!(
        wait_until(5, || probe
            .last_pos()
            .map(|p| near(p, (base.0 + 50.0, base.1 - 20.0), 0.5))
            .unwrap_or(false)),
        "relative motion: expected {:?}, at {:?}",
        (base.0 + 50.0, base.1 - 20.0),
        probe.last_pos()
    );

    // Absolute position: a quarter across, half down, of the 1280x720 output.
    send(&mut ws, json!({"t": "abs", "x": 0.25, "y": 0.5}));
    assert!(
        wait_until(5, || probe
            .last_pos()
            .map(|p| near(p, (320.0, 360.0), 2.0))
            .unwrap_or(false)),
        "absolute motion at {:?}",
        probe.last_pos()
    );

    // Plain buttons.
    send(&mut ws, json!({"t": "btn", "b": "left", "d": true}));
    send(&mut ws, json!({"t": "btn", "b": "left", "d": false}));
    send(&mut ws, json!({"t": "btn", "b": "right", "d": true}));
    send(&mut ws, json!({"t": "btn", "b": "right", "d": false}));
    assert!(wait_until(5, || probe.buttons().len() >= 4));
    assert_eq!(
        probe.buttons(),
        vec![(0x110, true), (0x110, false), (0x111, true), (0x111, false)]
    );

    // A tap gesture is a left click (page clock decides, not arrival time).
    let c = Clock(Instant::now());
    touch(&mut ws, &c, "down", 0, 100.0, 100.0);
    std::thread::sleep(Duration::from_millis(60));
    touch(&mut ws, &c, "up", 0, 100.0, 100.0);
    assert!(wait_until(5, || probe.buttons().len() >= 6));
    assert_eq!(&probe.buttons()[4..], &[(0x110, true), (0x110, false)]);
    assert_eq!(
        recv(&mut ws, 1000).unwrap()["k"],
        "tap",
        "haptic cue goes back to the phone"
    );

    // Two finger tap is a right click.
    let n = probe.buttons().len();
    touch(&mut ws, &c, "down", 0, 100.0, 100.0);
    touch(&mut ws, &c, "down", 1, 140.0, 100.0);
    std::thread::sleep(Duration::from_millis(60));
    touch(&mut ws, &c, "up", 0, 100.0, 100.0);
    touch(&mut ws, &c, "up", 1, 140.0, 100.0);
    assert!(wait_until(5, || probe.buttons().len() >= n + 2));
    assert_eq!(&probe.buttons()[n..n + 2], &[(0x111, true), (0x111, false)]);

    // Two finger drag upward scrolls the page down, smoothly, and stops.
    for step in 0..8 {
        let y = 300.0 - 12.0 * step as f64;
        touch(
            &mut ws,
            &c,
            if step == 0 { "down" } else { "move" },
            0,
            100.0,
            y,
        );
        touch(
            &mut ws,
            &c,
            if step == 0 { "down" } else { "move" },
            1,
            140.0,
            y,
        );
        std::thread::sleep(Duration::from_millis(16));
    }
    touch(&mut ws, &c, "up", 0, 100.0, 210.0);
    touch(&mut ws, &c, "up", 1, 140.0, 210.0);
    assert!(
        wait_until(5, || probe
            .events()
            .iter()
            .filter(|e| e["e"] == "axis")
            .count()
            >= 3),
        "no smooth scroll arrived"
    );
    let axes: Vec<f64> = probe
        .events()
        .iter()
        .filter(|e| e["e"] == "axis" && e["v"] == true)
        .map(|e| e["value"].as_f64().unwrap())
        .collect();
    assert!(
        axes.len() >= 3,
        "scroll is smooth (several events): {axes:?}"
    );
    assert!(
        axes.iter().sum::<f64>() > 30.0,
        "natural direction: fingers up, page down: {axes:?}"
    );

    // Tap and hold, then move: a drag (button held while the pointer moves).
    let n = probe.buttons().len();
    let before = probe.last_pos().unwrap();
    touch(&mut ws, &c, "down", 0, 200.0, 200.0);
    assert!(
        wait_until(5, || probe.buttons().len() > n),
        "hold never started the drag: {:?}",
        probe.events().iter().rev().take(12).collect::<Vec<_>>()
    );
    assert_eq!(probe.buttons()[n], (0x110, true));
    for i in 1..=5 {
        touch(&mut ws, &c, "move", 0, 200.0 + 10.0 * i as f64, 200.0);
        std::thread::sleep(Duration::from_millis(10));
    }
    touch(&mut ws, &c, "up", 0, 250.0, 200.0);
    assert!(wait_until(5, || probe.buttons().len() > n + 1));
    assert_eq!(probe.buttons()[n + 1], (0x110, false));
    let after = probe.last_pos().unwrap();
    assert!(
        after.0 > before.0 + 50.0,
        "the pointer moved while dragging: {before:?} -> {after:?}"
    );
}

#[test]
fn text_arrives_exactly_including_unicode_and_shortcuts_work() {
    if !need(&["sway", "swaymsg", "foot"], "typing") {
        return;
    }
    let mut env = Env::new("typing");
    let _sway = env.start_sway();
    let eng = start_engine(&env, &[]);
    wait_compositor(&eng, true);
    let out = env.root.join("typed.txt");
    let done = env.root.join("typed.done");
    let mut foot = env.cmd("foot");
    foot.args(["-a", "taos-test-foot", "--", "sh", "-c"])
        .arg(format!("cat > {}; touch {}", out.display(), done.display()));
    env.spawn_tracked(foot);
    assert!(
        wait_until(20, || {
            let t: Value = serde_json::from_str(&env.swaymsg(&["-t", "get_tree", "-r"]))
                .unwrap_or(Value::Null);
            focused_app(&t).as_deref() == Some("taos-test-foot")
        }),
        "terminal never got focus"
    );
    let creds = pair(&eng, "Test phone");
    let mut ws = ws(eng.port, &creds);
    let lines = [
        "Hello, World! h\u{e9}llo w\u{f6}rld \u{f1} \u{65e5}\u{672c}\u{8a9e} \u{1f600} \u{df} \u{20ac}\n",
        "ABC xyz 0123456789 !@#$%^&*()_+-=[]{}|;':\",./<>?`~\\\n",
        "tab\there caf\u{e9} \u{1f600}\u{1f600} \u{65e5}\n",
    ];
    for l in lines {
        send(&mut ws, json!({"t": "text", "s": l}));
    }
    // Ctrl+D at the start of a line ends cat: proves the Control modifier.
    send(&mut ws, json!({"t": "key", "k": "d", "m": ["ctrl"]}));
    assert!(
        wait_until(20, || done.exists()),
        "cat never ended (typed so far: {:?})",
        fs::read_to_string(&out)
    );
    assert_eq!(fs::read_to_string(&out).unwrap(), lines.concat());
}

fn focused_app(n: &Value) -> Option<String> {
    if n["focused"] == true {
        if let Some(a) = n["app_id"].as_str() {
            return Some(a.to_string());
        }
    }
    for k in ["nodes", "floating_nodes"] {
        for c in n[k].as_array().into_iter().flatten() {
            if let Some(a) = focused_app(c) {
                return Some(a);
            }
        }
    }
    None
}

#[test]
fn super_bindings_fire_with_a_tap_combo_and_with_a_sticky_modifier() {
    if !need(&["sway", "swaymsg"], "super") {
        return;
    }
    let mut env = Env::new("super");
    let _sway = env.start_sway();
    let eng = start_engine(&env, &[]);
    wait_compositor(&eng, true);
    let creds = pair(&eng, "Test phone");
    let mut ws = ws(eng.port, &creds);
    send(&mut ws, json!({"t": "key", "k": "F9", "m": ["super"]}));
    assert!(
        wait_until(10, || env.root.join("super-ok").exists()),
        "Super+F9 did not reach the compositor"
    );
    send(&mut ws, json!({"t": "mods", "m": ["super"]}));
    send(&mut ws, json!({"t": "key", "k": "F10"}));
    send(&mut ws, json!({"t": "mods", "m": []}));
    assert!(
        wait_until(10, || env.root.join("super-sticky").exists()),
        "sticky Super then F10 did not fire"
    );
}

#[test]
fn nothing_is_accepted_without_valid_credentials_from_a_lan_peer() {
    let env = Env::new("auth");
    let eng = start_engine(&env, &["--allow-net", "127.0.0.2/32"]);
    let host = format!("127.0.0.1:{}", eng.port);

    // No pairing code has been minted: pairing is refused before it starts.
    assert!(pair_code(&eng, "123456", "x", None).is_err());

    // Pair for real, then try credentials that are well formed but wrong.
    let good = pair(&eng, "Phone");
    assert!(accepted(eng.port, &good));
    let impostor = Creds {
        id: good.id,
        secret: taos_input::secure::new_secret(),
        engine_pub: good.engine_pub,
    };
    assert!(
        !accepted(eng.port, &impostor),
        "right device id, wrong device secret"
    );
    let stranger_id = Creds {
        id: [9; 6],
        secret: taos_input::secure::new_secret(),
        engine_pub: good.engine_pub,
    };
    assert!(!accepted(eng.port, &stranger_id), "unknown device id");
    // A phone that pinned some other engine's key refuses to talk to this one.
    let other_engine = Creds {
        id: good.id,
        secret: good.secret.clone(),
        engine_pub: taos_input::secure::public_bytes(&taos_input::secure::new_secret()),
    };
    assert!(
        ws_try(eng.port, &other_engine, None, None).is_err(),
        "phone must refuse an engine it did not pair with"
    );

    // Frames sent before the handshake completes do nothing and end the connection.
    let stream = connect_from(eng.port, None).unwrap();
    let mut req =
        tungstenite::client::IntoClientRequest::into_client_request(format!("ws://{host}/ws"))
            .unwrap();
    req.headers_mut()
        .insert("Sec-WebSocket-Protocol", "taos-input.v2".parse().unwrap());
    let (mut raw, _) = tungstenite::client(req, stream).unwrap();
    let _ = raw.send(tungstenite::Message::text(r#"{"t":"ptr","dx":5,"dy":5}"#));
    raw.get_mut()
        .set_read_timeout(Some(Duration::from_secs(3)))
        .ok();
    assert!(
        !matches!(raw.read(), Ok(tungstenite::Message::Binary(_))),
        "no reply to an unauthenticated message"
    );

    // A wrong code never pairs; guesses are counted per source address.
    let code = ctl(&eng, json!({"op": "pair"}))["code"]
        .as_str()
        .unwrap()
        .to_string();
    let wrong = if code == "000000" { "000001" } else { "000000" };
    let attacker: IpAddr = "127.0.0.2".parse().unwrap();
    for _ in 0..3 {
        assert!(pair_code(&eng, wrong, "x", Some(attacker)).is_err());
    }
    let err = pair_code(&eng, &code, "x", Some(attacker)).err().unwrap();
    assert!(
        err.contains("too many"),
        "the attacker is cut off for this code: {err}"
    );
    // The owner still pairs with the very same code (the attacker did not burn it).
    let owner = pair_code(&eng, &code, "Owner", None).expect("owner pairs");
    assert!(accepted(eng.port, &owner));
    assert!(
        pair_code(&eng, &code, "again", None).is_err(),
        "a code works once"
    );

    // HTTP hygiene on the pairing endpoint: JSON only, own Origin only, own Host only.
    let b = json!({}).to_string();
    let code2 = ctl(&eng, json!({"op": "pair"}))["code"]
        .as_str()
        .unwrap()
        .to_string();
    assert_eq!(
        http_with(
            eng.port,
            "POST",
            "/api/pair/begin",
            &b,
            None,
            &[("Content-Type", "text/plain")]
        )
        .unwrap()
        .0,
        400
    );
    assert_eq!(
        http_with(
            eng.port,
            "POST",
            "/api/pair/begin",
            &b,
            None,
            &[
                ("Content-Type", "application/json"),
                ("Origin", "http://evil.example")
            ]
        )
        .unwrap()
        .0,
        403
    );
    assert_eq!(
        http_with(
            eng.port,
            "POST",
            "/api/pair/begin",
            &b,
            None,
            &[
                ("Content-Type", "application/json"),
                ("Host", "evil.example")
            ]
        )
        .unwrap()
        .0,
        421,
        "DNS rebinding: a request addressed to another name gets nothing"
    );
    assert_eq!(
        http_with(eng.port, "GET", "/", "", None, &[("Host", "evil.example")])
            .unwrap()
            .0,
        421
    );
    assert_eq!(
        http_with(
            eng.port,
            "POST",
            "/api/pair/begin",
            &b,
            None,
            &[
                ("Content-Type", "application/json"),
                ("Origin", &format!("http://{host}"))
            ]
        )
        .unwrap()
        .0,
        200
    );
    let _ = code2;

    // WebSocket: another site's page and a rebound host are refused.
    assert!(ws_try(eng.port, &owner, Some("http://evil.example"), None).is_err());
    let own_origin = format!("http://{host}");
    assert!(ws_try(eng.port, &owner, Some(&own_origin), None).is_ok());

    // A peer outside the allowed networks gets nothing, even with good credentials.
    let outsider: IpAddr = "127.0.0.3".parse().unwrap();
    assert!(ws_try(eng.port, &owner, None, Some(outsider)).is_err());
    assert!(http(eng.port, "GET", "/", "", Some(outsider)).is_err());
    assert_eq!(http(eng.port, "GET", "/", "", None).unwrap().0, 200);

    // Revoking cuts the device off, including a live connection.
    let mut live = ws(eng.port, &owner);
    let devices = ctl(&eng, json!({"op": "devices"}));
    let id = taos_input::auth::hex(&owner.id);
    assert!(devices["devices"]
        .as_array()
        .unwrap()
        .iter()
        .any(|d| d["id"] == id.as_str()));
    assert_eq!(ctl(&eng, json!({"op": "revoke", "id": id}))["removed"], 1);
    assert!(
        wait_until(6, || {
            let _ = live.send(&json!({"t": "ping", "n": 1}));
            live.recv(150).is_none() && live.send(&json!({"t": "ping", "n": 2})).is_err()
        }),
        "revoked device stayed connected"
    );
    assert!(!accepted(eng.port, &owner));
}

#[test]
fn session_attacks_are_refused_and_inject_nothing() {
    if !need(&["sway", "swaymsg"], "session attacks") {
        return;
    }
    let mut env = Env::new("attacks");
    let _sway = env.start_sway();
    let eng = start_engine(&env, &[]);
    let probe = Probe::start(&mut env, "probe");
    wait_compositor(&eng, true);
    let creds = pair(&eng, "Phone");
    let mut ok = ws(eng.port, &creds);
    let base = settle(&mut ok, &probe);
    let motions = |p: &Probe| p.events().iter().filter(|e| e["e"] == "motion").count();
    let closed = |c: &mut Ws| {
        wait_until(6, || {
            c.recv(150).is_none() && c.send(&json!({"t": "ping", "n": 1})).is_err()
        })
    };
    drop(ok);

    // Control: a genuine frame moves the pointer exactly once.
    let mut c = ws(eng.port, &creds);
    send(&mut c, json!({"t": "ptr", "dx": 10, "dy": 0}));
    assert!(wait_until(5, || probe
        .last_pos()
        .map(|p| near(p, (base.0 + 10.0, base.1), 0.5))
        .unwrap_or(false)));
    let n = motions(&probe);

    // Replay: the same bytes again. Refused, and no second motion.
    let frame = c
        .chan
        .seal(
            json!({"t": "ptr", "dx": 10, "dy": 0})
                .to_string()
                .as_bytes(),
        )
        .unwrap();
    c.ws.send(tungstenite::Message::binary(frame.clone()))
        .unwrap();
    assert!(wait_until(5, || motions(&probe) > n));
    let n = motions(&probe);
    c.ws.send(tungstenite::Message::binary(frame)).unwrap();
    assert!(closed(&mut c), "replayed frame must end the connection");
    std::thread::sleep(Duration::from_millis(300));
    assert_eq!(motions(&probe), n, "the replay moved the pointer");

    // Tampered ciphertext: one bit flipped. Refused, no motion.
    let mut c = ws(eng.port, &creds);
    let mut frame = c
        .chan
        .seal(
            json!({"t": "ptr", "dx": 77, "dy": 0})
                .to_string()
                .as_bytes(),
        )
        .unwrap();
    let last = frame.len() - 1;
    frame[last] ^= 1;
    c.ws.send(tungstenite::Message::binary(frame)).unwrap();
    assert!(closed(&mut c), "tampered frame must end the connection");

    // Reordered counter: frame 1 before frame 0. Refused, neither moves the pointer.
    let mut c = ws(eng.port, &creds);
    let f0 = c
        .chan
        .seal(
            json!({"t": "ptr", "dx": 55, "dy": 0})
                .to_string()
                .as_bytes(),
        )
        .unwrap();
    let f1 = c
        .chan
        .seal(
            json!({"t": "ptr", "dx": 55, "dy": 0})
                .to_string()
                .as_bytes(),
        )
        .unwrap();
    c.ws.send(tungstenite::Message::binary(f1)).unwrap();
    assert!(closed(&mut c), "out of order frame must end the connection");
    let _ = f0;

    // A plaintext JSON text frame on an authenticated connection is refused.
    let mut c = ws(eng.port, &creds);
    c.ws.send(tungstenite::Message::text(r#"{"t":"ptr","dx":99,"dy":0}"#))
        .unwrap();
    assert!(closed(&mut c));

    std::thread::sleep(Duration::from_millis(400));
    assert_eq!(motions(&probe), n, "no attack moved the pointer");
    let p = probe.last_pos().unwrap();
    assert!(
        near(p, (base.0 + 20.0, base.1), 0.5),
        "the pointer only made the two genuine moves: {p:?}"
    );
}

#[test]
fn the_engine_refuses_to_listen_where_it_should_not() {
    let env = Env::new("bind");
    for bad in ["0.0.0.0", "::", "8.8.8.8", "224.0.0.1"] {
        let o = env
            .cmd(ENGINE)
            .args(["serve", "--bind", bad, "--port", "0", "--control"])
            .arg(env.root.join("c.sock"))
            .arg("--state-dir")
            .arg(env.root.join("s"))
            .output()
            .unwrap();
        assert!(!o.status.success(), "{bad} must be refused");
        assert!(
            String::from_utf8_lossy(&o.stderr).contains("refusing"),
            "{bad}: {}",
            String::from_utf8_lossy(&o.stderr)
        );
    }
    // Loopback is not a home network address unless named explicitly.
    let o = env
        .cmd(ENGINE)
        .args(["serve", "--bind", "127.0.0.1", "--port", "0", "--control"])
        .arg(env.root.join("c.sock"))
        .output()
        .unwrap();
    assert!(!o.status.success());
}

#[test]
fn a_dropped_connection_releases_held_buttons_and_modifiers() {
    if !need(&["sway", "swaymsg"], "release") {
        return;
    }
    let mut env = Env::new("release");
    let _sway = env.start_sway();
    let eng = start_engine(&env, &[]);
    let probe = Probe::start(&mut env, "probe");
    wait_compositor(&eng, true);
    let creds = pair(&eng, "Test phone");
    let mut ws = ws(eng.port, &creds);
    settle(&mut ws, &probe);
    send(&mut ws, json!({"t": "btn", "b": "left", "d": true}));
    send(&mut ws, json!({"t": "mods", "m": ["ctrl"]}));
    assert!(wait_until(5, || probe.buttons() == vec![(0x110, true)]));
    assert!(
        wait_until(5, || probe
            .events()
            .iter()
            .any(|e| e["e"] == "mods" && e["depressed"] == 4)),
        "control not held"
    );
    // The phone goes away without saying goodbye.
    let stream = ws.ws.get_ref().try_clone().unwrap();
    drop(ws);
    stream.shutdown(std::net::Shutdown::Both).ok();
    assert!(
        wait_until(10, || probe.buttons()
            == vec![(0x110, true), (0x110, false)]),
        "button stayed held: {:?}",
        probe.buttons()
    );
    let last_mods = probe
        .events()
        .iter()
        .rev()
        .find(|e| e["e"] == "mods")
        .map(|e| e["depressed"].as_u64().unwrap());
    assert_eq!(last_mods, Some(0), "modifier stayed held");
}

#[test]
fn a_compositor_restart_is_survived() {
    if !need(&["sway", "swaymsg"], "restart") {
        return;
    }
    let mut env = Env::new("restart");
    let mut sway = env.start_sway();
    let eng = start_engine(&env, &[]);
    wait_compositor(&eng, true);
    let creds = pair(&eng, "Test phone");
    let mut ws = ws(eng.port, &creds);
    send(&mut ws, json!({"t": "btn", "b": "left", "d": true}));
    send(&mut ws, json!({"t": "mods", "m": ["ctrl"]}));

    // The compositor dies (the exact process we started).
    let mut dead = sway.0.take().unwrap();
    dead.kill().unwrap();
    dead.wait().unwrap();
    wait_compositor(&eng, false);
    // The controller connection stays up and is told.
    let mut saw_down = false;
    for _ in 0..50 {
        match recv(&mut ws, 100) {
            Some(m) if m["t"] == "state" && m["compositor"] == false => {
                saw_down = true;
                break;
            }
            _ => {}
        }
    }
    assert!(saw_down, "the phone was not told the compositor is gone");
    // Input sent while it is gone is dropped, not queued for later.
    send(&mut ws, json!({"t": "ptr", "dx": 400, "dy": 400}));
    send(&mut ws, json!({"t": "text", "s": "stale"}));
    std::thread::sleep(Duration::from_millis(300));

    // A new compositor comes up under the same name.
    let _restarted = env.start_sway();
    wait_compositor(&eng, true);
    let probe = Probe::start(&mut env, "probe2");
    let base = settle(&mut ws, &probe);
    assert!(
        !probe
            .events()
            .iter()
            .any(|e| e["e"] == "key" || e["e"] == "button"),
        "nothing stale was replayed or released on the new compositor: {:?}",
        probe.events()
    );
    send(&mut ws, json!({"t": "ptr", "dx": 25, "dy": 10}));
    assert!(
        wait_until(5, || probe
            .last_pos()
            .map(|p| near(p, (base.0 + 25.0, base.1 + 10.0), 0.5))
            .unwrap_or(false)),
        "motion after restart: {:?}",
        probe.last_pos()
    );
    // Typing works again: the keymap was uploaded to the new compositor.
    send(&mut ws, json!({"t": "mods", "m": []}));
    send(&mut ws, json!({"t": "text", "s": "ab\u{e9}"}));
    assert!(
        wait_until(5, || probe
            .events()
            .iter()
            .filter(|e| e["e"] == "key" && e["down"] == true)
            .count()
            >= 3),
        "no keys after restart: {:?}",
        probe.events()
    );
    let keys: Vec<u64> = probe
        .events()
        .iter()
        .filter(|e| e["e"] == "key" && e["down"] == true)
        .map(|e| e["k"].as_u64().unwrap())
        .collect();
    assert_eq!(keys, vec![30, 48, 200]);
}

#[test]
fn a_three_finger_swipe_switches_workspace() {
    if !need(&["sway", "swaymsg"], "swipe") {
        return;
    }
    let mut env = Env::new("swipe");
    let _sway = env.start_sway();
    let eng = start_engine(&env, &[]);
    wait_compositor(&eng, true);
    let _p1 = Probe::start(&mut env, "p1");
    env.swaymsg(&["workspace", "2"]);
    let _p2 = Probe::start(&mut env, "p2");
    env.swaymsg(&["workspace", "1"]);
    let focused = |env: &Env| -> String {
        let v: Value = serde_json::from_str(&env.swaymsg(&["-t", "get_workspaces", "-r"])).unwrap();
        v.as_array()
            .unwrap()
            .iter()
            .find(|w| w["focused"] == true)
            .map(|w| w["name"].as_str().unwrap().to_string())
            .unwrap_or_default()
    };
    assert_eq!(focused(&env), "1");
    let creds = pair(&eng, "Test phone");
    let mut ws = ws(eng.port, &creds);
    let c = Clock(Instant::now());
    let swipe = |ws: &mut Ws, dir: f64| {
        for i in 0..3u32 {
            touch(ws, &c, "down", i, 200.0 + 40.0 * i as f64, 300.0);
        }
        for step in 1..=6 {
            for i in 0..3u32 {
                touch(
                    ws,
                    &c,
                    "move",
                    i,
                    200.0 + 40.0 * i as f64 + dir * 25.0 * step as f64,
                    300.0,
                );
            }
            std::thread::sleep(Duration::from_millis(10));
        }
        for i in 0..3u32 {
            touch(ws, &c, "up", i, 200.0, 300.0);
        }
    };
    swipe(&mut ws, -1.0);
    assert!(
        wait_until(5, || focused(&env) == "2"),
        "swipe left did not go to the next workspace"
    );
    swipe(&mut ws, 1.0);
    assert!(
        wait_until(5, || focused(&env) == "1"),
        "swipe right did not go back"
    );
}

/// Latency from the controller socket to the compositor delivering the event
/// to a client, on a private software-rendered sway. Prints the numbers.
#[test]
fn pointer_latency_is_measured_and_small() {
    if !need(&["sway", "swaymsg"], "latency") {
        return;
    }
    let mut env = Env::new("latency");
    let _sway = env.start_sway();
    let eng = start_engine(&env, &[]);
    let probe = Probe::start(&mut env, "probe");
    wait_compositor(&eng, true);
    let creds = pair(&eng, "Test phone");
    let mut ws = ws(eng.port, &creds);
    let base = settle(&mut ws, &probe);
    let mut sent: Vec<(f64, u64)> = Vec::new();
    for i in 1..=200 {
        sent.push((base.0 + i as f64, now_us()));
        send(&mut ws, json!({"t": "ptr", "dx": 1, "dy": 0}));
        std::thread::sleep(Duration::from_millis(8));
    }
    assert!(wait_until(10, || probe
        .last_pos()
        .map(|p| p.0 >= base.0 + 199.5)
        .unwrap_or(false)));
    let ev = probe.events();
    let mut lat: Vec<f64> = Vec::new();
    for (x, t) in sent {
        if let Some(e) = ev
            .iter()
            .find(|e| e["e"] == "motion" && (e["x"].as_f64().unwrap() - x).abs() < 0.01)
        {
            lat.push((e["us"].as_u64().unwrap() as f64 - t as f64) / 1000.0);
        }
    }
    assert!(lat.len() >= 100, "too few matched events: {}", lat.len());
    lat.sort_by(|a, b| a.partial_cmp(b).unwrap());
    let p = |q: f64| lat[((lat.len() - 1) as f64 * q) as usize];
    eprintln!(
        "LATENCY ms over {} motions: p50 {:.2} p95 {:.2} max {:.2}",
        lat.len(),
        p(0.5),
        p(0.95),
        lat[lat.len() - 1]
    );
    assert!(
        p(0.5) < 100.0 && p(0.95) < 500.0,
        "latency p50 {:.1} ms, p95 {:.1} ms",
        p(0.5),
        p(0.95)
    );
}

#[test]
fn the_phone_pages_own_crypto_interoperates_with_the_engine() {
    if !need(&["node"], "page crypto interop") {
        return;
    }
    let env = Env::new("interop");
    let eng = start_engine(&env, &[]);
    let script = Path::new(env!("CARGO_MANIFEST_DIR")).join("tests/js/interop.cjs");
    let out = Command::new("node")
        .arg("--experimental-websocket")
        .arg(&script)
        .arg(eng.port.to_string())
        .arg(&eng.control)
        .output()
        .expect("run node");
    let text = format!(
        "{}{}",
        String::from_utf8_lossy(&out.stdout),
        String::from_utf8_lossy(&out.stderr)
    );
    assert!(
        out.status.success() && text.contains("INTEROP OK"),
        "{text}"
    );
}
