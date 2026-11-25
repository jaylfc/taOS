// SPDX-License-Identifier: AGPL-3.0-or-later
//! `taos-mesh`: the daemon and the command line.

use serde_json::{json, Value};
use std::path::PathBuf;
use std::process::ExitCode;
use std::sync::atomic::AtomicBool;
use std::sync::Arc;
use taos_mesh::api::{call, Daemon};
use taos_mesh::engine::Engine;

const USAGE: &str = "taos-mesh: mesh radio engine

  taos-mesh daemon [--data-dir D] [--socket S] [--owner ID] [--device PATH|tcp:HOST:PORT]... [--network N] [--receive-only]
  taos-mesh radios | discover | health
  taos-mesh connect DEVICE [--network meshtastic|meshcore]
  taos-mesh listen
  taos-mesh messages [--identity I] [--channel N] [--network N] [--kind K] [--since MS] [--until MS] [--limit N] [--as WHO]
  taos-mesh send --network N --text T [--to NODE] [--channel N] [--radio R] [--as PRINCIPAL]
  taos-mesh positions [--as WHO]
  taos-mesh identities
  taos-mesh identities assign NODE IDENTITY [--name NAME] | unassign NODE
  taos-mesh identities contact NAME [--notes TEXT] | told CONTACT [--date YYYY-MM-DD] | untold CONTACT
  taos-mesh link CONTACT person:ACCOUNT
  taos-mesh hide-locations CONTACT --as person:ACCOUNT
  taos-mesh grants | grant AGENT --network N [--channel N] [--kind send|read] [--msgs-per-hour N] [--bytes-per-hour N] [--hours H] | revoke ID

Common: --socket S (default $XDG_RUNTIME_DIR/taos-mesh.sock), --as PRINCIPAL (default owner).
IDENTITY is agent:ID, person:ID or contact:ID.";

fn runtime_dir() -> PathBuf {
    std::env::var_os("XDG_RUNTIME_DIR").map(PathBuf::from).unwrap_or_else(std::env::temp_dir)
}

fn data_dir() -> PathBuf {
    if let Some(d) = std::env::var_os("XDG_DATA_HOME") {
        return PathBuf::from(d).join("taos-mesh");
    }
    std::env::var_os("HOME").map(|h| PathBuf::from(h).join(".local/share/taos-mesh")).unwrap_or_else(|| PathBuf::from("taos-mesh-data"))
}

struct Args {
    pos: Vec<String>,
    opts: Vec<(String, String)>,
    flags: Vec<String>,
}

fn parse(args: Vec<String>) -> Args {
    let mut a = Args { pos: vec![], opts: vec![], flags: vec![] };
    let mut it = args.into_iter().peekable();
    while let Some(x) = it.next() {
        if let Some(k) = x.strip_prefix("--") {
            if k == "receive-only" {
                a.flags.push(k.into());
            } else if let Some(v) = it.next() {
                a.opts.push((k.into(), v));
            }
        } else {
            a.pos.push(x);
        }
    }
    a
}

impl Args {
    fn opt(&self, k: &str) -> Option<&str> {
        self.opts.iter().rev().find(|(x, _)| x == k).map(|x| x.1.as_str())
    }
    fn all(&self, k: &str) -> Vec<&str> {
        self.opts.iter().filter(|(x, _)| x == k).map(|x| x.1.as_str()).collect()
    }
    fn num(&self, k: &str) -> Option<u64> {
        self.opt(k).and_then(|v| v.parse().ok())
    }
}

fn print_result(r: Result<Value, String>) -> ExitCode {
    match r {
        Err(e) => {
            eprintln!("taos-mesh: {e}");
            ExitCode::from(2)
        }
        Ok(v) => {
            if v["ok"] == true {
                println!("{}", serde_json::to_string_pretty(&v["result"]).unwrap_or_default());
                ExitCode::SUCCESS
            } else {
                eprintln!("taos-mesh: {}: {}", v["error"]["code"].as_str().unwrap_or("error"), v["error"]["message"].as_str().unwrap_or(""));
                ExitCode::from(1)
            }
        }
    }
}

