// SPDX-License-Identifier: AGPL-3.0-or-later
mod common;
use common::*;
use taos_mesh::meshcore::*;
use taos_mesh::model::*;

fn one(d: &mut MeshcoreDriver, raw: Vec<u8>) -> (Inbound, Vec<Vec<u8>>) {
    d.feed_frame(raw)
}

fn ready_driver() -> MeshcoreDriver {
    let fx = fixture("meshcore.json");
    let mut d = MeshcoreDriver::new("t");
    one(&mut d, hex(&fx["frames"][0]["hex"]));
    d
}

fn named(name: &str) -> (Vec<u8>, serde_json::Value) {
    let fx = fixture("meshcore.json");
    let f = fx["frames"].as_array().unwrap().iter().find(|f| f["name"] == name).unwrap().clone();
    (hex(&f["hex"]), f["oracle"][0]["payload"].clone())
}

#[test]
fn documented_commands_are_byte_exact() {
    let fx = fixture("meshcore.json");
    let c = &fx["documented_commands"];
    // doc example for APP_START uses all-zero reserved bytes; ours follows
    // the reference client (version byte), so compare layout, not the bytes
    let ours = app_start("mccli");
    let doc = hex(&c["app_start_doc"]);
    assert_eq!(ours.len(), doc.len());
    assert_eq!(ours[0], doc[0]);
    assert_eq!(&ours[8..], &doc[8..]);
    assert_eq!(device_query(), hex(&c["device_query_doc"]));
    assert_eq!(sync_next_message(), hex(&c["get_message_doc"]));
    assert_eq!(send_channel(1, 1_234_567_890, "Hello"), hex(&c["send_channel_doc"]));
}

#[test]
fn serial_frames_in_both_directions() {
    assert_eq!(frame_to_radio(&[0x16, 0x03]), vec![b'<', 2, 0, 0x16, 0x03]);
    let mut dec = SerialDecoder::new();
    let mut s = b"debug text".to_vec();
    s.extend_from_slice(&[b'>', 3, 0, 9, 8, 7]);
    s.extend_from_slice(&[b'>', 0xff, 0x7f]); // oversize: resync
    s.extend_from_slice(&[b'>', 1, 0, 0x83]);
    let mut out = vec![];
    for b in &s {
        out.extend(dec.push(&[*b]));
    }
    assert_eq!(out, vec![vec![9, 8, 7], vec![0x83]]);
    assert!(dec.console_bytes >= 10);
}

#[test]
fn self_info_gives_the_radio_its_id_and_position() {
    let fx = fixture("meshcore.json");
    let mut d = MeshcoreDriver::new("t");
    let (i, _) = one(&mut d, hex(&fx["frames"][0]["hex"]));
    assert_eq!(d.radio_id().as_deref(), Some("010203040506"));
    assert_eq!(i.from.as_deref(), Some("010203040506"));
    let p = i.position.unwrap();
    assert!((p.lat.unwrap() - 51.45).abs() < 1e-9 && (p.lon.unwrap() + 1.23).abs() < 1e-9);
}

#[test]
fn contact_frame_matches_the_reference_reader() {
    let (raw, o) = named("contact_with_position");
    let mut d = ready_driver();
    let (i, cmds) = one(&mut d, raw);
    assert!(cmds.is_empty());
    assert_eq!(i.from.as_deref(), Some(&o["public_key"].as_str().unwrap()[..12]));
    let p = i.position.unwrap();
    assert!((p.lat.unwrap() - o["adv_lat"].as_f64().unwrap()).abs() < 1e-9);
    assert!((p.lon.unwrap() - o["adv_lon"].as_f64().unwrap()).abs() < 1e-9);
    assert_eq!(p.time, o["last_advert"].as_u64());
    assert!(matches!(i.event, Event::NodeInfo { ref long_name, .. } if long_name == "Bravo"));
}

#[test]
fn new_advert_push_is_a_position_event_with_the_advertised_fix() {
    let (raw, o) = named("new_advert_push");
    let mut d = ready_driver();
    let (i, _) = one(&mut d, raw);
    assert!(matches!(i.event, Event::Position(_)));
    assert!(i.broadcast);
    assert!((i.position.unwrap().lat.unwrap() - o["adv_lat"].as_f64().unwrap()).abs() < 1e-9);
}

#[test]
fn a_contact_without_a_fix_has_no_position_not_a_zero_one() {
    let (raw, _) = named("contact_without_position");
    let mut d = ready_driver();
    let (i, _) = one(&mut d, raw);
    assert!(i.position.is_none());
}

