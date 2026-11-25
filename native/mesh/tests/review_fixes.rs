// SPDX-License-Identifier: AGPL-3.0-or-later
//! Behaviours found in review: each test pins one.
mod common;
use common::*;
use serde_json::{json, Value};
use std::sync::Arc;
use taos_mesh::engine::{Engine, Filter, Session};
use taos_mesh::grants::Grant;
use taos_mesh::link::{FakeLink, Link, LinkKind};
use taos_mesh::meshcore;
use taos_mesh::meshtastic::{frame, MeshtasticDriver};
use taos_mesh::model::*;
use taos_mesh::proto::Writer;

const NET: &str = "meshtastic";

fn rig_net(net: &str, hello: &[u8]) -> (TmpDir, Arc<Engine>, FakeLink, Session) {
    let t = TmpDir::new("rf");
    let e = Engine::open(t.path(), "person:jay").unwrap();
    let link = FakeLink::new("fake0");
    let mut s = Session::new(e.clone(), Box::new(link.clone()), net).unwrap();
    link.reply_on_write(hello);
    s.pump().unwrap();
    (t, e, link, s)
}

fn mt() -> (TmpDir, Arc<Engine>, FakeLink, Session) {
    rig_net(
        NET,
        &frame(&frame_named(
            &fixture("meshtastic.json"),
            "my_info_heltec_v2_1",
        )),
    )
}

fn mc() -> (TmpDir, Arc<Engine>, FakeLink, Session) {
    let fx = fixture("meshcore.json");
    let si = hex(&fx["frames"][0]["hex"]);
    let mut b = vec![b'>', si.len() as u8, 0];
    b.extend(si);
    rig_net("meshcore", &b)
}

fn grant(kind: &str, agent: &str, ch: Option<u32>) -> Grant {
    Grant {
        id: String::new(),
        kind: kind.into(),
        agent: agent.into(),
        network: NET.into(),
        channel: ch,
        max_msgs_per_hour: None,
        max_bytes_per_hour: None,
        expires_ms: None,
        created_ms: 0,
        created_by: String::new(),
    }
}

