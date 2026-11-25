// SPDX-License-Identifier: AGPL-3.0-or-later
//! Reading what a radio says about itself (firmware, board, region, channels) and writing settings back, through the
//! real path: fake link -> daemon thread -> unix socket API. Frames are encoded by the upstream reference library
//! (tests/fixtures/gen_device_fixtures.py). Nothing here touches a real radio.
mod common;
use common::*;
use serde_json::{json, Value};
use std::os::unix::net::UnixStream;
use std::path::{Path, PathBuf};
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
    _d: Arc<Daemon>,
}

/// What the radio sends after a handshake: its identity, firmware, settings and channels.
fn handshake_reply() -> Vec<u8> {
    let fx = fixture("meshtastic_device.json");
    let mut s = vec![];
    for n in [
        "my_info",
        "metadata",
        "node_info_self",
        "config_lora",
        "config_device",
        "config_position",
        "channel_0",
        "channel_1",
        "channel_2",
        "config_complete",
    ] {
        s.extend(frame(&frame_named(&fx, n)));
    }
    s
}

fn up(receive_only: bool) -> Up {
    let t = TmpDir::new("devcfg");
    let engine = Engine::open(&t.path().join("data"), "person:jay").unwrap();
    let d = Arc::new(Daemon::new(engine, receive_only));
    let sock = t.path().join("mesh.sock");
    let link = FakeLink::new("fake-devcfg");
    link.reply_on_write(&handshake_reply());
    d.attach(Box::new(link.clone()), "meshtastic", vec![])
        .unwrap();
    let d2 = d.clone();
    let s2 = sock.clone();
    std::thread::spawn(move || d2.serve(&s2).unwrap());
    let end = Instant::now() + Duration::from_secs(5);
    while UnixStream::connect(&sock).is_err() {
        assert!(Instant::now() < end, "socket never came up");
        std::thread::sleep(Duration::from_millis(10));
    }
    Up {
        _t: t,
        sock,
        link,
        _d: d,
    }
}

fn config(sock: &Path) -> Value {
    let end = Instant::now() + Duration::from_secs(5);
    loop {
        let r = call(
            sock,
            "device.config",
            "owner",
            json!({"radio": "!a1b2c3d4"}),
        )
        .unwrap();
        if r["ok"] == true && r["result"]["state"] == "ok" {
            return r["result"]["values"].clone();
        }
        assert!(Instant::now() < end, "config never became readable: {r}");
        std::thread::sleep(Duration::from_millis(20));
    }
}

#[test]
fn reads_firmware_board_region_channels_and_owner() {
    let u = up(false);
    let v = config(&u.sock);
    assert_eq!(v["network"], "meshtastic");
    assert_eq!(v["firmware"], "2.7.15.567b8ea");
    assert_eq!(v["hardware"], "Heltec V3");
    assert_eq!(v["owner"]["long_name"], "Jay Heltec");
    assert_eq!(v["lora"]["region"], "EU_868");
    assert_eq!(v["lora"]["modem_preset"], "LONG_FAST");
    assert_eq!(v["device"]["role"], "CLIENT");
    assert_eq!(v["position"]["broadcast_secs"], 900);
    let ch = v["channels"].as_array().unwrap();
    assert_eq!(ch.len(), 2, "a disabled channel is not listed: {ch:?}");
    assert_eq!(ch[0]["index"], 0);
    assert_eq!(ch[0]["role"], "PRIMARY");
    assert_eq!(ch[1]["name"], "family");
    assert_eq!(
        ch[1]["psk_b64"],
        "AQIDBAUGBwgJCgsMDQ4PEBESExQVFhcYGRobHB0eHyA="
    );
    assert!(ch[1]["share_url"]
        .as_str()
        .unwrap()
        .starts_with("https://meshtastic.org/e/#"));
    assert!(v["options"]["region"]
        .as_array()
        .unwrap()
        .contains(&json!("EU_868")));
    assert!(v["options"]["modem_preset"]
        .as_array()
        .unwrap()
        .contains(&json!("MEDIUM_FAST")));
}

