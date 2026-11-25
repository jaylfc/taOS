// SPDX-License-Identifier: AGPL-3.0-or-later
mod common;
use common::*;
use std::sync::Arc;
use taos_mesh::engine::{Engine, Filter, Session};
use taos_mesh::link::FakeLink;
use taos_mesh::meshtastic::{frame, node_name};
use taos_mesh::model::*;

const ME: &str = "!0000aaaa"; // the owner's own radio
const SAM: &str = "!00000b0b"; // a neighbour's handset
const NET: &str = "meshtastic";

fn text(from: &str, to: &str, body: &str, ch: u32) -> Inbound {
    Inbound {
        raw: body.as_bytes().to_vec(),
        event: Event::Text { text: body.into() },
        from: Some(from.into()),
        to: Some(to.into()),
        channel: Some(ch),
        packet_id: None,
        radio_ts: None,
        signal: Signal { rssi: Some(-100), snr: Some(1.0), hops: Some(0), ..Default::default() },
        position: None,
        broadcast: to == "!ffffffff",
        trust: None,
    }
}

fn pos(from: &str, lat: f64, lon: f64) -> Inbound {
    let p = Position { lat: Some(lat), lon: Some(lon), alt_m: Some(10), time: Some(1_764_000_000), source: Some("packet".into()), ..Default::default() };
    Inbound { raw: vec![7], event: Event::Position(p.clone()), from: Some(from.into()), to: Some("!ffffffff".into()), channel: Some(0), packet_id: None, radio_ts: None, signal: Signal::default(), position: Some(p), broadcast: true, trust: None }
}

fn telem(from: &str) -> Inbound {
    Inbound { raw: vec![9], event: Event::Telemetry { kind: "device".into(), values: serde_json::json!({"battery_level": 50}) }, from: Some(from.into()), to: Some("!ffffffff".into()), channel: Some(0), packet_id: None, radio_ts: None, signal: Signal::default(), position: None, broadcast: true, trust: None }
}

struct World {
    t: TmpDir,
    e: Arc<Engine>,
    sam: String,
}

fn world() -> World {
    let t = TmpDir::new("ids");
    let e = Engine::open(t.path(), "person:jay").unwrap();
    e.with_ids("owner", |i| i.assign(ME, Some("person:jay"), Some("Jay"), "owner")).unwrap();
    let sam = e.with_ids("owner", |i| i.create_contact("Sam", Some("neighbour, orange handset"), "owner")).unwrap();
    e.with_ids("owner", |i| i.assign(SAM, Some(&format!("contact:{sam}")), None, "owner")).unwrap();
    World { t, e, sam }
}

#[test]
fn messages_are_attributed_to_the_identity_behind_the_radio() {
    let w = world();
    let r = w.e.ingest(NET, ME, text(SAM, ME, "hello jay", 0)).unwrap();
    assert_eq!(r.sender.as_ref().unwrap().identity, format!("contact:{}", w.sam));
    assert_eq!(r.sender.as_ref().unwrap().name, "Sam");
    assert_eq!(r.observer.as_deref(), Some("person:jay"));
    assert_eq!(r.recipient.as_ref().unwrap().identity, "person:jay");
}

#[test]
fn agent_radios_are_attributed_to_the_agent() {
    let w = world();
    w.e.with_ids("owner", |i| i.assign("!0000c0c0", Some("agent:scout"), Some("Scout"), "owner")).unwrap();
    let r = w.e.ingest(NET, ME, text("!0000c0c0", "!ffffffff", "status ok", 0)).unwrap();
    assert_eq!(r.sender.unwrap().identity, "agent:scout");
}

