// SPDX-License-Identifier: AGPL-3.0-or-later
//! `taos-input`: the engine and its command line.
//!
//!   taos-input serve [--bind auto|IP] [--port N] [--allow-net CIDR]... [--state-dir DIR]
//!                    [--control PATH] [--theme-file PATH] [--theme-mode dark|light]
//!   taos-input status | pair | devices | revoke <id>|--all | stop | ctl '<json>'
//!
//! Everything but `serve` talks to the running engine over its control
//! socket and prints the JSON reply (`--json` is the default shape; the human
//! forms print the useful lines).

use serde_json::{json, Value};
use std::net::{IpAddr, SocketAddr, TcpListener};
use std::path::PathBuf;
use std::process::ExitCode;
use taos_input::auth::Auth;
use taos_input::engine::Engine;
use taos_input::inject::{Connector, Injector, Sink};
use taos_input::netpolicy::{detect_lan_addr, Net, Policy};
use taos_input::wl::WaylandSink;
use taos_input::{control, server};

const DEFAULT_PORT: u16 = 7981;

fn control_path(over: &Option<String>) -> PathBuf {
    if let Some(p) = over {
        return PathBuf::from(p);
    }
    if let Some(p) = std::env::var_os("TAOS_INPUT_CONTROL") {
        return PathBuf::from(p);
    }
    let rt = std::env::var_os("XDG_RUNTIME_DIR")
        .map(PathBuf::from)
        .unwrap_or_else(std::env::temp_dir);
    rt.join("taos-input").join("control.sock")
}

fn state_dir(over: &Option<String>) -> PathBuf {
    if let Some(p) = over {
        return PathBuf::from(p);
    }
    if let Some(p) = std::env::var_os("XDG_STATE_HOME") {
        return PathBuf::from(p).join("taos-input");
    }
    let home = std::env::var_os("HOME")
        .map(PathBuf::from)
        .unwrap_or_else(std::env::temp_dir);
    home.join(".local/state/taos-input")
}

struct Args {
    rest: Vec<String>,
}

impl Args {
    fn flag(&mut self, name: &str) -> Option<String> {
        let i = self.rest.iter().position(|a| a == name)?;
        if i + 1 >= self.rest.len() {
            return None;
        }
        self.rest.remove(i);
        Some(self.rest.remove(i))
    }
    fn flags(&mut self, name: &str) -> Vec<String> {
        let mut v = Vec::new();
        while let Some(x) = self.flag(name) {
            v.push(x);
        }
        v
    }
    fn switch(&mut self, name: &str) -> bool {
        match self.rest.iter().position(|a| a == name) {
            Some(i) => {
                self.rest.remove(i);
                true
            }
            None => false,
        }
    }
}

fn usage() -> ExitCode {
    eprintln!(
        "usage: taos-input serve [--bind auto|IP] [--port N] [--allow-net CIDR]... [--allow-host HOST:PORT]... [--state-dir DIR] [--control PATH]\n\
         \x20                       [--theme-file PATH] [--theme-mode dark|light]\n\
         \x20      taos-input status | pair | devices | revoke <id>|--all | stop | ctl '<json>'   [--control PATH]"
    );
    ExitCode::from(2)
}

