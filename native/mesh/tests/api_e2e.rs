// SPDX-License-Identifier: AGPL-3.0-or-later
//! The real path: a radio (fake link) feeding the daemon thread, the unix
//! socket API, and the `taos-mesh` command-line binary as the client.
mod common;
use common::*;
use serde_json::{json, Value};
use std::io::{BufRead, BufReader, Write};
use std::os::unix::net::UnixStream;
use std::path::PathBuf;
use std::process::Command;
use std::sync::atomic::AtomicBool;
use std::sync::Arc;
use std::time::{Duration, Instant};
use taos_mesh::api::{call, Daemon};
use taos_mesh::engine::Engine;
use taos_mesh::link::FakeLink;
use taos_mesh::meshtastic::frame;

struct Up {
    _t: TmpDir,
    sock: PathBuf,
    link: FakeLink,
    d: Arc<Daemon>,
}

fn up(receive_only: bool) -> Up {
    let t = TmpDir::new("e2e");
    let engine = Engine::open(&t.path().join("data"), "person:jay").unwrap();
    let d = Arc::new(Daemon { engine, receive_only, stop: Arc::new(AtomicBool::new(false)) });
    let sock = t.path().join("mesh.sock");
    let link = FakeLink::new("fake-e2e");
    let fx = fixture("meshtastic.json");
    link.reply_on_write(&{
        let mut s = frame(&frame_named(&fx, "my_info_heltec_v2_1"));
        s.extend(frame(&frame_named(&fx, "node_info_heltec_v2_1")));
        s.extend(frame(&frame_named(&fx, "text_broadcast")));
        s.extend(frame(&frame_named(&fx, "position_packet")));
        s
    });
    d.attach(Box::new(link.clone()), "meshtastic", vec![]).unwrap();
    let d2 = d.clone();
    let s2 = sock.clone();
    std::thread::spawn(move || d2.serve(&s2).unwrap());
    let end = Instant::now() + Duration::from_secs(5);
    while UnixStream::connect(&sock).is_err() {
        assert!(Instant::now() < end, "socket never came up");
        std::thread::sleep(Duration::from_millis(10));
    }
    Up { _t: t, sock, link, d }
}

fn wait_for(sock: &PathBuf, n: usize) -> Vec<Value> {
    let end = Instant::now() + Duration::from_secs(5);
    loop {
        let r = call(sock, "messages", "owner", json!({"filter": {}})).unwrap();
        let v = r["result"].as_array().cloned().unwrap_or_default();
        if v.len() >= n {
            return v;
        }
        assert!(Instant::now() < end, "only {} records arrived: {r}", v.len());
        std::thread::sleep(Duration::from_millis(20));
    }
}

fn cli(sock: &PathBuf, args: &[&str]) -> (i32, String, String) {
    let o = Command::new(env!("CARGO_BIN_EXE_taos-mesh")).arg("--socket").arg(sock).args(args).output().unwrap();
    (o.status.code().unwrap(), String::from_utf8_lossy(&o.stdout).into(), String::from_utf8_lossy(&o.stderr).into())
}

#[test]
fn frames_from_the_radio_arrive_through_the_api_and_the_cli() {
    let u = up(false);
    let recs = wait_for(&u.sock, 4);
    assert!(recs.iter().any(|r| r["kind"] == "text" && r["text"] == "hello mesh" && r["signal"]["rssi"] == -91));
    assert!(recs.iter().all(|r| r["radio"] == "!28af67cc" || r["radio"] == "fake-e2e"));
    let radios = call(&u.sock, "radios", "owner", json!({})).unwrap();
    assert_eq!(radios["result"][0]["id"], "!28af67cc");
    assert_eq!(radios["result"][0]["connected"], true);
    let h = call(&u.sock, "health", "owner", json!({})).unwrap();
    assert_eq!(h["result"]["ok"], true);
    let (code, out, _) = cli(&u.sock, &["messages", "--kind", "text"]);
    assert_eq!(code, 0);
    assert!(out.contains("hello mesh"), "{out}");
    let (code, out, _) = cli(&u.sock, &["radios"]);
    assert_eq!(code, 0);
    assert!(out.contains("28af67cc"));
    let (code, out, _) = cli(&u.sock, &["positions"]);
    assert_eq!(code, 0);
    assert!(out.contains("51.45"), "{out}");
}

#[test]
fn identities_and_link_through_the_cli() {
    let u = up(false);
    wait_for(&u.sock, 4);
    let (c, out, e) = cli(&u.sock, &["identities", "contact", "Sam", "--notes", "orange handset"]);
    assert_eq!(c, 0, "{e}");
    let cid = serde_json::from_str::<Value>(&out).unwrap()["contact"].as_str().unwrap().to_string();
    assert_eq!(cli(&u.sock, &["identities", "assign", "!7ed23c00", &format!("contact:{cid}")]).0, 0);
    assert_eq!(cli(&u.sock, &["identities", "told", &cid, "--date", "2026-11-25"]).0, 0);
    let (_, out, _) = cli(&u.sock, &["messages", "--identity", &format!("contact:{cid}")]);
    let v: Value = serde_json::from_str(&out).unwrap();
    assert_eq!(v.as_array().unwrap().len(), 1, "{out}");
    assert_eq!(v[0]["sender"]["name"], "Sam");
    assert_eq!(cli(&u.sock, &["link", &cid, "person:sam"]).0, 0);
    let (_, out, _) = cli(&u.sock, &["identities"]);
    assert!(out.contains("person:sam") && out.contains("2026-11-25"));
    // the linked account sees the contact's position through the API
    let r = call(&u.sock, "messages", "person:sam", json!({"filter": {}})).unwrap();
    assert_eq!(r["result"].as_array().unwrap().len(), 1);
    // a bad link is refused with a non-zero exit
    assert_ne!(cli(&u.sock, &["link", &cid, "agent:x"]).0, 0);
}