#[test]
fn history_before_an_assignment_is_attributed_when_queried_but_old_stamps_stay() {
    let t = TmpDir::new("retro");
    let e = Engine::open(t.path(), "person:jay").unwrap();
    let r = e.ingest(NET, ME, text(SAM, ME, "who am i", 0)).unwrap();
    assert!(r.sender.is_none());
    let c = e.with_ids("owner", |i| i.create_contact("Sam", None, "owner")).unwrap();
    e.with_ids("owner", |i| i.assign(SAM, Some(&format!("contact:{c}")), None, "owner")).unwrap();
    let got = e.messages("person:jay", &Filter { identity: Some(format!("contact:{c}")), ..Default::default() }).unwrap();
    assert_eq!(got.len(), 1, "the earlier message now resolves to Sam");
    // reassign the handset to someone else: a new frame follows the new owner, the stored one keeps Sam
    e.ingest(NET, ME, text(SAM, ME, "still sam", 0)).unwrap();
    let c2 = e.with_ids("owner", |i| i.create_contact("Pat", None, "owner")).unwrap();
    e.with_ids("owner", |i| i.assign(SAM, Some(&format!("contact:{c2}")), None, "owner")).unwrap();
    e.ingest(NET, ME, text(SAM, ME, "now pat", 0)).unwrap();
    let all = e.messages("person:jay", &Filter::default()).unwrap();
    let who: Vec<String> = all.iter().map(|r| r.sender.as_ref().unwrap().name.clone()).collect();
    assert_eq!(who, vec!["Pat", "Sam", "Pat"], "unstamped history follows the current assignment; stamped frames keep who they were");
}

#[test]
fn filters_by_identity_channel_and_time() {
    let w = world();
    w.e.ingest(NET, ME, text(SAM, "!ffffffff", "public chatter", 0)).unwrap();
    w.e.ingest(NET, ME, text(SAM, ME, "private note", 1)).unwrap();
    w.e.ingest(NET, ME, text("!00000999", "!ffffffff", "stranger", 0)).unwrap();
    let sam = format!("contact:{}", w.sam);
    let by_id = w.e.messages("owner", &Filter { identity: Some(sam.clone()), ..Default::default() }).unwrap();
    assert_eq!(by_id.len(), 2);
    let by_ch = w.e.messages("owner", &Filter { channel: Some(1), ..Default::default() }).unwrap();
    assert_eq!(by_ch.len(), 1);
    assert_eq!(by_ch[0].text.as_deref(), Some("private note"));
    let none = w.e.messages("owner", &Filter { since_ms: Some(now_ms() + 100_000), ..Default::default() }).unwrap();
    assert!(none.is_empty());
    let limited = w.e.messages("owner", &Filter { limit: Some(1), newest_first: Some(true), ..Default::default() }).unwrap();
    assert_eq!(limited[0].text.as_deref(), Some("stranger"));
}

/// Jay's radio hears Sam: a DM, an overheard public message, a position
/// broadcast and telemetry.
fn conversation(w: &World) {
    w.e.ingest(NET, ME, text(SAM, ME, "sam to jay (dm)", 1)).unwrap();
    w.e.ingest(NET, ME, text(SAM, "!ffffffff", "sam on the public channel", 0)).unwrap();
    w.e.ingest(NET, ME, pos(SAM, 51.5, -0.12)).unwrap();
    w.e.ingest(NET, ME, telem(SAM)).unwrap();
    // somebody else's public chatter that Jay's radio merely overheard
    w.e.ingest(NET, ME, text("!00000999", "!ffffffff", "stranger on the public channel", 0)).unwrap();
}

#[test]
fn before_linking_the_contact_has_no_view_and_the_owner_has_everything() {
    let w = world();
    conversation(&w);
    assert!(w.e.messages("person:sam", &Filter::default()).unwrap().is_empty());
    assert_eq!(w.e.messages("person:jay", &Filter::default()).unwrap().len(), 5);
}

#[test]
fn linking_gives_the_account_its_radios_history_but_not_other_peoples_overheard_traffic() {
    let w = world();
    conversation(&w);
    w.e.with_ids("owner", |i| i.link(&w.sam, "person:sam", "owner")).unwrap();
    let seen = w.e.messages("person:sam", &Filter::default()).unwrap();
    let kinds: Vec<(&str, &str)> = seen.iter().map(|r| (r.kind.as_str(), r.text.as_deref().unwrap_or(""))).collect();
    // everything Sam's radio sent or was sent directly
    assert!(kinds.contains(&("text", "sam to jay (dm)")), "{kinds:?}");
    assert!(kinds.contains(&("text", "sam on the public channel")), "{kinds:?}");
    assert!(kinds.contains(&("position", "")), "{kinds:?}");
    assert!(kinds.contains(&("telemetry", "")), "{kinds:?}");
    // but not what other people said that Jay's radio overheard
    assert!(!kinds.iter().any(|(_, t)| t.contains("stranger")), "overheard traffic from others stays with the observer: {kinds:?}");
    assert_eq!(seen.len(), 4);
    // the observer keeps everything
    assert_eq!(w.e.messages("person:jay", &Filter::default()).unwrap().len(), 5);
    // an unrelated account still sees nothing
    assert!(w.e.messages("person:eve", &Filter::default()).unwrap().is_empty());
}

