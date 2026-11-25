// SPDX-License-Identifier: AGPL-3.0-or-later
mod common;
use common::*;
use std::sync::Arc;
use taos_mesh::engine::{Engine, EngineError, Filter, Session};
use taos_mesh::grants::Grant;
use taos_mesh::link::FakeLink;
use taos_mesh::meshtastic::frame;
use taos_mesh::model::*;

const NET: &str = "meshtastic";

struct Rig {
    _t: TmpDir,
    e: Arc<Engine>,
    link: FakeLink,
    s: Session,
    id: String,
}

fn rig() -> Rig {
    let t = TmpDir::new("grants");
    let e = Engine::open(t.path(), "person:jay").unwrap();
    let link = FakeLink::new("fake0");
    let mut s = Session::new(e.clone(), Box::new(link.clone()), NET).unwrap();
    link.reply_on_write(&frame(&frame_named(&fixture("meshtastic.json"), "my_info_heltec_v2_1")));
    s.pump().unwrap();
    let id = s.radio_id().to_string();
    assert_eq!(id, "!28af67cc");
    Rig { _t: t, e, link, s, id }
}

fn grant(agent: &str, channel: Option<u32>, msgs: Option<u32>, bytes: Option<u32>, expires: Option<u64>) -> Grant {
    Grant { id: String::new(), kind: "send".into(), agent: agent.into(), network: NET.into(), channel, max_msgs_per_hour: msgs, max_bytes_per_hour: bytes, expires_ms: expires, created_ms: 0, created_by: String::new() }
}

#[test]
fn an_agent_without_a_grant_cannot_send_and_nothing_reaches_the_radio() {
    let mut r = rig();
    let before = r.link.written_bytes();
    let err = r.e.send_text("agent:scout", NET, None, None, Some(0), "hello").unwrap_err();
    assert!(matches!(err, EngineError::Denied(_)), "{err}");
    assert_eq!(err.code(), "no_grant");
    r.s.pump().unwrap();
    assert_eq!(r.link.written_bytes(), before, "not one byte was written after the refusal");
    // the refusal is itself audited
    let raw = taos_mesh::store::Store::read_all(r._t.path()).unwrap();
    let refused: Vec<_> = raw.iter().filter(|x| x.kind == "send_refused").collect();
    assert_eq!(refused.len(), 1);
    assert_eq!(refused[0].principal.as_deref(), Some("agent:scout"));
    assert!(refused[0].note.as_ref().unwrap().contains("no grant"));
}

#[test]
fn a_grant_is_per_channel() {
    let mut r = rig();
    r.e.grant_add("owner", grant("agent:scout", Some(2), None, None, None)).unwrap();
    assert!(r.e.send_text("agent:scout", NET, None, None, Some(2), "ok").is_ok());
    assert!(r.e.send_text("agent:scout", NET, None, None, Some(0), "nope").is_err());
    assert!(r.e.send_text("agent:scout", NET, None, Some("!00000b0b"), None, "a direct message needs an all-channel grant").is_err());
    r.s.pump().unwrap();
    // only the granted send reached the link
    let w = r.link.written_bytes();
    assert!(w.windows(2).any(|x| x == b"ok"));
    assert!(!w.windows(4).any(|x| x == b"nope"));
}

#[test]
fn only_the_owner_can_grant() {
    let r = rig();
    assert!(r.e.grant_add("agent:scout", grant("agent:scout", None, None, None, None)).is_err());
    assert!(r.e.grant_add("person:eve", grant("agent:scout", None, None, None, None)).is_err());
}

#[test]
fn the_airtime_budget_limits_messages_and_bytes_per_hour() {
    let r = rig();
    r.e.grant_add("owner", grant("agent:scout", None, Some(2), None, None)).unwrap();
    assert!(r.e.send_text("agent:scout", NET, None, None, Some(0), "one").is_ok());
    assert!(r.e.send_text("agent:scout", NET, None, None, Some(0), "two").is_ok());
    let e = r.e.send_text("agent:scout", NET, None, None, Some(0), "three").unwrap_err();
    assert!(e.to_string().contains("2 messages per hour"), "{e}");
    let r2 = rig();
    r2.e.grant_add("owner", grant("agent:b", None, None, Some(10), None)).unwrap();
    assert!(r2.e.send_text("agent:b", NET, None, None, Some(0), "123456").is_ok());
    let e = r2.e.send_text("agent:b", NET, None, None, Some(0), "12345").unwrap_err();
    assert!(e.to_string().contains("10 bytes per hour"), "{e}");
}

#[test]
fn the_budget_is_remembered_across_a_restart() {
    let r = rig();
    r.e.grant_add("owner", grant("agent:scout", None, Some(1), None, None)).unwrap();
    r.e.send_text("agent:scout", NET, None, None, Some(0), "one").unwrap();
    let dir = r._t.path().to_path_buf();
    drop(r.s);
    drop(r.e);
    let e = Engine::open(&dir, "person:jay").unwrap();
    // radio must exist for a send; the budget check comes first and refuses
    let err = e.send_text("agent:scout", NET, None, None, Some(0), "two").unwrap_err();
    assert!(matches!(err, EngineError::Denied(_)), "budget survived the restart: {err}");
}

#[test]
fn an_expired_or_revoked_grant_stops_working() {
    let r = rig();
    let g = r.e.grant_add("owner", grant("agent:scout", None, None, None, Some(now_ms() - 1))).unwrap();
    let e = r.e.send_text("agent:scout", NET, None, None, Some(0), "late").unwrap_err();
    assert!(e.to_string().contains("expired"), "{e}");
    let g2 = r.e.grant_add("owner", grant("agent:scout", None, None, None, None)).unwrap();
    assert!(r.e.send_text("agent:scout", NET, None, None, Some(0), "fine").is_ok());
    r.e.grant_revoke("owner", &g2.id).unwrap();
    r.e.grant_revoke("owner", &g.id).unwrap();
    assert!(r.e.send_text("agent:scout", NET, None, None, Some(0), "gone").is_err());
}