#[test]
fn an_agent_over_the_api_is_refused_without_a_grant_then_allowed_with_one() {
    let u = up(false);
    wait_for(&u.sock, 4);
    let r = call(&u.sock, "send", "agent:scout", json!({"network": "meshtastic", "text": "hi", "channel": 0})).unwrap();
    assert_eq!(r["ok"], false);
    assert_eq!(r["error"]["code"], "no_grant");
    let r = call(&u.sock, "grants.add", "agent:scout", json!({"agent": "agent:scout", "network": "meshtastic"})).unwrap();
    assert_eq!(r["error"]["code"], "forbidden", "an agent cannot grant itself");
    let (c, _, e) = cli(&u.sock, &["grant", "agent:scout", "--network", "meshtastic", "--channel", "0", "--msgs-per-hour", "5"]);
    assert_eq!(c, 0, "{e}");
    let r = call(&u.sock, "send", "agent:scout", json!({"network": "meshtastic", "text": "hi", "channel": 0})).unwrap();
    assert_eq!(r["ok"], true, "{r}");
    // the daemon thread hands it to the link
    let end = Instant::now() + Duration::from_secs(5);
    while !u.link.written_bytes().windows(2).any(|w| w == b"hi") {
        assert!(Instant::now() < end, "never written to the link");
        std::thread::sleep(Duration::from_millis(20));
    }
    let (c, out, _) = cli(&u.sock, &["grants"]);
    assert_eq!(c, 0);
    assert!(out.contains("agent:scout"));
    // agents cannot call owner methods
    assert_eq!(call(&u.sock, "identities.list", "agent:scout", json!({})).unwrap()["error"]["code"], "forbidden");
    assert_eq!(call(&u.sock, "discover", "agent:scout", json!({})).unwrap()["error"]["code"], "forbidden");
}

#[test]
fn receive_only_daemon_never_writes_a_send() {
    let u = up(true);
    wait_for(&u.sock, 4);
    let before = u.link.written_bytes();
    let (c, _, e) = cli(&u.sock, &["send", "--network", "meshtastic", "--text", "nope", "--channel", "0"]);
    assert_eq!(c, 0, "{e}");
    std::thread::sleep(Duration::from_millis(300));
    assert_eq!(u.link.written_bytes(), before);
    let r = call(&u.sock, "messages", "owner", json!({"filter": {"kind": "send_state"}})).unwrap();
    let _ = r;
    let _ = &u.d;
}

#[test]
fn listen_streams_new_records_as_they_are_stored() {
    let u = up(false);
    wait_for(&u.sock, 4);
    let mut s = UnixStream::connect(&u.sock).unwrap();
    writeln!(s, "{}", json!({"id": 9, "method": "subscribe", "principal": "owner"})).unwrap();
    let mut rd = BufReader::new(s.try_clone().unwrap());
    let mut first = String::new();
    rd.read_line(&mut first).unwrap();
    assert!(first.contains("subscribed"));
    // the radio says something new
    u.link.push(&frame(&frame_named(&fixture("meshtastic.json"), "text_direct_utf8")));
    let mut line = String::new();
    s.set_read_timeout(Some(Duration::from_secs(5))).unwrap();
    rd.read_line(&mut line).unwrap();
    let v: Value = serde_json::from_str(&line).unwrap();
    assert_eq!(v["event"], "record");
    assert_eq!(v["record"]["text"], "caf\u{e9} \u{2713}");
}

#[test]
fn the_socket_is_private_and_a_second_daemon_cannot_take_it() {
    use std::os::unix::fs::PermissionsExt;
    let u = up(false);
    let mode = std::fs::metadata(&u.sock).unwrap().permissions().mode() & 0o777;
    assert_eq!(mode, 0o600);
    let e = Engine::open(&u._t.path().join("data2"), "x").unwrap();
    let d2 = Arc::new(Daemon { engine: e, receive_only: true, stop: Arc::new(AtomicBool::new(false)) });
    assert!(d2.serve(&u.sock).is_err());
}

#[test]
fn malformed_requests_get_an_error_not_a_hang() {
    let u = up(false);
    let mut s = UnixStream::connect(&u.sock).unwrap();
    writeln!(s, "this is not json").unwrap();
    writeln!(s, "{}", json!({"id": 3, "method": "nope"})).unwrap();
    let mut rd = BufReader::new(s);
    let mut a = String::new();
    rd.read_line(&mut a).unwrap();
    assert!(a.contains("bad_request"));
    let mut b = String::new();
    rd.read_line(&mut b).unwrap();
    assert!(b.contains("unknown_method"));
}
