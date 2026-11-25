// SPDX-License-Identifier: AGPL-3.0-or-later
//! Access control on the API: who may read what, who may connect, how the
//! socket is created. Written red first (see the PR).
mod common;
use common::*;
use serde_json::{json, Value};
use std::sync::Arc;
use taos_mesh::api::Daemon;
use taos_mesh::engine::Engine;
use taos_mesh::grants::Grant;
use taos_mesh::model::*;

const NET: &str = "meshtastic";
const ME: &str = "!0000aaaa";

fn text(from: &str, to: &str, ch: u32, body: &str) -> Inbound {
    Inbound {
        raw: vec![1],
        event: Event::Text { text: body.into() },
        from: Some(from.into()),
        to: Some(to.into()),
        channel: Some(ch),
        packet_id: None,
        radio_ts: None,
        signal: Signal::default(),
        position: None,
        broadcast: to == "!ffffffff",
        trust: None,
    }
}

fn pos(from: &str) -> Inbound {
    let p = Position {
        lat: Some(51.5),
        lon: Some(-0.12),
        source: Some("packet".into()),
        ..Default::default()
    };
    Inbound {
        raw: vec![2],
        event: Event::Position(p.clone()),
        from: Some(from.into()),
        to: Some("!ffffffff".into()),
        channel: Some(0),
        packet_id: None,
        radio_ts: None,
        signal: Signal::default(),
        position: Some(p),
        broadcast: true,
        trust: None,
    }
}

fn world() -> (TmpDir, Arc<Daemon>) {
    let t = TmpDir::new("auth");
    let e = Engine::open(t.path(), "person:jay").unwrap();
    e.register_radio(ME, NET, "x", "usb");
    e.ingest(NET, ME, text("!00000b0b", "!ffffffff", 0, "public zero"))
        .unwrap();
    e.ingest(NET, ME, text("!00000b0b", "!ffffffff", 1, "team one"))
        .unwrap();
    e.ingest(NET, ME, text("!00000b0b", ME, 0, "secret direct"))
        .unwrap();
    e.ingest(NET, ME, pos("!00000b0b")).unwrap();
    // a later message carries the sender's last known position
    e.ingest(NET, ME, text("!00000b0b", "!ffffffff", 0, "after the fix"))
        .unwrap();
    let d = Arc::new(Daemon::new(e, false));
    (t, d)
}

fn call(d: &Daemon, who: &str, m: &str, p: Value) -> Value {
    d.handle_trusted(&json!({"id": 1, "method": m, "principal": who, "params": p}).to_string())
}

fn grant(d: &Daemon, kind: &str, ch: Option<u32>) {
    d.engine
        .grant_add(
            "owner",
            Grant {
                id: String::new(),
                kind: kind.into(),
                agent: "agent:evil".into(),
                network: NET.into(),
                channel: ch,
                max_msgs_per_hour: None,
                max_bytes_per_hour: None,
                expires_ms: None,
                created_ms: 0,
                created_by: String::new(),
            },
        )
        .unwrap();
}

fn texts(r: &Value) -> Vec<String> {
    r["result"]
        .as_array()
        .unwrap()
        .iter()
        .filter_map(|m| m["text"].as_str().map(String::from))
        .collect()
}

#[test]
fn an_agent_without_a_grant_reads_nothing_through_any_method() {
    let (_t, d) = world();
    for m in [
        "messages",
        "app.messages",
        "last_positions",
        "app.identities",
        "app.radios",
        "radios",
    ] {
        let r = call(&d, "agent:evil", m, json!({"filter": {}}));
        let empty = r["ok"] == false || r["result"].as_array().is_some_and(|a| a.is_empty());
        assert!(empty, "{m} leaked to an agent with no grant: {r}");
    }
}

#[test]
fn the_owner_sees_everything_through_the_app_view() {
    let (_t, d) = world();
    let r = call(&d, "owner", "app.messages", json!({}));
    assert_eq!(texts(&r).len(), 4);
    let r = call(&d, "owner", "app.identities", json!({}));
    assert!(r["result"]
        .as_array()
        .unwrap()
        .iter()
        .any(|x| x["hasPosition"] == true));
    assert!(!call(&d, "owner", "app.radios", json!({}))["result"]
        .as_array()
        .unwrap()
        .is_empty());
}