#[test]
fn the_owner_sends_without_a_grant_and_the_bytes_are_the_expected_framed_protobuf() {
    let mut r = rig();
    let rec = r.e.send_text("owner", NET, None, None, Some(0), "check in ok").unwrap();
    assert_eq!(rec.state.as_deref(), Some("queued"));
    // queued record is stored before the radio sees anything
    assert!(taos_mesh::store::Store::read_all(r._t.path()).unwrap().iter().any(|x| x.id == rec.id && x.dir == "out"));
    let before = r.link.written_bytes().len();
    r.s.pump().unwrap();
    let w = r.link.written_bytes();
    let sent = &w[before..];
    assert_eq!(&sent[..4], &[0x94, 0xC3, 0, (sent.len() - 4) as u8]);
    assert_eq!(to_hex(&sent[4..]), rec.raw_hex);
    // state moved queued -> sent, as appended records
    let all = taos_mesh::store::Store::read_all(r._t.path()).unwrap();
    let states: Vec<_> = all.iter().filter(|x| x.ref_id.as_deref() == Some(&rec.id)).map(|x| x.state.clone().unwrap()).collect();
    assert_eq!(states, vec!["sent"]);
    assert_eq!(r.e.radios()[0].frames_out, 1);
}

#[test]
fn receive_only_mode_never_writes_a_send_and_says_so() {
    let mut r = rig();
    r.s.transmit_enabled = false;
    let rec = r.e.send_text("owner", NET, None, None, Some(0), "should not go out").unwrap();
    let before = r.link.written_bytes();
    r.s.pump().unwrap();
    assert_eq!(r.link.written_bytes(), before);
    let all = taos_mesh::store::Store::read_all(r._t.path()).unwrap();
    let st = all.iter().find(|x| x.ref_id.as_deref() == Some(&rec.id)).unwrap();
    assert_eq!(st.state.as_deref(), Some("failed"));
    assert!(st.note.as_ref().unwrap().contains("receive-only"));
}

#[test]
fn a_write_error_on_the_link_marks_the_radio_disconnected_and_is_reported() {
    let r = rig();
    struct Broken;
    impl taos_mesh::link::Link for Broken {
        fn name(&self) -> String {
            "broken".into()
        }
        fn kind(&self) -> taos_mesh::link::LinkKind {
            taos_mesh::link::LinkKind::Serial
        }
        fn read(&mut self, _b: &mut [u8]) -> std::io::Result<usize> {
            Ok(0)
        }
        fn write_all(&mut self, _d: &[u8]) -> std::io::Result<()> {
            Err(std::io::Error::new(std::io::ErrorKind::BrokenPipe, "unplugged"))
        }
    }
    drop(r.s);
    let mut s = Session::new(r.e.clone(), Box::new(Broken), NET).unwrap();
    assert!(s.pump().is_err(), "the handshake write fails and is reported");
    let st = r.e.radios().into_iter().find(|x| x.link == "broken").unwrap();
    assert!(!st.connected);
    assert!(st.last_error.unwrap().contains("unplugged"));
}

#[test]
fn sends_validate_text_and_destination() {
    let r = rig();
    assert!(r.e.send_text("owner", NET, None, None, Some(0), "").is_err());
    assert!(r.e.send_text("owner", NET, None, None, Some(0), &"x".repeat(300)).is_err());
    assert!(r.e.send_text("owner", NET, None, Some("not-a-node"), None, "hi").is_err());
    assert!(r.e.send_text("owner", "reticulum", None, None, None, "hi").is_err());
    assert!(r.e.send_text("owner", "meshcore", None, None, None, "no meshcore radio connected").is_err());
}

#[test]
fn a_failed_store_write_means_the_send_is_refused_and_not_queued_later() {
    if !std::path::Path::new("/dev/full").exists() {
        return;
    }
    let t = TmpDir::new("sendfail");
    let store = taos_mesh::store::Store::open_file(std::path::Path::new("/dev/full")).unwrap();
    let e = Engine::open_with_store(t.path(), "owner", store).unwrap();
    let link = FakeLink::new("fake9");
    let _s = Session::new(e.clone(), Box::new(link), NET).unwrap();
    let err = e.send_text("owner", NET, None, None, Some(0), "x").unwrap_err();
    assert_eq!(err.code(), "store_error");
    assert_eq!(e.health()["pending_unwritten"], 0, "a send that was reported as failed is not stored later as queued");
}

#[test]
fn agents_read_only_what_the_owner_granted() {
    let r = rig();
    for (ch, body) in [(0, "public"), (1, "team")] {
        r.e.ingest(NET, &r.id, Inbound { raw: vec![1], event: Event::Text { text: body.into() }, from: Some("!00000b0b".into()), to: Some("!ffffffff".into()), channel: Some(ch), packet_id: None, radio_ts: None, signal: Signal::default(), position: None, broadcast: true }).unwrap();
    }
    assert!(r.e.messages("agent:scout", &Filter::default()).unwrap().is_empty(), "no read grant, no messages");
    let mut g = grant("agent:scout", Some(1), None, None, None);
    g.kind = "read".into();
    r.e.grant_add("owner", g).unwrap();
    let got = r.e.messages("agent:scout", &Filter { kind: Some("text".into()), ..Default::default() }).unwrap();
    assert_eq!(got.len(), 1);
    assert_eq!(got[0].text.as_deref(), Some("team"));
}