#[test]
fn direct_message_v3_decodes_and_asks_for_the_next_one() {
    let (raw, o) = named("contact_msg_v3_direct");
    let mut d = ready_driver();
    let (i, cmds) = one(&mut d, raw);
    assert!(matches!(&i.event, Event::Text { text } if text == o["text"].as_str().unwrap()));
    assert_eq!(i.from.as_deref(), o["pubkey_prefix"].as_str());
    assert_eq!(i.to.as_deref(), Some("010203040506"));
    assert_eq!(i.signal.snr, Some(o["SNR"].as_f64().unwrap() as f32));
    assert_eq!(i.signal.hops, None, "0xFF path_len is a stored direct route: the hop count is not reported, so unknown");
    assert_eq!(i.radio_ts, o["sender_timestamp"].as_u64());
    assert!(!i.broadcast);
    assert_eq!(cmds, vec![sync_next_message()]);
}

#[test]
fn direct_message_two_hops_and_old_layout() {
    let (raw, o) = named("contact_msg_v3_two_hops");
    let mut d = ready_driver();
    let (i, _) = one(&mut d, raw);
    assert_eq!(i.signal.hops, Some(o["path_len"].as_u64().unwrap() as u32));
    assert_eq!(i.signal.snr, Some(2.5));
    let (raw, o) = named("contact_msg_v2");
    let (i, _) = one(&mut d, raw);
    assert!(matches!(&i.event, Event::Text { text } if text == o["text"].as_str().unwrap()));
    assert_eq!(i.signal.snr, None, "v2 frames carry no SNR: unknown, not zero");
}

#[test]
fn channel_messages_v3_and_v2() {
    let (raw, o) = named("channel_msg_v3");
    let mut d = ready_driver();
    let (i, cmds) = one(&mut d, raw);
    assert_eq!(i.channel, Some(o["channel_idx"].as_u64().unwrap() as u32));
    assert!(matches!(&i.event, Event::Text { text } if text == "Bravo: water found"));
    assert!(i.broadcast);
    assert_eq!(i.signal.snr, Some(4.5));
    assert_eq!(cmds.len(), 1);
    let (raw, o) = named("channel_msg_v2");
    let (i, _) = one(&mut d, raw);
    assert_eq!(i.channel, Some(o["channel_idx"].as_u64().unwrap() as u32));
    assert_eq!(i.signal.hops, Some(3));
}

#[test]
fn messages_waiting_triggers_one_sync_and_the_drain_ends_on_no_more() {
    let mut d = ready_driver();
    let (_, c) = one(&mut d, vec![0x83]);
    assert_eq!(c, vec![sync_next_message()]);
    // while draining, a second tickle must not queue a duplicate sync
    let (raw, _) = named("channel_msg_v3");
    let (_, c) = one(&mut d, raw);
    assert_eq!(c.len(), 1);
    let (_, c) = one(&mut d, vec![0x83]);
    assert!(c.is_empty());
    let (i, _) = one(&mut d, vec![10]);
    assert!(matches!(i.event, Event::Control { ref what } if what == "no_more_messages"));
    let (_, c) = one(&mut d, vec![0x83]);
    assert_eq!(c.len(), 1);
}

#[test]
fn send_confirmation_push_is_an_ack() {
    let (raw, o) = named("send_confirmed_push");
    let mut d = ready_driver();
    let (i, _) = one(&mut d, raw);
    let Event::Ack { request_id, .. } = i.event else { panic!() };
    assert_eq!(format!("{request_id:08x}"), {
        // the reference reader prints the 4 bytes as they arrive
        let h = o["code"].as_str().unwrap();
        let b = from_hex(h).unwrap();
        format!("{:08x}", u32::from_le_bytes(b.try_into().unwrap()))
    });
}

#[test]
fn unknown_and_truncated_frames_are_kept_not_dropped() {
    let mut d = ready_driver();
    let (i, _) = one(&mut d, vec![0x99, 1, 2, 3]);
    assert_eq!(i.raw, vec![0x99, 1, 2, 3]);
    let (i, _) = one(&mut d, vec![16, 0]);
    assert!(matches!(i.event, Event::Opaque { .. }));
    assert_eq!(d.decode_errors, 1);
}

#[test]
fn outbound_direct_and_channel_text_layout() {
    let p = parse_prefix("646566676869").unwrap();
    let d = send_direct(&p, 1_764_000_000, "hi");
    assert_eq!(&d[..3], &[2, 0, 0]);
    assert_eq!(&d[3..7], &1_764_000_000u32.to_le_bytes());
    assert_eq!(&d[7..13], &p);
    assert_eq!(&d[13..], b"hi");
    assert!(parse_prefix("zz").is_none());
}