#[test]
fn a_channel_read_grant_shows_only_that_channels_broadcasts_and_never_direct_messages() {
    let (_t, d) = world();
    grant(&d, "read", Some(0));
    let r = call(&d, "agent:evil", "app.messages", json!({}));
    assert_eq!(texts(&r), vec!["public zero", "after the fix"], "{r}");
    let r = call(
        &d,
        "agent:evil",
        "messages",
        json!({"filter": {"kind": "text"}}),
    );
    assert_eq!(texts(&r), vec!["public zero", "after the fix"]);
}

#[test]
fn positions_need_their_own_grant_everywhere() {
    let (_t, d) = world();
    grant(&d, "read", Some(0));
    // channel 0 is readable, the position frame is on channel 0, but positions are a separate grant
    for m in ["messages", "last_positions"] {
        let r = call(&d, "agent:evil", m, json!({"filter": {}}));
        let rows = r["result"].as_array().unwrap();
        assert!(
            rows.iter()
                .all(|x| x["kind"] != "position" && x["position"].is_null()),
            "{m}: {r}"
        );
        if m == "last_positions" {
            assert!(rows.is_empty(), "{r}");
        }
    }
    let r = call(&d, "agent:evil", "app.identities", json!({}));
    assert!(
        r["result"]
            .as_array()
            .unwrap()
            .iter()
            .all(|x| x["hasPosition"] == false),
        "{r}"
    );
    // the text sent after the fix carries no position for the agent either
    let r = call(
        &d,
        "agent:evil",
        "messages",
        json!({"filter": {"kind": "text"}}),
    );
    assert!(
        r["result"]
            .as_array()
            .unwrap()
            .iter()
            .all(|x| x["position"].is_null()),
        "{r}"
    );
    let owner = call(&d, "owner", "messages", json!({"filter": {"kind": "text"}}));
    assert!(
        owner["result"]
            .as_array()
            .unwrap()
            .iter()
            .any(|x| !x["position"].is_null()),
        "the owner does see it: {owner}"
    );
    grant(&d, "position", None);
    let r = call(&d, "agent:evil", "last_positions", json!({}));
    assert_eq!(r["result"].as_array().unwrap().len(), 1, "{r}");
    let r = call(&d, "agent:evil", "app.identities", json!({}));
    assert!(r["result"]
        .as_array()
        .unwrap()
        .iter()
        .any(|x| x["hasPosition"] == true));
}

#[test]
fn health_does_not_reveal_where_the_store_is() {
    let (t, d) = world();
    let r = call(&d, "owner", "health", json!({}));
    let s = r["result"].to_string();
    assert!(!s.contains(t.path().to_str().unwrap()), "{s}");
    assert!(r["result"].get("store").is_none());
    assert_eq!(r["result"]["ok"], true);
}

// ---- who may connect, and as whom ------------------------------------------------

use std::io::{BufRead, BufReader, Write};
use std::os::unix::net::UnixStream;
use std::time::Duration;
use taos_mesh::auth::TokenVerifier;

fn verifier() -> TokenVerifier {
    TokenVerifier::new(vec![9u8; 32]).unwrap()
}

fn req(who: &str, token: Option<&str>, m: &str) -> String {
    json!({"id": 1, "method": m, "principal": who, "token": token, "params": {"filter": {}}})
        .to_string()
}

#[test]
fn a_non_owner_principal_is_refused_when_no_controller_is_configured() {
    let (_t, d) = world();
    for who in ["agent:evil", "person:jay", "person:anyone"] {
        let r = d.handle_request(&req(who, None, "messages"));
        assert_eq!(r["error"]["code"], "forbidden", "{who}: {r}");
        // even a well-formed token is no use without a key to check it against
        let tok = verifier().mint(who, now_ms() + 60_000, &["*"]);
        let r = d.handle_request(&req(who, Some(&tok), "messages"));
        assert_eq!(r["error"]["code"], "forbidden", "{who} with a token: {r}");
    }
    // the owner needs no token
    assert_eq!(
        d.handle_request(&req("owner", None, "messages"))["ok"],
        true
    );
}