fn main() -> ExitCode {
    let raw: Vec<String> = std::env::args().skip(1).collect();
    if raw.is_empty() || raw[0] == "-h" || raw[0] == "--help" || raw[0] == "help" {
        println!("{USAGE}");
        return ExitCode::SUCCESS;
    }
    let a = parse(raw);
    let cmd = a.pos.first().cloned().unwrap_or_default();
    let socket = a.opt("socket").map(PathBuf::from).unwrap_or_else(|| runtime_dir().join("taos-mesh.sock"));
    let who = a.opt("as").unwrap_or("owner").to_string();
    let c = |m: &str, p: Value| print_result(call(&socket, m, &who, p));
    match cmd.as_str() {
        "daemon" => daemon(&a, &socket),
        "__append-loop" => append_loop(&a),
        "radios" => c("radios", json!({})),
        "discover" => c("discover", json!({})),
        "health" => c("health", json!({})),
        "connect" => c("connect", json!({"device": a.pos.get(1), "network": a.opt("network")})),
        "listen" => listen(&socket),
        "messages" => {
            let mut f = json!({});
            if let Some(v) = a.opt("identity") {
                f["identity"] = json!(v);
            }
            if let Some(v) = a.opt("network") {
                f["network"] = json!(v);
            }
            if let Some(v) = a.opt("kind") {
                f["kind"] = json!(v);
            }
            for (k, key) in [("channel", "channel"), ("since", "since_ms"), ("until", "until_ms"), ("limit", "limit")] {
                if let Some(v) = a.num(k) {
                    f[key] = json!(v);
                }
            }
            c("messages", json!({"filter": f, "as": a.opt("as")}))
        }
        "send" => c("send", json!({"network": a.opt("network"), "text": a.opt("text"), "to": a.opt("to"), "channel": a.num("channel"), "radio": a.opt("radio")})),
        "positions" => c("last_positions", json!({})),
        "identities" => match a.pos.get(1).map(String::as_str) {
            None => c("identities.list", json!({})),
            Some("assign") => c("identities.assign", json!({"node": a.pos.get(2), "identity": a.pos.get(3), "name": a.opt("name")})),
            Some("unassign") => c("identities.assign", json!({"node": a.pos.get(2), "identity": null})),
            Some("contact") => c("contacts.create", json!({"name": a.pos.get(2), "notes": a.opt("notes")})),
            Some("told") => c("contacts.told", json!({"contact": a.pos.get(2), "told": true, "date": a.opt("date")})),
            Some("untold") => c("contacts.told", json!({"contact": a.pos.get(2), "told": false})),
            Some(o) => {
                eprintln!("taos-mesh: unknown identities subcommand {o}");
                ExitCode::from(2)
            }
        },
        "link" => c("link", json!({"contact": a.pos.get(1), "account": a.pos.get(2)})),
        "hide-locations" => c("locations.hide", json!({"contact": a.pos.get(1)})),
        "grants" => c("grants.list", json!({})),
        "grant" => {
            let expires = a.num("hours").map(|h| taos_mesh::model::now_ms() + h * 3_600_000);
            c(
                "grants.add",
                json!({"agent": a.pos.get(1), "network": a.opt("network"), "channel": a.num("channel"), "kind": a.opt("kind").unwrap_or("send"),
                       "max_msgs_per_hour": a.num("msgs-per-hour"), "max_bytes_per_hour": a.num("bytes-per-hour"), "expires_ms": expires}),
            )
        }
        "revoke" => c("grants.revoke", json!({"id": a.pos.get(1)})),
        other => {
            eprintln!("taos-mesh: unknown command {other}\n{USAGE}");
            ExitCode::from(2)
        }
    }
}

fn listen(socket: &std::path::Path) -> ExitCode {
    use std::io::{BufRead, BufReader, Write};
    let mut s = match std::os::unix::net::UnixStream::connect(socket) {
        Ok(s) => s,
        Err(e) => {
            eprintln!("taos-mesh: cannot reach {}: {e}", socket.display());
            return ExitCode::from(2);
        }
    };
    let _ = writeln!(s, "{}", json!({"id": 1, "method": "subscribe", "principal": "owner"}));
    for l in BufReader::new(s).lines() {
        match l {
            Ok(l) => println!("{l}"),
            Err(_) => break,
        }
    }
    ExitCode::SUCCESS
}

fn daemon(a: &Args, socket: &std::path::Path) -> ExitCode {
    let dir = a.opt("data-dir").map(PathBuf::from).unwrap_or_else(data_dir);
    let owner = a.opt("owner").unwrap_or("owner").to_string();
    let engine = match Engine::open(&dir, &owner) {
        Ok(e) => e,
        Err(e) => {
            eprintln!("taos-mesh: cannot open the store in {}: {e}", dir.display());
            return ExitCode::from(1);
        }
    };
    let d = Arc::new(Daemon { engine, receive_only: a.flags.iter().any(|f| f == "receive-only"), stop: Arc::new(AtomicBool::new(false)) });
    for dev in a.all("device") {
        match d.connect_spec(dev, a.opt("network")) {
            Ok(v) => eprintln!("connected {v}"),
            Err(e) => eprintln!("taos-mesh: {dev}: {e}"),
        }
    }
    eprintln!("taos-mesh: serving {} (data in {})", socket.display(), dir.display());
    if let Err(e) = d.serve(socket) {
        eprintln!("taos-mesh: {e}");
        return ExitCode::from(1);
    }
    ExitCode::SUCCESS
}

/// Test helper: append records as fast as possible, printing each id only
/// after its fsync returned. The kill-mid-write test SIGKILLs this process.
fn append_loop(a: &Args) -> ExitCode {
    use taos_mesh::model::*;
    let dir = PathBuf::from(a.pos.get(1).cloned().unwrap_or_default());
    let engine = match Engine::open(&dir, "owner") {
        Ok(e) => e,
        Err(e) => {
            eprintln!("{e}");
            return ExitCode::from(1);
        }
    };
    let mut n = 0u32;
    loop {
        n += 1;
        let raw = vec![(n % 251) as u8; 200];
        let inb = Inbound {
            raw,
            event: Event::Text { text: format!("message {n}") },
            from: Some("!00000001".into()),
            to: Some("!ffffffff".into()),
            channel: Some(0),
            packet_id: Some(n),
            radio_ts: None,
            signal: Signal::default(),
            position: None,
            broadcast: true,
        };
        match engine.ingest("meshtastic", "!0000aaaa", inb) {
            Ok(r) => println!("{}", r.id),
            Err(e) => {
                eprintln!("{e}");
                return ExitCode::from(1);
            }
        }
    }
}
