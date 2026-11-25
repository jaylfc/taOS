// SPDX-License-Identifier: AGPL-3.0-or-later
//! The local JSON API, driven over a real socket.
mod common;
use common::*;
use serde_json::{json, Value};
use std::io::{BufRead, BufReader, Read, Write};
use std::net::TcpStream;
use std::process::{Child, Stdio};

struct Api {
    child: Child,
    port: u16,
    token: String,
}

impl Drop for Api {
    fn drop(&mut self) {
        let _ = self.child.kill();
        let _ = self.child.wait();
    }
}

fn mint(env: &Env, action: &str, subject: &str) -> String {
    let (c, v) = env.run(&["owner", "mint", "--action", action, "--subject", subject]);
    assert_eq!(c, 0, "{v}");
    v["nonce"].as_str().unwrap().to_string()
}

fn start(env: &Env) -> Api {
    start_with(env, &[])
}

fn start_with(env: &Env, envs: &[(&str, &str)]) -> Api {
    let mut cmd = env.cmd();
    for (k, v) in envs {
        cmd.env(k, v);
    }
    let mut c = cmd
        .args(["serve", "--port", "0"])
        .stdout(Stdio::piped())
        .spawn()
        .unwrap();
    let mut line = String::new();
    BufReader::new(c.stdout.take().unwrap())
        .read_line(&mut line)
        .unwrap();
    let v: Value = serde_json::from_str(&line).unwrap();
    let token = std::fs::read_to_string(env.data.join("api-token")).unwrap();
    Api {
        child: c,
        port: v["port"].as_u64().unwrap() as u16,
        token,
    }
}

fn call(
    a: &Api,
    method: &str,
    path: &str,
    body: Option<Value>,
    auth: bool,
    extra: &str,
    host: Option<&str>,
) -> (u16, Value) {
    let mut s = TcpStream::connect(("127.0.0.1", a.port)).unwrap();
    let body = body.map(|b| b.to_string()).unwrap_or_default();
    let host = host
        .map(String::from)
        .unwrap_or(format!("127.0.0.1:{}", a.port));
    let au = if auth {
        format!("Authorization: Bearer {}\r\n", a.token)
    } else {
        String::new()
    };
    write!(
        s,
        "{method} {path} HTTP/1.1\r\nHost: {host}\r\n{au}{extra}Content-Length: {}\r\n\r\n{body}",
        body.len()
    )
    .unwrap();
    let mut out = String::new();
    s.read_to_string(&mut out).unwrap();
    let status: u16 = out.split_whitespace().nth(1).unwrap().parse().unwrap();
    let json = out
        .split("\r\n\r\n")
        .nth(1)
        .and_then(|b| serde_json::from_str(b).ok())
        .unwrap_or(Value::Null);
    (status, json)
}

#[test]
fn the_api_needs_the_token_and_refuses_browsers() {
    let srv = Server::start();
    let env = Env::new(&srv.base);
    let a = start(&env);
    assert_eq!(call(&a, "GET", "/v1/status", None, false, "", None).0, 401);
    assert_eq!(
        call(
            &a,
            "GET",
            "/v1/status",
            None,
            true,
            "Origin: http://evil.example\r\n",
            None
        )
        .0,
        403
    );
    assert_eq!(
        call(
            &a,
            "GET",
            "/v1/status",
            None,
            true,
            "",
            Some("evil.example")
        )
        .0,
        403,
        "DNS rebinding host"
    );
    let (s, v) = call(&a, "GET", "/v1/status", None, true, "", None);
    assert_eq!(s, 200);
    assert_eq!(v["service"], "taos-firmware");
    assert_eq!(call(&a, "GET", "/v1/nope", None, true, "", None).0, 404);
    use std::os::unix::fs::PermissionsExt;
    let mode = std::fs::metadata(env.data.join("api-token"))
        .unwrap()
        .permissions()
        .mode()
        & 0o777;
    assert_eq!(mode, 0o600);
}