#[test]
fn health_says_settings_can_be_read_and_written() {
    let u = up(false);
    let h = call(&u.sock, "health", "owner", json!({})).unwrap();
    assert_eq!(h["result"]["device_config"], true);
    assert_eq!(h["result"]["device_config_write"], true);
    let ro = up(true);
    let h = call(&ro.sock, "health", "owner", json!({})).unwrap();
    assert_eq!(h["result"]["device_config"], true);
    assert_eq!(
        h["result"]["device_config_write"], false,
        "receive-only never writes to a radio"
    );
}

#[test]
fn app_radios_carry_firmware_and_board() {
    let u = up(false);
    let _ = config(&u.sock);
    let r = call(&u.sock, "app.radios", "owner", json!({})).unwrap();
    let r0 = &r["result"][0];
    assert_eq!(r0["firmware"], "2.7.15.567b8ea");
    assert_eq!(r0["hardware"], "Heltec V3");
}

fn written(link: &FakeLink) -> Vec<u8> {
    link.written_bytes()
}

/// Wait until the session has written something new (it writes on its next cycle), then let it finish the batch.
fn wait_new(link: &FakeLink, before: usize) -> Vec<u8> {
    let end = Instant::now() + Duration::from_secs(5);
    while link.written_bytes().len() <= before {
        assert!(Instant::now() < end, "nothing was written to the radio");
        std::thread::sleep(Duration::from_millis(20));
    }
    std::thread::sleep(Duration::from_millis(300));
    link.written_bytes()[before..].to_vec()
}

fn contains(hay: &[u8], needle: &[u8]) -> bool {
    hay.windows(needle.len()).any(|w| w == needle)
}

#[test]
fn apply_writes_the_reference_encoded_admin_frames_in_one_transaction() {
    let u = up(false);
    let _ = config(&u.sock);
    let before = written(&u.link).len();
    let r = call(&u.sock, "device.apply", "owner", json!({"radio": "!a1b2c3d4", "confirm_region": true, "changes": {"lora.region": "US", "lora.modem_preset": "MEDIUM_FAST"}})).unwrap();
    assert_eq!(r["ok"], true, "{r}");
    assert_eq!(r["result"]["applied"].as_array().unwrap().len(), 2);
    let new = wait_new(&u.link, before);
    let fx = fixture("meshtastic_device.json");
    let b = hex(&fx["writes"]["begin"]);
    let s = hex(&fx["writes"]["set_lora_us_mediumfast"]);
    let c = hex(&fx["writes"]["commit"]);
    // the engine picks its own packet ids; compare everything but the 4 id bytes by decoding position-independently
    let strip = |x: &[u8]| -> Vec<u8> { taos_mesh::mt_device::without_packet_id(x) };
    let mut expect = vec![];
    for p in [&b, &s, &c] {
        expect.extend(frame(&strip(p)));
    }
    let got = taos_mesh::mt_device::strip_ids_from_stream(&new);
    assert_eq!(got, expect, "begin, set_config(lora), commit in that order");
}

#[test]
fn apply_owner_and_channel_name() {
    let u = up(false);
    let _ = config(&u.sock);
    let before = written(&u.link).len();
    let r = call(&u.sock, "device.apply", "owner", json!({"radio": "!a1b2c3d4", "changes": {"owner.long_name": "Trail Radio", "owner.short_name": "TR", "channels.1.name": "crew"}})).unwrap();
    assert_eq!(r["ok"], true, "{r}");
    let new = wait_new(&u.link, before);
    let fx = fixture("meshtastic_device.json");
    let got = taos_mesh::mt_device::strip_ids_from_stream(&new);
    let strip = |k: &str| {
        frame(&taos_mesh::mt_device::without_packet_id(&hex(
            &fx["writes"][k]
        )))
    };
    assert!(
        contains(&got, &strip("set_owner")),
        "set_owner frame missing"
    );
    assert!(
        contains(&got, &strip("set_channel_1_name_crew")),
        "set_channel frame missing"
    );
}