#[test]
fn both_sides_of_a_shared_conversation_reach_the_linked_account_including_the_owners_replies() {
    let w = world();
    // the owner's reply is an outbound record to Sam's handset
    let link = FakeLink::new("fake1");
    let mut s = Session::new(w.e.clone(), Box::new(link.clone()), NET).unwrap();
    link.reply_on_write(&frame(&common::frame_named(&fixture("meshtastic.json"), "my_info_heltec_v2_1")));
    s.pump().unwrap();
    let my_id = s.radio_id().to_string();
    w.e.with_ids("owner", |i| i.assign(&my_id, Some("person:jay"), Some("Jay"), "owner")).unwrap();
    s.pump().unwrap();
    w.e.send_text("owner", NET, None, Some(SAM), Some(0), "jay to sam (dm)").unwrap();
    w.e.send_text("owner", NET, None, None, Some(0), "jay to everyone").unwrap();
    w.e.ingest(NET, &my_id, text(SAM, &my_id, "sam to jay (dm)", 0)).unwrap();
    w.e.with_ids("owner", |i| i.link(&w.sam, "person:sam", "owner")).unwrap();
    let seen: Vec<String> = w.e.messages("person:sam", &Filter { kind: Some("text".into()), ..Default::default() }).unwrap().into_iter().map(|r| r.text.unwrap()).collect();
    assert_eq!(seen, vec!["jay to sam (dm)", "sam to jay (dm)"], "both directions of the conversation; Jay's own broadcast stays with Jay");
}

#[test]
fn told_flag_and_date_are_recorded_by_the_owner() {
    let w = world();
    let c = &w.e.identities()["contacts"][0];
    assert_eq!(c["positions_told"], false);
    w.e.with_ids("owner", |i| i.set_told(&w.sam, true, Some("2026-11-25"), "owner")).unwrap();
    let c = &w.e.identities()["contacts"][0];
    assert_eq!(c["positions_told"], true);
    assert_eq!(c["positions_told_date"], "2026-11-25");
}

#[test]
fn only_the_owner_edits_identities_and_only_a_person_account_can_be_linked() {
    let w = world();
    assert!(w.e.with_ids("agent:x", |i| i.create_contact("Eve", None, "agent:x")).is_err());
    assert!(w.e.with_ids("owner", |i| i.link(&w.sam, "agent:x", "owner")).is_err());
    assert!(w.e.with_ids("owner", |i| i.link("nope", "person:sam", "owner")).is_err());
    assert!(w.e.with_ids("owner", |i| i.assign(ME, Some("someone"), None, "owner")).is_err());
    assert!(w.e.with_ids("owner", |i| i.assign(ME, Some("contact:missing"), None, "owner")).is_err());
}

#[test]
fn positions_are_kept_with_messages_and_last_positions_per_identity() {
    let w = world();
    w.e.ingest(NET, ME, pos(SAM, 51.5, -0.12)).unwrap();
    let m = w.e.ingest(NET, ME, text(SAM, "!ffffffff", "at the bridge", 0)).unwrap();
    let p = m.position.expect("the message carries the sender's last known position");
    assert_eq!(p.source.as_deref(), Some("last_known"));
    assert_eq!(p.time, Some(1_764_000_000), "with the time it was reported, so it is not mistaken for 'now'");
    w.e.ingest(NET, ME, pos(SAM, 51.6, -0.2)).unwrap();
    let last = w.e.last_positions("person:jay").unwrap();
    assert_eq!(last.len(), 1);
    assert_eq!(last[0]["name"], "Sam");
    assert_eq!(last[0]["position"]["lat"], 51.6);
}

