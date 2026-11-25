// SPDX-License-Identifier: AGPL-3.0-or-later
//! `taos-mesh`: the daemon and the command line.

use serde_json::{json, Value};
use std::path::PathBuf;
use std::process::ExitCode;
use std::sync::Arc;
use taos_mesh::api::call_with_token;
use taos_mesh::engine::Engine;

const USAGE: &str = "taos-mesh: mesh radio engine

  taos-mesh daemon [--data-dir D] [--socket S] [--owner ID] [--device PATH|tcp:HOST:PORT]... [--network N] [--receive-only]
  taos-mesh radios | discover | health
  taos-mesh connect DEVICE [--network meshtastic|meshcore]
  taos-mesh listen
  taos-mesh messages [--identity I] [--channel N] [--network N] [--kind K] [--since MS] [--until MS] [--limit N] [--as WHO]
  taos-mesh send --network N --text T [--to NODE] [--channel N] [--radio R] [--as PRINCIPAL]
  taos-mesh positions [--as WHO]
  taos-mesh release DEVICE | device-config RADIO | device-apply RADIO path=value ... [--confirm-region]
  taos-mesh identities
  taos-mesh identities assign NODE IDENTITY [--name NAME] | unassign NODE
  taos-mesh identities contact NAME [--notes TEXT] | told CONTACT [--date YYYY-MM-DD] | untold CONTACT
  taos-mesh link CONTACT person:ACCOUNT
  taos-mesh hide-locations CONTACT --as person:ACCOUNT
  taos-mesh controller-key | token --sub PRINCIPAL [--ttl-secs N] [--methods a,b]
  taos-mesh grants | grant AGENT --network N [--channel N] [--kind send|read|position] [--msgs-per-hour N] [--bytes-per-hour N] [--hours H] | revoke ID

Common: --socket S (default $XDG_RUNTIME_DIR/taos-mesh.sock), --as PRINCIPAL (default owner; any other needs --token, see docs/API.md).
IDENTITY is agent:ID, person:ID or contact:ID.";

/// The default socket lives in the user's runtime directory. There is no
/// fallback to a shared directory such as /tmp.
fn default_socket() -> Result<PathBuf, String> {
    std::env::var_os("XDG_RUNTIME_DIR")
        .filter(|d| !d.is_empty())
        .map(|d| PathBuf::from(d).join("taos-mesh.sock"))
        .ok_or_else(|| "XDG_RUNTIME_DIR is not set: there is no private place for the socket (pass --socket PATH to choose one)".to_string())
}

fn data_dir() -> PathBuf {
    if let Some(d) = std::env::var_os("XDG_DATA_HOME") {
        return PathBuf::from(d).join("taos-mesh");
    }
    std::env::var_os("HOME")
        .map(|h| PathBuf::from(h).join(".local/share/taos-mesh"))
        .unwrap_or_else(|| PathBuf::from("taos-mesh-data"))
}

struct Args {
    pos: Vec<String>,
    opts: Vec<(String, String)>,
    flags: Vec<String>,
}