#[test]
fn an_app_can_run_the_whole_flow_over_the_api() {
    let srv = Server::start();
    publish_all(&srv, None);
    let env = Env::new(&srv.base);
    env.add_usb("1-1", "10c4", "ea60", "SN1", "ttyUSB0");
    let a = start(&env);
    let c = |m: &str, p: &str, b: Option<Value>| call(&a, m, p, b, true, "", None);
    let (_, v) = c("GET", "/v1/boards", None);
    assert!(v["boards"]
        .as_array()
        .unwrap()
        .iter()
        .any(|b| b["id"] == "heltec-v3"));
    // Licences first, then the refresh.
    let (_, v) = c("GET", "/v1/licences", None);
    assert_eq!(v["pending"].as_array().unwrap().len(), 3);
    let (_, v) = c(
        "POST",
        "/v1/library/refresh",
        Some(json!({"network":"meshtastic"})),
    );
    assert_eq!(v["reports"][0]["status"], "licence_required");
    for n in ["meshtastic", "meshcore", "rnode"] {
        let (s, _) = c(
            "POST",
            "/v1/licences/accept",
            Some(
                json!({"network":n,"by":"Test Owner","owner_nonce":mint(&env,"licence.accept",n)}),
            ),
        );
        assert_eq!(s, 200);
    }
    let (s, _) = c(
        "POST",
        "/v1/licences/accept",
        Some(json!({"network":"meshcore","owner_nonce":mint(&env,"licence.accept","meshcore")})),
    );
    assert_eq!(s, 400, "someone must be named");
    let (_, v) = c("POST", "/v1/library/refresh", Some(json!({})));
    assert!(
        v["reports"]
            .as_array()
            .unwrap()
            .iter()
            .all(|r| r["status"] == "ok"),
        "{v}"
    );
    let (_, v) = c("GET", "/v1/library?board=heltec-v3&network=meshcore", None);
    assert_eq!(entry_ids(&v).len(), 4, "two versions, ble and usb");
    let (_, v) = c("GET", "/v1/devices?probe=1", None);
    assert_eq!(v["devices"][0]["id"], "usb-10c4-ea60-SN1-1-1");
    let (_, v) = c(
        "GET",
        "/v1/devices/usb-10c4-ea60-SN1-1-1/firmware?probe=1",
        None,
    );
    assert!(!v["firmware"].as_array().unwrap().is_empty());
    let body =
        json!({"device":"usb-10c4-ea60-SN1-1-1","entry":"meshtastic:heltec-v3:2.7.26.54e0d8d"});
    let (_, v) = c("POST", "/v1/flash/plan", Some(body.clone()));
    assert!(
        v["plan"].get("confirm_phrase").is_none(),
        "the plan does not repeat the confirmation"
    );
    assert_eq!(v["plan"]["requires_unsigned_ack"], true);
    let subject = "usb-10c4-ea60-SN1-1-1|meshtastic:heltec-v3:2.7.26.54e0d8d";
    let mut run = body.clone();
    run["owner_nonce"] = mint(&env, "flash.run", subject).into();
    let (_, v) = c("POST", "/v1/flash/run", Some(run.clone()));
    assert_eq!(v["code"], "confirmation_required");
    run["owner_nonce"] = mint(&env, "flash.run", subject).into();
    run["confirm"] = "Heltec V3".into();
    run["actor"] = "agent:scout".into();
    let (_, v) = c("POST", "/v1/flash/run", Some(run.clone()));
    assert_eq!(v["code"], "consent_required");
    let (s, _) = c(
        "POST",
        "/v1/consent/grant",
        Some(
            json!({"agent":"scout","by":"Test Owner","minutes":5,"owner_nonce":mint(&env,"consent.grant","scout")}),
        ),
    );
    assert_eq!(s, 200);
    run["owner_nonce"] = mint(&env, "flash.run", subject).into();
    let (_, v) = c("POST", "/v1/flash/run", Some(run.clone()));
    assert_eq!(v["code"], "unsigned_ack_required");
    run["owner_nonce"] = mint(&env, "flash.run", subject).into();
    run["ack_unsigned"] = true.into();
    let (_, v) = c("POST", "/v1/flash/run", Some(run));
    assert_eq!(v["ok"], true, "{v}");
    assert_eq!(v["verify"]["status"], "confirmed");
    let (_, v) = c("GET", "/v1/log?limit=5", None);
    assert!(v["log"]
        .as_array()
        .unwrap()
        .iter()
        .any(|l| l["event"] == "flash-end"));
    let (_, v) = c("GET", "/v1/backups", None);
    assert_eq!(v["backups"].as_array().unwrap().len(), 1);
}