#[test]
fn cached_positions_survive_a_restart() {
    let w = world();
    w.e.ingest(NET, ME, pos(SAM, 51.5, -0.12)).unwrap();
    drop(w.e);
    let e = Engine::open(w.t.path(), "person:jay").unwrap();
    let m = e.ingest(NET, ME, text(SAM, "!ffffffff", "after restart", 0)).unwrap();
    assert_eq!(m.position.unwrap().lat, Some(51.5));
}

#[test]
fn the_linked_account_can_hide_its_own_locations_nobody_else_can() {
    let w = world();
    w.e.ingest(NET, ME, pos(SAM, 51.5, -0.12)).unwrap();
    assert!(w.e.hide_locations("person:sam", &w.sam).is_err(), "not linked yet");
    w.e.with_ids("owner", |i| i.link(&w.sam, "person:sam", "owner")).unwrap();
    assert!(w.e.hide_locations("person:eve", &w.sam).is_err());
    assert_eq!(w.e.messages("person:sam", &Filter::default()).unwrap().len(), 1);
    w.e.hide_locations("person:sam", &w.sam).unwrap();
    assert!(w.e.messages("person:sam", &Filter::default()).unwrap().is_empty());
    assert!(w.e.messages("person:jay", &Filter { kind: Some("position".into()), ..Default::default() }).unwrap().is_empty());
    // the raw record is still on disk (kept pending the owner's ruling)
    assert_eq!(taos_mesh::store::Store::read_all(w.t.path()).unwrap().len(), 1);
    // a position after the hide is visible again (the hide covers history up to that moment)
    std::thread::sleep(std::time::Duration::from_millis(5));
    w.e.ingest(NET, ME, pos(SAM, 51.7, -0.3)).unwrap();
    assert_eq!(w.e.messages("person:sam", &Filter::default()).unwrap().len(), 1);
}

#[test]
fn registry_state_survives_a_restart() {
    let w = world();
    w.e.with_ids("owner", |i| i.link(&w.sam, "person:sam", "owner")).unwrap();
    drop(w.e);
    let e = Engine::open(w.t.path(), "person:jay").unwrap();
    let ids = e.identities();
    assert_eq!(ids["contacts"][0]["linked_account"], "person:sam");
    assert_eq!(ids["assignments"].as_array().unwrap().len(), 2);
    let _ = node_name(1);
}

#[test]
fn hiding_locations_also_removes_the_position_riding_on_messages_and_in_last_positions() {
    let w = world();
    w.e.ingest(NET, ME, pos(SAM, 51.5, -0.12)).unwrap();
    w.e.ingest(NET, ME, text(SAM, "!ffffffff", "carries a last-known position", 0)).unwrap();
    let ni = Inbound { raw: vec![5], event: Event::NodeInfo { long_name: "Sam".into(), short_name: "S".into(), hw_model: None, position: Some(Position { lat: Some(51.5), lon: Some(-0.12), source: Some("packet".into()), ..Default::default() }) }, from: Some(SAM.into()), to: None, channel: None, packet_id: None, radio_ts: None, signal: Signal::default(), position: Some(Position { lat: Some(51.5), lon: Some(-0.12), source: Some("packet".into()), ..Default::default() }), broadcast: false, trust: None };
    w.e.ingest(NET, ME, ni).unwrap();
    w.e.with_ids("owner", |i| i.link(&w.sam, "person:sam", "owner")).unwrap();
    assert!(w.e.messages("person:sam", &Filter { kind: Some("text".into()), ..Default::default() }).unwrap()[0].position.is_some());
    w.e.hide_locations("person:sam", &w.sam).unwrap();
    for viewer in ["person:sam", "person:jay"] {
        let all = w.e.messages(viewer, &Filter::default()).unwrap();
        assert!(all.iter().all(|r| r.position.is_none()), "{viewer}: no position survives on any record");
        assert!(w.e.last_positions(viewer).unwrap().is_empty(), "{viewer}: last_positions empty");
    }
}