fn parse(args: Vec<String>) -> Args {
    let mut a = Args {
        pos: vec![],
        opts: vec![],
        flags: vec![],
    };
    let mut it = args.into_iter().peekable();
    while let Some(x) = it.next() {
        if let Some(k) = x.strip_prefix("--") {
            if k == "receive-only" || k == "confirm-region" {
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
        self.opts
            .iter()
            .rev()
            .find(|(x, _)| x == k)
            .map(|x| x.1.as_str())
    }
    fn all(&self, k: &str) -> Vec<&str> {
        self.opts
            .iter()
            .filter(|(x, _)| x == k)
            .map(|x| x.1.as_str())
            .collect()
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
                println!(
                    "{}",
                    serde_json::to_string_pretty(&v["result"]).unwrap_or_default()
                );
                ExitCode::SUCCESS
            } else {
                eprintln!(
                    "taos-mesh: {}: {}",
                    v["error"]["code"].as_str().unwrap_or("error"),
                    v["error"]["message"].as_str().unwrap_or("")
                );
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
    let socket = match a
        .opt("socket")
        .map(PathBuf::from)
        .map(Ok)
        .unwrap_or_else(default_socket)
    {
        Ok(s) => s,
        Err(e) if matches!(cmd.as_str(), "controller-key" | "token" | "__append-loop") => {
            let _ = e;
            PathBuf::new()
        }
        Err(e) => {
            eprintln!("taos-mesh: {e}");
            return ExitCode::from(2);
        }
    };
    let who = a.opt("as").unwrap_or("owner").to_string();
    let token = a.opt("token").map(String::from);
    let c =
        |m: &str, p: Value| print_result(call_with_token(&socket, m, &who, token.as_deref(), p));
    match cmd.as_str() {
        "daemon" => daemon(&a, &socket),
        "__append-loop" => append_loop(&a),
        "controller-key" => controller_key(&a),
        "token" => mint_token(&a),
        "radios" => c("radios", json!({})),
        "discover" => c("discover", json!({})),
        "health" => c("health", json!({})),
        "connect" => c(
            "connect",
            json!({"device": a.pos.get(1), "network": a.opt("network")}),
        ),
        "release" => c("radios.release", json!({"device": a.pos.get(1)})),
        "device-config" => c("device.config", json!({"radio": a.pos.get(1)})),
        "device-apply" => {
            // taos-mesh device-apply RADIO path=value ...   (the owner's own shell; agents have no such method)
            let mut ch = serde_json::Map::new();
            for kv in a.pos.iter().skip(2) {
                let Some((k, v)) = kv.split_once('=') else {
                    eprintln!("taos-mesh: {kv}: expected path=value");
                    return ExitCode::from(2);
                };
                ch.insert(
                    k.into(),
                    v.parse::<u64>()
                        .map(|n| json!(n))
                        .unwrap_or_else(|_| json!(v)),
                );
            }
            c(
                "device.apply",
                json!({"radio": a.pos.get(1), "changes": ch, "confirm_region": a.flags.iter().any(|f| f == "confirm-region")}),
            )
        }
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
            for (k, key) in [
                ("channel", "channel"),
                ("since", "since_ms"),
                ("until", "until_ms"),
                ("limit", "limit"),
            ] {
                if let Some(v) = a.num(k) {
                    f[key] = json!(v);
                }
            }
            c("messages", json!({"filter": f, "as": a.opt("as")}))
        }
        "send" => c(
            "send",
            json!({"network": a.opt("network"), "text": a.opt("text"), "to": a.opt("to"), "channel": a.num("channel"), "radio": a.opt("radio")}),
        ),
        "positions" => c("last_positions", json!({})),
        "identities" => match a.pos.get(1).map(String::as_str) {
            None => c("identities.list", json!({})),
            Some("assign") => c(
                "identities.assign",
                json!({"node": a.pos.get(2), "identity": a.pos.get(3), "name": a.opt("name")}),
            ),
            Some("unassign") => c(
                "identities.assign",
                json!({"node": a.pos.get(2), "identity": null}),
            ),
            Some("contact") => c(
                "contacts.create",
                json!({"name": a.pos.get(2), "notes": a.opt("notes")}),
            ),
            Some("told") => c(
                "contacts.told",
                json!({"contact": a.pos.get(2), "told": true, "date": a.opt("date")}),
            ),
            Some("untold") => c(
                "contacts.told",
                json!({"contact": a.pos.get(2), "told": false}),
            ),
            Some(o) => {
                eprintln!("taos-mesh: unknown identities subcommand {o}");
                ExitCode::from(2)
            }
        },
        "link" => c(
            "link",
            json!({"contact": a.pos.get(1), "account": a.pos.get(2)}),
        ),
        "hide-locations" => c("locations.hide", json!({"contact": a.pos.get(1)})),
        "grants" => c("grants.list", json!({})),
        "grant" => {
            let expires = a
                .num("hours")
                .map(|h| taos_mesh::model::now_ms() + h * 3_600_000);
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
    let _ = writeln!(
        s,
        "{}",
        json!({"id": 1, "method": "subscribe", "principal": "owner"})
    );
    for l in BufReader::new(s).lines() {
        match l {
            Ok(l) => println!("{l}"),
            Err(_) => break,
        }
    }
    ExitCode::SUCCESS
}

fn daemon(a: &Args, socket: &std::path::Path) -> ExitCode {
    let dir = a
        .opt("data-dir")
        .map(PathBuf::from)
        .unwrap_or_else(data_dir);
    let owner = a.opt("owner").map(String::from).unwrap_or_else(|| {
        std::env::var("USER")
            .map(|u| format!("person:{u}"))
            .unwrap_or_else(|_| "owner".to_string())
    });
    let engine = match Engine::open(&dir, &owner) {
        Ok(e) => e,
        Err(e) => {
            eprintln!("taos-mesh: cannot open the store in {}: {e}", dir.display());
            return ExitCode::from(1);
        }
    };
    let mut d = taos_mesh::api::Daemon::new(engine, a.flags.iter().any(|f| f == "receive-only"));
    match taos_mesh::auth::TokenVerifier::from_dir(&dir) {
        Ok(v) => d.verifier = v,
        Err(e) => {
            eprintln!("taos-mesh: controller key: {e}");
            return ExitCode::from(1);
        }
    }
    let d = Arc::new(d);
    taos_mesh::api::set_radios_file(Some(dir.join("radios.json")));
    // radios the owner asked for in the app are opened again (read-only handshake, as for --device)
    for saved in taos_mesh::api::saved_radios() {
        if let Some(dev) = saved["device"].as_str() {
            match d.connect_spec(dev, saved["network"].as_str()) {
                Ok(v) => eprintln!("reconnected {v}"),
                Err(e) => eprintln!("taos-mesh: {dev}: {e}"),
            }
        }
    }
    for dev in a.all("device") {
        match d.connect_spec(dev, a.opt("network")) {
            Ok(v) => eprintln!("connected {v}"),
            Err(e) => eprintln!("taos-mesh: {dev}: {e}"),
        }
    }
    eprintln!(
        "taos-mesh: serving {} (data in {})",
        socket.display(),
        dir.display()
    );
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
            event: Event::Text {
                text: format!("message {n}"),
            },
            from: Some("!00000001".into()),
            to: Some("!ffffffff".into()),
            channel: Some(0),
            packet_id: Some(n),
            radio_ts: None,
            signal: Signal::default(),
            position: None,
            broadcast: true,
            trust: None,
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

/// Create `<data dir>/controller.key` (random, mode 0600) for the taOS
/// controller to hold. Refuses to replace an existing key.
fn controller_key(a: &Args) -> ExitCode {
    use std::io::{Read, Write};
    use std::os::unix::fs::OpenOptionsExt;
    let dir = a
        .opt("data-dir")
        .map(PathBuf::from)
        .unwrap_or_else(data_dir);
    let mut key = [0u8; 32];
    if std::fs::File::open("/dev/urandom")
        .and_then(|mut f| f.read_exact(&mut key))
        .is_err()
    {
        eprintln!("taos-mesh: cannot read /dev/urandom");
        return ExitCode::from(1);
    }
    let _ = std::fs::create_dir_all(&dir);
    let p = dir.join("controller.key");
    match std::fs::OpenOptions::new()
        .write(true)
        .create_new(true)
        .mode(0o600)
        .open(&p)
        .and_then(|mut f| f.write_all(&key))
    {
        Ok(()) => {
            println!("{}", p.display());
            ExitCode::SUCCESS
        }
        Err(e) => {
            eprintln!("taos-mesh: {}: {e}", p.display());
            ExitCode::from(1)
        }
    }
}

/// Mint a capability token (the controller normally does this itself; this
/// is the same code, for tests and for operators).
fn mint_token(a: &Args) -> ExitCode {
    let dir = a
        .opt("data-dir")
        .map(PathBuf::from)
        .unwrap_or_else(data_dir);
    let ver = match taos_mesh::auth::TokenVerifier::from_dir(&dir) {
        Ok(Some(v)) => v,
        Ok(None) => {
            eprintln!(
                "taos-mesh: no controller.key in {}; run `taos-mesh controller-key` first",
                dir.display()
            );
            return ExitCode::from(1);
        }
        Err(e) => {
            eprintln!("taos-mesh: {e}");
            return ExitCode::from(1);
        }
    };
    let Some(sub) = a.opt("sub") else {
        eprintln!("taos-mesh: --sub PRINCIPAL is required");
        return ExitCode::from(2);
    };
    let ttl = a.num("ttl-secs").unwrap_or(300);
    let methods: Vec<&str> = a
        .opt("methods")
        .unwrap_or("messages,last_positions,send")
        .split(',')
        .collect();
    println!(
        "{}",
        ver.mint(sub, taos_mesh::model::now_ms() + ttl * 1000, &methods)
    );
    ExitCode::SUCCESS
}