#[test]
fn tokens_are_checked_for_signature_subject_expiry_and_method() {
    let (_t, d) = world();
    let mut d = Arc::try_unwrap(d).ok().unwrap();
    d.verifier = Some(verifier());
    let v = verifier();
    let now = now_ms();
    let ok = v.mint("agent:evil", now + 60_000, &["messages"]);
    assert_eq!(
        d.handle_request(&req("agent:evil", Some(&ok), "messages"))["ok"],
        true
    );
    let cases = [
        ("no token", None, "agent:evil", "messages"),
        (
            "for another principal",
            Some(v.mint("agent:other", now + 60_000, &["*"])),
            "agent:evil",
            "messages",
        ),
        (
            "expired",
            Some(v.mint("agent:evil", now - 1, &["*"])),
            "agent:evil",
            "messages",
        ),
        ("method not listed", Some(ok.clone()), "agent:evil", "send"),
        (
            "signed with another key",
            Some(TokenVerifier::new(vec![1u8; 32]).unwrap().mint(
                "agent:evil",
                now + 60_000,
                &["*"],
            )),
            "agent:evil",
            "messages",
        ),
        (
            "tampered payload",
            Some({
                let (p, m) = ok.split_once('.').unwrap();
                let mut p = p.to_string();
                p.replace_range(10..11, if &p[10..11] == "a" { "b" } else { "a" });
                format!("{p}.{m}")
            }),
            "agent:evil",
            "messages",
        ),
        (
            "garbage",
            Some("not a token".to_string()),
            "agent:evil",
            "messages",
        ),
    ];
    for (what, tok, who, m) in cases {
        let r = d.handle_request(&req(who, tok.as_deref(), m));
        assert_eq!(r["error"]["code"], "forbidden", "{what}: {r}");
    }
    // a wildcard token allows any method, but owner-only methods still refuse the agent
    let all = v.mint("agent:evil", now + 60_000, &["*"]);
    assert_eq!(
        d.handle_request(&req("agent:evil", Some(&all), "identities.list"))["error"]["code"],
        "forbidden"
    );
    // and the owner is never asked for a token
    assert_eq!(
        d.handle_request(&req("owner", None, "identities.list"))["ok"],
        true
    );
}

#[test]
fn a_short_controller_key_is_refused() {
    assert!(TokenVerifier::new(vec![1u8; 31]).is_err());
}

#[test]
fn the_key_file_must_be_private() {
    use std::os::unix::fs::PermissionsExt;
    let t = TmpDir::new("key");
    assert!(TokenVerifier::from_dir(t.path()).unwrap().is_none());
    let p = t.path().join("controller.key");
    std::fs::write(&p, [3u8; 32]).unwrap();
    std::fs::set_permissions(&p, std::fs::Permissions::from_mode(0o644)).unwrap();
    assert!(TokenVerifier::from_dir(t.path()).is_err());
    std::fs::set_permissions(&p, std::fs::Permissions::from_mode(0o600)).unwrap();
    assert!(TokenVerifier::from_dir(t.path()).unwrap().is_some());
}

fn serve_in(t: &TmpDir, d: Arc<Daemon>) -> std::path::PathBuf {
    let sock = t.path().join("s.sock");
    let s2 = sock.clone();
    std::thread::spawn(move || {
        let _ = d.serve(&s2);
    });
    let end = std::time::Instant::now() + Duration::from_secs(5);
    while UnixStream::connect(&sock).is_err() {
        assert!(std::time::Instant::now() < end, "socket never came up");
        std::thread::sleep(Duration::from_millis(10));
    }
    sock
}