fn serve(mut a: Args) -> Result<(), String> {
    let control_over = a.flag("--control");
    let state_over = a.flag("--state-dir");
    let bind = a.flag("--bind").unwrap_or_else(|| "auto".into());
    let port: u16 = a
        .flag("--port")
        .map(|p| p.parse().map_err(|_| "bad --port".to_string()))
        .transpose()?
        .unwrap_or(DEFAULT_PORT);
    let nets = a.flags("--allow-net");
    let extra_hosts = a.flags("--allow-host");
    let theme_file = a.flag("--theme-file");
    let theme_mode = a.flag("--theme-mode").unwrap_or_else(|| "dark".into());
    if !a.rest.is_empty() {
        return Err(format!("unexpected argument: {}", a.rest[0]));
    }
    if theme_mode != "dark" && theme_mode != "light" {
        return Err("--theme-mode is dark or light".into());
    }
    let policy = if nets.is_empty() {
        Policy::lan()
    } else {
        Policy::new(
            nets.iter()
                .map(|n| Net::parse(n))
                .collect::<Result<Vec<_>, _>>()?,
        )
    };
    let ip: IpAddr = if bind == "auto" {
        detect_lan_addr(&policy)
            .ok_or("could not find this machine's home network address; give one with --bind")?
    } else {
        bind.parse()
            .map_err(|_| format!("--bind needs an IP address, got {bind}"))?
    };
    policy.check_bind(ip)?;
    let listener = TcpListener::bind(SocketAddr::new(ip, port))
        .map_err(|e| format!("cannot listen on {ip}:{port}: {e}"))?;
    let local = listener.local_addr().map_err(|e| e.to_string())?;
    let url = match local.ip() {
        IpAddr::V4(v) => format!("http://{v}:{}/", local.port()),
        IpAddr::V6(v) => format!("http://[{v}]:{}/", local.port()),
    };
    let sdir = state_dir(&state_over);
    std::fs::create_dir_all(&sdir).map_err(|e| format!("{}: {e}", sdir.display()))?;
    let auth = Auth::open(&sdir.join("devices.json"))?;
    let identity = taos_input::secure::Identity::load_or_create(&sdir.join("identity.key"))?;
    let mut hosts = vec![format!(
        "{}:{}",
        match local.ip() {
            IpAddr::V4(v) => v.to_string(),
            IpAddr::V6(v) => format!("[{v}]"),
        },
        local.port()
    )];
    hosts.extend(extra_hosts);
    let cpath = control_path(&control_over);
    let ctl = control::bind(&cpath)?;
    let connector: Connector =
        Box::new(|| WaylandSink::connect().map(|s| Box::new(s) as Box<dyn Sink>));
    let injector = Injector::spawn(connector);
    let theme = theme_file.map(|p| (PathBuf::from(p), theme_mode));
    let engine = Engine::new(auth, identity, hosts, injector, policy, url.clone(), theme);

    let (stop_tx, stop_rx) = std::sync::mpsc::channel::<()>();
    {
        let mut sigs = signal_hook::iterator::Signals::new([
            signal_hook::consts::SIGTERM,
            signal_hook::consts::SIGINT,
        ])
        .map_err(|e| e.to_string())?;
        let tx = stop_tx.clone();
        std::thread::spawn(move || {
            if sigs.forever().next().is_some() {
                let _ = tx.send(());
            }
        });
    }
    {
        let e = engine.clone();
        std::thread::spawn(move || server::serve(e, listener));
        let e = engine.clone();
        std::thread::spawn(move || control::serve(e, ctl));
        let e = engine.clone();
        let tx = stop_tx;
        std::thread::spawn(move || {
            while !e.stopping() {
                std::thread::sleep(std::time::Duration::from_millis(100));
            }
            let _ = tx.send(());
        });
    }
    eprintln!(
        "taos-input listening on {url} (control {})",
        cpath.display()
    );
    let _ = stop_rx.recv();
    engine.request_stop();
    // Held input is released by each connection as it closes; give them a beat.
    std::thread::sleep(std::time::Duration::from_millis(250));
    let _ = std::fs::remove_file(&cpath);
    Ok(())
}

fn client(cmd: &str, mut a: Args) -> Result<(), String> {
    let path = control_path(&a.flag("--control"));
    let human = !a.switch("--json");
    let req: Value = match cmd {
        "status" | "pair" | "devices" | "stop" => json!({"op": cmd}),
        "revoke" => {
            if a.switch("--all") {
                json!({"op": "revoke", "all": true})
            } else if let Some(id) = a.rest.first() {
                json!({"op": "revoke", "id": id})
            } else {
                return Err("revoke needs a device id or --all".into());
            }
        }
        "ctl" => {
            let raw = a.rest.first().ok_or("ctl needs a JSON request")?;
            serde_json::from_str(raw).map_err(|e| format!("not JSON: {e}"))?
        }
        _ => return Err(format!("unknown command {cmd}")),
    };
    let reply = control::request(&path, &req)?;
    let ok = reply.get("ok").and_then(|o| o.as_bool()).unwrap_or(false);
    if human && cmd == "pair" && ok {
        println!(
            "Open {} on the phone and enter the code {}. It works once, for {} minutes.",
            reply["url"].as_str().unwrap_or(""),
            reply["code"].as_str().unwrap_or(""),
            reply["expires_in"].as_u64().unwrap_or(0) / 60
        );
    } else {
        println!("{reply}");
    }
    if ok {
        Ok(())
    } else {
        Err(reply
            .get("error")
            .and_then(|e| e.as_str())
            .unwrap_or("failed")
            .to_string())
    }
}

fn main() -> ExitCode {
    let mut argv: Vec<String> = std::env::args().skip(1).collect();
    if argv.is_empty() {
        return usage();
    }
    let cmd = argv.remove(0);
    let a = Args { rest: argv };
    let r = match cmd.as_str() {
        "serve" => serve(a),
        "status" | "pair" | "devices" | "revoke" | "stop" | "ctl" => client(&cmd, a),
        _ => return usage(),
    };
    match r {
        Ok(()) => ExitCode::SUCCESS,
        Err(e) => {
            eprintln!("taos-input: {e}");
            ExitCode::FAILURE
        }
    }
}