#[test]
fn a_probe_never_runs_while_a_flash_is_writing() {
    let srv = Server::start();
    publish_all(&srv, None);
    let env = Env::new(&srv.base);
    env.accept_all();
    env.refresh_all();
    env.add_usb("1-1", "10c4", "ea60", "SN1", "ttyUSB0");
    let a = start_with(&env, &[("FAKE_FLASH_SLEEP", "2")]);
    let (port, token) = (a.port, a.token.clone());
    let nonce = mint(
        &env,
        "flash.run",
        "usb-10c4-ea60-SN1-1-1|meshtastic:heltec-v3:2.7.26.54e0d8d",
    );
    let t = std::thread::spawn(move || {
        let b = Api {
            child: std::process::Command::new("true").spawn().unwrap(),
            port,
            token,
        };
        call(
            &b,
            "POST",
            "/v1/flash/run",
            Some(
                json!({"device":"usb-10c4-ea60-SN1-1-1","entry":"meshtastic:heltec-v3:2.7.26.54e0d8d","confirm":"Heltec V3","ack_unsigned":true,"owner_nonce":nonce}),
            ),
            true,
            "",
            None,
        )
    });
    std::thread::sleep(std::time::Duration::from_millis(900));
    let probes = || {
        env.tool_log()
            .iter()
            .filter(|l| l.contains("chip_id"))
            .count()
    };
    let before = probes();
    let (s, v) = call(
        &a,
        "POST",
        "/v1/flash/plan",
        Some(
            json!({"device":"usb-10c4-ea60-SN1-1-1","entry":"meshtastic:heltec-v3:2.7.26.54e0d8d"}),
        ),
        true,
        "",
        None,
    );
    assert_eq!((s, v["code"].as_str()), (409, Some("busy")), "{v}");
    let (s, v) = call(&a, "GET", "/v1/devices?probe=1", None, true, "", None);
    assert_eq!(s, 200);
    assert_eq!(v["probe_skipped_busy"], true, "{v}");
    assert_eq!(v["devices"][0]["probe"], "skipped");
    let (s, v) = call(
        &a,
        "GET",
        "/v1/devices/usb-10c4-ea60-SN1-1-1/firmware?probe=1",
        None,
        true,
        "",
        None,
    );
    assert_eq!(s, 200, "{v}");
    assert_eq!(probes(), before, "no board was probed during the write");
    let (_, run) = t.join().unwrap();
    assert_eq!(run["ok"], true, "{run}");
}

#[test]
fn consent_licence_and_flash_need_the_owner_nonce_not_just_the_token() {
    use std::os::unix::fs::PermissionsExt;
    let srv = Server::start();
    let env = Env::new(&srv.base);
    let a = start(&env);
    let c = |p: &str, b: Value| call(&a, "POST", p, Some(b), true, "", None);
    // The bearer token alone is refused everywhere that matters.
    assert_eq!(
        c("/v1/licences/accept", json!({"network":"rnode","by":"x"})).0,
        403
    );
    assert_eq!(
        c(
            "/v1/consent/grant",
            json!({"agent":"a","by":"x","minutes":5})
        )
        .0,
        403
    );
    assert_eq!(
        c(
            "/v1/flash/run",
            json!({"device":"d","entry":"e","actor":"person"})
        )
        .0,
        403
    );
    // A nonce is scoped to its action and subject, and used up by one try.
    let n = mint(&env, "licence.accept", "rnode");
    assert_eq!(
        c(
            "/v1/licences/accept",
            json!({"network":"meshcore","by":"x","owner_nonce":n})
        )
        .0,
        403
    );
    assert_eq!(
        c(
            "/v1/licences/accept",
            json!({"network":"rnode","by":"x","owner_nonce":n})
        )
        .0,
        403,
        "already spent"
    );
    let n = mint(&env, "consent.grant", "scout");
    assert_eq!(
        c(
            "/v1/licences/accept",
            json!({"network":"rnode","by":"x","owner_nonce":n})
        )
        .0,
        403,
        "wrong action"
    );
    let n = mint(&env, "licence.accept", "rnode");
    assert_eq!(
        c(
            "/v1/licences/accept",
            json!({"network":"rnode","by":"Owner","owner_nonce":n})
        )
        .0,
        200
    );
    // An unknown action cannot be minted, and the socket is private.
    let (code, _) = env.run(&["owner", "mint", "--action", "anything", "--subject", "x"]);
    assert_eq!(code, 1);
    let mode = std::fs::metadata(env.data.join("owner.sock"))
        .unwrap()
        .permissions()
        .mode()
        & 0o777;
    assert_eq!(mode, 0o600);
}