#[test]
fn a_connection_from_another_user_is_refused_before_any_request() {
    let (t, d) = world();
    let mut d = Arc::try_unwrap(d).ok().unwrap();
    d.owner_uid = nix::unistd::geteuid().as_raw() + 1; // pretend the owner is someone else
    let sock = serve_in(&t, Arc::new(d));
    let mut s = UnixStream::connect(&sock).unwrap();
    s.set_read_timeout(Some(Duration::from_secs(5))).unwrap();
    let _ = writeln!(s, "{}", req("owner", None, "messages"));
    let mut line = String::new();
    BufReader::new(s).read_line(&mut line).unwrap();
    assert!(line.contains("forbidden"), "{line}");
    assert!(!line.contains("public zero"));
}

#[test]
fn the_socket_is_created_private_and_leaves_nothing_behind() {
    use std::os::unix::fs::PermissionsExt;
    let (t, d) = world();
    let sock = serve_in(&t, d);
    assert_eq!(
        std::fs::metadata(&sock).unwrap().permissions().mode() & 0o777,
        0o600
    );
    let leftovers: Vec<_> = std::fs::read_dir(t.path())
        .unwrap()
        .flatten()
        .filter(|e| {
            e.file_name()
                .to_string_lossy()
                .starts_with(".taos-mesh-bind")
        })
        .collect();
    assert!(leftovers.is_empty(), "{leftovers:?}");
}

#[test]
fn the_daemon_will_not_replace_a_file_that_is_not_a_socket() {
    let (t, d) = world();
    let p = t.path().join("important.txt");
    std::fs::write(&p, "keep me").unwrap();
    let e = d.serve(&p).unwrap_err();
    assert_eq!(e.kind(), std::io::ErrorKind::AlreadyExists, "{e}");
    assert_eq!(std::fs::read_to_string(&p).unwrap(), "keep me");
}

#[test]
fn the_daemon_will_not_put_its_socket_in_a_directory_others_can_write() {
    use std::os::unix::fs::PermissionsExt;
    let (t, d) = world();
    let dir = t.path().join("shared");
    std::fs::create_dir(&dir).unwrap();
    std::fs::set_permissions(&dir, std::fs::Permissions::from_mode(0o777)).unwrap();
    let e = d.serve(&dir.join("s.sock")).unwrap_err();
    assert_eq!(e.kind(), std::io::ErrorKind::PermissionDenied, "{e}");
}

#[test]
fn the_binary_refuses_to_start_without_a_runtime_directory() {
    let t = TmpDir::new("noxdg");
    let o = std::process::Command::new(env!("CARGO_BIN_EXE_taos-mesh"))
        .args(["daemon", "--data-dir"])
        .arg(t.path())
        .env_remove("XDG_RUNTIME_DIR")
        .output()
        .unwrap();
    assert_eq!(o.status.code(), Some(2));
    let msg = String::from_utf8_lossy(&o.stderr);
    assert!(msg.contains("XDG_RUNTIME_DIR"), "{msg}");
}

#[test]
fn controller_key_and_token_commands_make_tokens_the_daemon_accepts() {
    let t = TmpDir::new("ck");
    let bin = env!("CARGO_BIN_EXE_taos-mesh");
    let o = std::process::Command::new(bin)
        .args(["controller-key", "--data-dir"])
        .arg(t.path())
        .output()
        .unwrap();
    assert!(o.status.success(), "{}", String::from_utf8_lossy(&o.stderr));
    // never replaces an existing key
    assert!(!std::process::Command::new(bin)
        .args(["controller-key", "--data-dir"])
        .arg(t.path())
        .output()
        .unwrap()
        .status
        .success());
    let o = std::process::Command::new(bin)
        .args([
            "token",
            "--sub",
            "agent:scout",
            "--methods",
            "messages",
            "--data-dir",
        ])
        .arg(t.path())
        .output()
        .unwrap();
    let tok = String::from_utf8(o.stdout).unwrap().trim().to_string();
    let v = TokenVerifier::from_dir(t.path()).unwrap().unwrap();
    let c = v.verify(&tok, "agent:scout", "messages", now_ms()).unwrap();
    assert_eq!(c.methods, vec!["messages"]);
    assert!(v.verify(&tok, "agent:scout", "send", now_ms()).is_err());
}