fn inbound_text(from: &str, to: &str, ch: u32, body: &str) -> Inbound {
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

#[test]
fn a_channel_read_grant_does_not_expose_direct_messages() {
    let (_t, e, _l, s) = mt();
    let me = s.radio_id().to_string();
    // Meshtastic direct messages carry a channel index too (here 0)
    e.ingest(NET, &me, inbound_text("!00000b0b", &me, 0, "private note"))
        .unwrap();
    e.ingest(
        NET,
        &me,
        inbound_text("!00000b0b", "!ffffffff", 0, "public note"),
    )
    .unwrap();
    e.grant_add("owner", grant("read", "agent:scout", Some(0)))
        .unwrap();
    let got: Vec<_> = e
        .messages(
            "agent:scout",
            &Filter {
                kind: Some("text".into()),
                ..Default::default()
            },
        )
        .unwrap()
        .into_iter()
        .map(|r| r.text.unwrap())
        .collect();
    assert_eq!(got, vec!["public note"]);
    e.grant_add("owner", grant("read", "agent:scout", None))
        .unwrap();
    assert_eq!(
        e.messages(
            "agent:scout",
            &Filter {
                kind: Some("text".into()),
                ..Default::default()
            }
        )
        .unwrap()
        .len(),
        2
    );
}

#[test]
fn a_send_that_fails_after_the_grant_check_does_not_use_budget() {
    let t = TmpDir::new("budget");
    let e = Engine::open(t.path(), "person:jay").unwrap();
    let mut g = grant("send", "agent:scout", None);
    g.max_msgs_per_hour = Some(1);
    e.grant_add("owner", g).unwrap();
    // no radio is connected: the send fails after authorisation
    assert_eq!(
        e.send_text("agent:scout", NET, None, None, Some(0), "x")
            .unwrap_err()
            .code(),
        "no_radio"
    );
    let link = FakeLink::new("fake0");
    let _s = Session::new(e.clone(), Box::new(link), NET).unwrap();
    assert!(
        e.send_text("agent:scout", NET, None, None, Some(0), "x")
            .is_ok(),
        "the failed attempt did not consume the one allowed message"
    );
    assert!(e
        .send_text("agent:scout", NET, None, None, Some(0), "y")
        .is_err());
}

#[test]
fn packet_ids_never_repeat() {
    let mut seen = std::collections::HashSet::new();
    for _ in 0..20_000 {
        let id = taos_mesh::meshtastic::fresh_packet_id();
        assert_ne!(id, 0);
        assert!(seen.insert(id), "repeated packet id {id}");
    }
}

struct FailAfter {
    inner: FakeLink,
    writes: usize,
    ok_writes: usize,
}
impl Link for FailAfter {
    fn name(&self) -> String {
        self.inner.name()
    }
    fn kind(&self) -> LinkKind {
        LinkKind::Serial
    }
    fn read(&mut self, b: &mut [u8]) -> std::io::Result<usize> {
        self.inner.read(b)
    }
    fn write_all(&mut self, d: &[u8]) -> std::io::Result<()> {
        self.writes += 1;
        if self.writes > self.ok_writes {
            return Err(std::io::Error::new(
                std::io::ErrorKind::BrokenPipe,
                "unplugged",
            ));
        }
        self.inner.write_all(d)
    }
}

#[test]
fn when_the_link_dies_mid_batch_every_queued_send_ends_failed_not_queued_forever() {
    let t = TmpDir::new("batch");
    let e = Engine::open(t.path(), "person:jay").unwrap();
    let inner = FakeLink::new("fake0");
    inner.reply_on_write(&frame(&frame_named(
        &fixture("meshtastic.json"),
        "my_info_heltec_v2_1",
    )));
    let mut s = Session::new(
        e.clone(),
        Box::new(FailAfter {
            inner,
            writes: 0,
            ok_writes: 1,
        }),
        NET,
    )
    .unwrap();
    s.pump().unwrap(); // handshake is the one allowed write
    let ids: Vec<String> = ["a", "b", "c"]
        .iter()
        .map(|t| {
            e.send_text("owner", NET, None, None, Some(0), t)
                .unwrap()
                .id
        })
        .collect();
    assert!(s.pump().is_err());
    let msgs = e
        .messages(
            "owner",
            &Filter {
                kind: Some("text".into()),
                dir: Some("out".into()),
                ..Default::default()
            },
        )
        .unwrap();
    assert_eq!(msgs.len(), 3);
    for m in &msgs {
        assert!(ids.contains(&m.id));
        assert_eq!(
            m.delivery.as_deref(),
            Some("failed"),
            "{}",
            m.text.clone().unwrap()
        );
    }
}

#[test]
fn every_row_carries_the_owner() {
    let (t, e, _l, mut s) = mt();
    e.send_text("owner", NET, None, None, Some(0), "hi")
        .unwrap();
    let _ = e.send_text("agent:x", NET, None, None, Some(0), "refused");
    s.pump().unwrap();
    let rows = taos_mesh::store::Store::read_all(t.path()).unwrap();
    assert!(rows.len() >= 4);
    assert!(
        rows.iter().all(|r| r.owner == "person:jay"),
        "queued, sent, refused and inbound rows all have an owner"
    );
    let line = std::fs::read_to_string(t.path().join("records.jsonl")).unwrap();
    assert!(line.lines().all(|l| l.contains("\"owner\":\"person:jay\"")));
}

fn packet_bytes(build: impl Fn(&mut Writer)) -> Vec<u8> {
    let mut pkt = Writer::new();
    build(&mut pkt);
    let mut fr = Writer::new();
    fr.bytes(2, &pkt.finish());
    fr.finish()
}

fn data_text(w: &mut Writer, s: &str) {
    let mut d = Writer::new();
    d.varint(1, 1);
    d.bytes(2, s.as_bytes());
    w.bytes(4, &d.finish());
}

#[test]
fn a_missing_hop_limit_with_a_known_hop_start_means_all_hops_used() {
    // hop_limit 0 is a proto3 default and is not on the wire
    let raw = packet_bytes(|w| {
        w.fixed32(1, 5);
        w.fixed32(2, 0xffff_ffff);
        data_text(w, "far");
        w.fixed32(6, 9);
        w.varint(15, 3); // hop_start = 3, hop_limit absent
    });
    let inb = MeshtasticDriver::new().decode_frame(raw);
    assert_eq!(inb.signal.hops, Some(3));
    assert_eq!(inb.signal.hop_limit, Some(0));
    // and with no hop_start at all it is unknown
    let raw = packet_bytes(|w| {
        w.fixed32(1, 5);
        w.fixed32(2, 0xffff_ffff);
        data_text(w, "unknown");
    });
    assert_eq!(MeshtasticDriver::new().decode_frame(raw).signal.hops, None);
}

#[test]
fn trust_comes_only_from_flags_in_the_frame() {
    let signed = packet_bytes(|w| {
        w.fixed32(1, 5);
        w.fixed32(2, 0xffff_ffff);
        data_text(w, "s");
        w.varint(22, 1);
    });
    assert_eq!(
        MeshtasticDriver::new()
            .decode_frame(signed)
            .trust
            .as_deref(),
        Some("signed")
    );
    let pki = packet_bytes(|w| {
        w.fixed32(1, 5);
        w.fixed32(2, 7);
        data_text(w, "p");
        w.varint(17, 1);
    });
    assert_eq!(
        MeshtasticDriver::new().decode_frame(pki).trust.as_deref(),
        Some("encrypted")
    );
    let bc = packet_bytes(|w| {
        w.fixed32(1, 5);
        w.fixed32(2, 0xffff_ffff);
        data_text(w, "b");
    });
    assert_eq!(
        MeshtasticDriver::new().decode_frame(bc).trust.as_deref(),
        Some("claimed")
    );
    let dm = packet_bytes(|w| {
        w.fixed32(1, 5);
        w.fixed32(2, 7);
        data_text(w, "d");
    });
    assert_eq!(
        MeshtasticDriver::new().decode_frame(dm).trust,
        None,
        "a direct message without a flag: the frame does not say, so we do not either"
    );
}

fn routing_ack(from: u32, to: u32, request_id: u32, error: u32) -> Vec<u8> {
    let mut routing = Writer::new();
    if error != 0 {
        routing.varint(3, u64::from(error));
    }
    packet_bytes(|w| {
        w.fixed32(1, from);
        w.fixed32(2, to);
        let mut d = Writer::new();
        d.varint(1, 5);
        d.bytes(2, &routing.buf);
        d.fixed32(6, request_id);
        w.bytes(4, &d.buf.clone());
        w.fixed32(6, 1000 + from);
    })
}

#[test]
fn delivery_follows_the_acks_the_network_sends_back() {
    let (_t, e, link, mut s) = mt();
    let me = s.radio_id().to_string();
    let sent = e
        .send_text(
            "owner",
            NET,
            None,
            Some("!00000b0b"),
            Some(0),
            "are you there",
        )
        .unwrap();
    s.pump().unwrap();
    let delivery = |e: &Arc<Engine>, id: &str| {
        e.messages(
            "owner",
            &Filter {
                dir: Some("out".into()),
                ..Default::default()
            },
        )
        .unwrap()
        .into_iter()
        .find(|r| r.id == id)
        .unwrap()
        .delivery
        .unwrap()
    };
    assert_eq!(delivery(&e, &sent.id), "sent");
    let pid = sent
        .packet_id
        .expect("outbound records carry the packet id the acks refer to");
    // an implicit ack from a neighbour that relayed it
    link.push(&frame(&routing_ack(0x77, 0x28af_67cc, pid, 0)));
    s.pump().unwrap();
    assert_eq!(delivery(&e, &sent.id), "heard");
    // the recipient's own ack
    link.push(&frame(&routing_ack(0x0b0b, 0x28af_67cc, pid, 0)));
    s.pump().unwrap();
    assert_eq!(delivery(&e, &sent.id), "acknowledged");
    // a later NAK for a different packet is not applied to this one
    link.push(&frame(&routing_ack(0x0b0b, 0x28af_67cc, pid + 1, 1)));
    s.pump().unwrap();
    assert_eq!(delivery(&e, &sent.id), "acknowledged");
    let _ = me;
    // a NAK on a fresh send
    let s2 = e
        .send_text("owner", NET, None, Some("!00000b0b"), Some(0), "second")
        .unwrap();
    s.pump().unwrap();
    link.push(&frame(&routing_ack(
        0x0b0b,
        0x28af_67cc,
        s2.packet_id.unwrap(),
        1,
    )));
    s.pump().unwrap();
    assert_eq!(delivery(&e, &s2.id), "failed");
}

#[test]
fn meshcore_delivery_uses_the_expected_ack_code() {
    let (_t, e, link, mut s) = mc();
    let sent = e
        .send_text(
            "owner",
            "meshcore",
            None,
            Some("646566676869"),
            None,
            "ping",
        )
        .unwrap();
    s.pump().unwrap();
    // radio answers the send: RESP_SENT 06 | route flag | expected_ack | timeout
    let mut sent_resp = vec![6u8, 1];
    sent_resp.extend_from_slice(&0x0bad_f00du32.to_le_bytes());
    sent_resp.extend_from_slice(&1500u32.to_le_bytes());
    let mut b = vec![b'>', sent_resp.len() as u8, 0];
    b.extend(&sent_resp);
    link.push(&b);
    s.pump().unwrap();
    let d = |e: &Arc<Engine>| {
        e.messages(
            "owner",
            &Filter {
                dir: Some("out".into()),
                ..Default::default()
            },
        )
        .unwrap()[0]
            .delivery
            .clone()
            .unwrap()
    };
    assert_eq!(d(&e), "sent");
    let mut ack = vec![0x82u8];
    ack.extend_from_slice(&0x0bad_f00du32.to_le_bytes());
    ack.extend_from_slice(&900u32.to_le_bytes());
    let mut b = vec![b'>', ack.len() as u8, 0];
    b.extend(&ack);
    link.push(&b);
    s.pump().unwrap();
    assert_eq!(d(&e), "acknowledged");
    let _ = sent;
    let _ = meshcore::sync_next_message();
}

#[test]
fn receive_only_failure_is_visible_through_the_api_with_its_reason() {
    let (_t, e, _l, mut s) = mt();
    s.transmit_enabled = false;
    e.send_text("owner", NET, None, None, Some(0), "stays home")
        .unwrap();
    s.pump().unwrap();
    let d = Arc::new(taos_mesh::api::Daemon::new(e.clone(), true));
    let r: Value = d.handle_trusted(
        &json!({"id": 1, "method": "messages", "params": {"filter": {"dir": "state"}}}).to_string(),
    );
    let rows = r["result"].as_array().unwrap();
    assert!(rows.iter().any(|x| x["state"] == "failed" && x["note"].as_str().unwrap().contains("receive-only")), "{r}");
    // an agent never sees send progress
    let r: Value = d.handle_trusted(&json!({"id": 1, "method": "messages", "principal": "agent:x", "params": {"filter": {"dir": "state"}}}).to_string());
    assert!(r["result"].as_array().unwrap().is_empty());
}

#[test]
fn app_view_matches_the_survival_studio_interface() {
    let (_t, e, _l, mut s) = mt();
    let me = s.radio_id().to_string();
    let d = Arc::new(taos_mesh::api::Daemon::new(e.clone(), false));
    let call = |m: &str, p: Value, who: &str| -> Value {
        d.handle_trusted(&json!({"id": 1, "method": m, "principal": who, "params": p}).to_string())
    };
    // a neighbour, a stranger and a position
    let sam = e
        .with_ids("owner", |i| i.create_contact("Sam", None, "owner"))
        .unwrap();
    e.with_ids("owner", |i| {
        i.assign("!00000b0b", Some(&format!("contact:{sam}")), None, "owner")
    })
    .unwrap();
    e.with_ids("owner", |i| {
        i.assign("!00000c0c", Some("agent:scout"), Some("Scout"), "owner")
    })
    .unwrap();
    e.ingest(
        NET,
        &me,
        inbound_text("!00000b0b", "!ffffffff", 0, "hello team"),
    )
    .unwrap();
    e.ingest(NET, &me, inbound_text("!00000b0b", &me, 0, "just you"))
        .unwrap();
    e.ingest(
        NET,
        &me,
        inbound_text("!00000c0c", "!ffffffff", 1, "scout here"),
    )
    .unwrap();
    let pos = Position {
        lat: Some(51.5),
        lon: Some(-0.12),
        source: Some("packet".into()),
        ..Default::default()
    };
    e.ingest(
        NET,
        &me,
        Inbound {
            raw: vec![3],
            event: Event::Position(pos.clone()),
            from: Some("!00000b0b".into()),
            to: Some("!ffffffff".into()),
            channel: Some(0),
            packet_id: None,
            radio_ts: None,
            signal: Signal::default(),
            position: Some(pos),
            broadcast: true,
            trust: None,
        },
    )
    .unwrap();
    // radios
    let r = call("app.radios", json!({}), "owner");
    let radio = &r["result"][0];
    assert_eq!(radio["id"], me.as_str());
    assert_eq!(radio["transport"], "meshtastic");
    assert_eq!(radio["link"], "usb");
    assert_eq!(
        radio["state"], "connecting",
        "config not complete yet: not claimed as connected"
    );
    assert_eq!(radio["batteryPercent"], -1, "unknown, not 0");
    // identities
    let r = call("app.identities", json!({}), "owner");
    let ids = r["result"].as_array().unwrap();
    let sam_n = ids.iter().find(|x| x["id"] == "!00000b0b").unwrap();
    assert_eq!(sam_n["callsign"], "Sam");
    assert_eq!(sam_n["owner"], "", "a contact is not a taOS identity");
    assert_eq!(sam_n["hasPosition"], true);
    assert_eq!(sam_n["lat"], 51.5);
    assert_eq!(sam_n["isSelf"], false);
    let scout = ids.iter().find(|x| x["id"] == "!00000c0c").unwrap();
    assert_eq!(scout["owner"], "agent:scout");
    assert_eq!(scout["hasPosition"], false);
    assert!(ids
        .iter()
        .any(|x| x["id"] == me.as_str() && x["isSelf"] == true));
    // messages and threads
    let r = call("app.messages", json!({}), "owner");
    let m = r["result"].as_array().unwrap();
    assert_eq!(m.len(), 3);
    assert_eq!(m[0]["threadId"], "ch:meshtastic:0");
    assert_eq!(m[0]["outgoing"], false);
    assert_eq!(m[0]["trust"], "");
    assert_eq!(m[1]["threadId"], "!00000b0b");
    let r = call("app.messages", json!({"thread": "!00000b0b"}), "owner");
    assert_eq!(r["result"].as_array().unwrap().len(), 1);
    // send over the interface, then it shows up outgoing with a delivery
    let r = call(
        "app.send",
        json!({"thread": "!00000b0b", "text": "on my way"}),
        "owner",
    );
    assert_eq!(r["result"]["accepted"], true);
    s.pump().unwrap();
    let r = call("app.messages", json!({"thread": "!00000b0b"}), "owner");
    let last = r["result"].as_array().unwrap().last().unwrap().clone();
    assert_eq!(
        (last["outgoing"].clone(), last["delivery"].clone()),
        (json!(true), json!("sent"))
    );
    let r = call(
        "app.send",
        json!({"thread": "ch:meshtastic:2", "text": "to the team"}),
        "owner",
    );
    assert_eq!(r["result"]["accepted"], true);
    // an agent without a grant is refused in plain words
    let r = call(
        "app.send",
        json!({"thread": "ch:meshtastic:2", "text": "x"}),
        "agent:scout",
    );
    assert_eq!(r["result"]["accepted"], false);
    assert!(r["result"]["error"].as_str().unwrap().contains("no grant"));
    // once the radio reports its configuration the state is connected
    let mut link_cfg = vec![];
    let mut w = Writer::new();
    w.varint(7, 0x7a05_0001);
    link_cfg.extend(frame(&w.finish()));
    let (_t2, e2, l2, mut s2) = mt();
    l2.push(&link_cfg);
    s2.pump().unwrap();
    let d2 = Arc::new(taos_mesh::api::Daemon::new(e2, false));
    let r: Value =
        d2.handle_trusted(&json!({"id": 1, "method": "app.radios", "params": {}}).to_string());
    assert_eq!(r["result"][0]["state"], "connected");
}

#[test]
fn battery_comes_from_the_radios_own_device_telemetry() {
    let (_t, e, _l, s) = mt();
    let me = s.radio_id().to_string();
    e.ingest(
        NET,
        &me,
        Inbound {
            raw: vec![4],
            event: Event::Telemetry {
                kind: "device".into(),
                values: json!({"battery_level": 87}),
            },
            from: Some(me.clone()),
            to: Some("!ffffffff".into()),
            channel: Some(0),
            packet_id: None,
            radio_ts: None,
            signal: Signal::default(),
            position: None,
            broadcast: true,
            trust: None,
        },
    )
    .unwrap();
    let d = Arc::new(taos_mesh::api::Daemon::new(e, false));
    let r: Value =
        d.handle_trusted(&json!({"id": 1, "method": "app.radios", "params": {}}).to_string());
    assert_eq!(r["result"][0]["batteryPercent"], 87);
}