#[test]
fn apply_refusals_write_nothing() {
    let ro = up(true);
    let _ = config(&ro.sock);
    let before = written(&ro.link).len();
    let r = call(
        &ro.sock,
        "device.apply",
        "owner",
        json!({"radio": "!a1b2c3d4", "changes": {"lora.region": "US"}}),
    )
    .unwrap();
    assert_eq!(r["ok"], false);
    assert!(
        r["error"]["message"]
            .as_str()
            .unwrap()
            .contains("receive-only"),
        "{r}"
    );
    assert_eq!(written(&ro.link).len(), before);

    let u = up(false);
    let _ = config(&u.sock);
    let before = written(&u.link).len();
    for (path, val) in [
        ("lora.region", json!("MOON")),
        ("lora.hop_limit", json!(7)),
        ("channels.9.name", json!("x")),
        ("owner.long_name", json!("")),
    ] {
        let r = call(
            &u.sock,
            "device.apply",
            "owner",
            json!({"radio": "!a1b2c3d4", "changes": {path: val}}),
        )
        .unwrap();
        assert_eq!(r["ok"], false, "{path} must be refused: {r}");
    }
    assert_eq!(
        written(&u.link).len(),
        before,
        "a refused change writes nothing"
    );
    // agents never write settings
    let r = call(
        &u.sock,
        "device.apply",
        "agent:scout",
        json!({"radio": "!a1b2c3d4", "changes": {"lora.region": "US"}}),
    )
    .unwrap();
    assert_eq!(r["ok"], false);
}

#[test]
fn a_restarted_radio_is_handshaken_again_and_its_old_settings_are_not_kept() {
    let u = up(false);
    let _ = config(&u.sock);
    let before = written(&u.link).len();
    u.link.reply_on_write(&handshake_reply()); // the restarted radio answers the new handshake
                                               // FromRadio { rebooted: true }: field 8, varint 1 (hand-built from mesh.proto; the radio sends it after a restart)
    u.link.push(&frame(&[0x40, 0x01]));
    let new = wait_new(&u.link, before);
    // the wake bytes and a framed want_config_id went out again
    assert!(
        contains(
            &new,
            &frame(&taos_mesh::meshtastic::encode_want_config(0x7a05_0002))
        ),
        "no new handshake after a restart"
    );
    // and the radio, answering it, makes the settings readable again
    let _ = config(&u.sock);
}

fn apply(u: &Up, changes: Value) -> Value {
    call(
        &u.sock,
        "device.apply",
        "owner",
        json!({"radio": "!a1b2c3d4", "changes": changes}),
    )
    .unwrap()
}

#[test]
fn a_region_change_needs_an_explicit_confirmation_in_the_engine() {
    let u = up(false);
    let _ = config(&u.sock);
    let before = written(&u.link).len();
    let r = apply(&u, json!({"lora.region": "US"}));
    assert_eq!(r["ok"], false, "{r}");
    assert!(
        r["error"]["message"]
            .as_str()
            .unwrap()
            .contains("confirm_region"),
        "{r}"
    );
    assert_eq!(
        written(&u.link).len(),
        before,
        "nothing written without the confirmation"
    );
    let r = call(
        &u.sock,
        "device.apply",
        "owner",
        json!({"radio": "!a1b2c3d4", "confirm_region": true, "changes": {"lora.region": "US"}}),
    )
    .unwrap();
    assert_eq!(r["ok"], true, "{r}");
}

#[test]
fn unset_and_unknown_regions_are_refused_even_when_confirmed() {
    let u = up(false);
    let _ = config(&u.sock);
    let before = written(&u.link).len();
    for region in ["UNSET", "MOON", "", "us"] {
        let r = call(&u.sock, "device.apply", "owner", json!({"radio": "!a1b2c3d4", "confirm_region": true, "changes": {"lora.region": region}})).unwrap();
        assert_eq!(r["ok"], false, "{region} must be refused: {r}");
    }
    assert_eq!(written(&u.link).len(), before);
}

#[test]
fn the_cli_needs_confirm_region_for_a_region() {
    let u = up(false);
    let _ = config(&u.sock);
    let run = |args: &[&str]| {
        std::process::Command::new(env!("CARGO_BIN_EXE_taos-mesh"))
            .arg("--socket")
            .arg(&u.sock)
            .args(args)
            .output()
            .unwrap()
    };
    let o = run(&["device-apply", "!a1b2c3d4", "lora.region=US"]);
    assert_eq!(o.status.code(), Some(1), "refused without the flag");
    assert!(String::from_utf8_lossy(&o.stderr).contains("confirm_region"));
    let o = run(&[
        "device-apply",
        "!a1b2c3d4",
        "lora.region=US",
        "--confirm-region",
    ]);
    assert_eq!(
        o.status.code(),
        Some(0),
        "{}",
        String::from_utf8_lossy(&o.stderr)
    );
}
