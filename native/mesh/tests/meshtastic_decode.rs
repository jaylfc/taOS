// SPDX-License-Identifier: AGPL-3.0-or-later
mod common;
use common::*;
use taos_mesh::meshtastic::*;
use taos_mesh::model::*;

fn decode(raw: Vec<u8>) -> Inbound {
    MeshtasticDriver::new().decode_frame(raw)
}

#[test]
fn every_fixture_decodes_to_what_the_reference_library_says() {
    let fx = fixture("meshtastic.json");
    let mut seen = 0;
    for f in fx["frames"].as_array().unwrap() {
        let name = f["name"].as_str().unwrap();
        let raw = hex(&f["hex"]);
        let e = &f["expect"];
        let inb = decode(raw.clone());
        assert_eq!(inb.raw, raw, "{name}: raw bytes kept verbatim");
        if let Some(k) = e["kind"].as_str() {
            assert_eq!(inb.event.kind(), k, "{name}");
        }
        if let Some(from) = e["from"].as_str() {
            assert_eq!(inb.from.as_deref(), Some(from), "{name}");
        }
        if let Some(to) = e["to"].as_str() {
            assert_eq!(inb.to.as_deref(), Some(to), "{name}");
        }
        if let Some(b) = e["broadcast"].as_bool() {
            assert_eq!(inb.broadcast, b, "{name}");
        }
        if let Some(c) = e["channel"].as_u64() {
            assert_eq!(inb.channel, Some(c as u32), "{name}");
        }
        if let Some(t) = e["text"].as_str() {
            assert!(matches!(&inb.event, Event::Text { text } if text == t), "{name}: {:?}", inb.event);
        }
        if let Some(s) = e["snr"].as_f64() {
            assert_eq!(inb.signal.snr, Some(s as f32), "{name}");
        }
        if let Some(r) = e["rssi"].as_i64() {
            assert_eq!(inb.signal.rssi, Some(r as i32), "{name}");
        }
        if let Some(h) = e["hops"].as_u64() {
            assert_eq!(inb.signal.hops, Some(h as u32), "{name} hops");
        }
        if let Some(p) = e["packet_id"].as_u64() {
            assert_eq!(inb.packet_id, Some(p as u32), "{name}");
        }
        if let Some(t) = e["radio_ts"].as_u64() {
            assert_eq!(inb.radio_ts, Some(t), "{name} radio_ts");
        }
        if let Some(lat) = e["lat"].as_f64() {
            let p = inb.position.as_ref().unwrap_or_else(|| panic!("{name}: no position"));
            assert!((p.lat.unwrap() - lat).abs() < 1e-6, "{name} lat {:?}", p.lat);
            assert!((p.lon.unwrap() - e["lon"].as_f64().unwrap()).abs() < 1e-6, "{name} lon");
            if let Some(a) = e["alt"].as_i64() {
                assert_eq!(p.alt_m, Some(a as i32), "{name} alt");
            }
            if e.get("time").is_some() {
                assert_eq!(p.time, e["time"].as_u64(), "{name} time");
            }
            if let Some(h) = e["hdop"].as_u64() {
                assert_eq!(p.hdop, Some(h as u32));
                assert_eq!(p.pdop, e["pdop"].as_u64().map(|x| x as u32));
                assert_eq!(p.sats, e["sats"].as_u64().map(|x| x as u32));
                assert_eq!(p.precision_bits, e["precision_bits"].as_u64().map(|x| x as u32));
                assert_eq!(p.gps_accuracy_mm, e["gps_accuracy_mm"].as_u64().map(|x| x as u32));
                assert_eq!(p.accuracy_m, None, "accuracy is not invented from DOP");
            }
        }
        if let Some(l) = e["long_name"].as_str() {
            assert!(matches!(&inb.event, Event::NodeInfo { long_name, short_name, .. } if long_name == l && short_name == e["short_name"].as_str().unwrap()), "{name}: {:?}", inb.event);
        }
        if let Some(t) = e["telemetry"].as_str() {
            let Event::Telemetry { kind, values } = &inb.event else { panic!("{name}") };
            assert_eq!(kind, t);
            for (k, v) in e.as_object().unwrap() {
                if ["kind", "from", "telemetry", "radio_ts"].contains(&k.as_str()) {
                    continue;
                }
                let got = values[k].as_f64().unwrap_or_else(|| panic!("{name}: telemetry field {k} missing in {values}"));
                assert!((got - v.as_f64().unwrap()).abs() < 1e-3, "{name}.{k}: {got}");
            }
        }
        if e["kind"] == "ack" {
            let Event::Ack { request_id, error } = &inb.event else { panic!() };
            assert_eq!(*request_id as u64, e["request_id"].as_u64().unwrap());
            assert_eq!(error.as_deref(), e["error"].as_str());
        }
        if let Some(v) = e["variant"].as_str() {
            match v {
                "my_info" => {
                    assert!(matches!(&inb.event, Event::Control { what } if what == "my_info"));
                    assert_eq!(inb.from, Some(node_name(e["my_node_num"].as_u64().unwrap() as u32)));
                }
                "node_info" => {
                    assert_eq!(inb.from, Some(node_name(e["num"].as_u64().unwrap() as u32)));
                    assert!(matches!(&inb.event, Event::NodeInfo { long_name, short_name, .. } if long_name == e["long_name"].as_str().unwrap() && short_name == e["short_name"].as_str().unwrap()));
                }
                "rebooted" => assert!(matches!(&inb.event, Event::Control { what } if what == "rebooted")),
                other => panic!("{name}: unhandled variant {other}"),
            }
        }
        seen += 1;
    }
    assert_eq!(seen, 16);
}

#[test]
fn recorded_node_info_from_a_real_radio() {
    let fx = fixture("meshtastic.json");
    let inb = decode(frame_named(&fx, "node_info_heltec_v2_1"));
    assert_eq!(inb.from.as_deref(), Some("!28af67cc"));
    assert!(matches!(inb.event, Event::NodeInfo { ref long_name, .. } if long_name == "Unknown 67cc"));
}

#[test]
fn my_info_sets_the_radio_id_before_the_frame_is_returned() {
    let fx = fixture("meshtastic.json");
    let mut d = MeshtasticDriver::new();
    assert_eq!(d.radio_id(), None);
    let mut stream = frame(&frame_named(&fx, "my_info_heltec_v2_1"));
    stream.extend(frame(&frame_named(&fx, "node_info_heltec_v2_1")));
    let got = d.feed(&stream);
    assert_eq!(got.len(), 2);
    assert_eq!(d.radio_id().as_deref(), Some("!28af67cc"));
}

#[test]
fn hop_start_zero_is_unknown_not_direct() {
    // old firmware never set hop_start; hops must stay None, not Some(0)
    let fx = fixture("meshtastic.json");
    let inb = decode(frame_named(&fx, "position_negative_alt"));
    assert_eq!(inb.signal.hops, None);
    assert_eq!(inb.position.as_ref().unwrap().time, None, "no time sent: unknown, not zero");
}

#[test]
fn hop_arithmetic_matches_the_firmware_definition() {
    let fx = fixture("meshtastic.json");
    assert_eq!(decode(frame_named(&fx, "text_broadcast")).signal.hops, Some(1));
    assert_eq!(decode(frame_named(&fx, "text_direct_utf8")).signal.hops, Some(0));
}

#[test]
fn garbage_that_is_not_a_protobuf_becomes_an_opaque_record_not_a_drop() {
    let inb = decode(vec![0xff, 0xff, 0xff]);
    assert_eq!(inb.raw, vec![0xff, 0xff, 0xff]);
    assert!(matches!(inb.event, Event::Opaque { .. }));
}

#[test]
fn stream_framing_resyncs_and_counts_console_text() {
    let fx = fixture("meshtastic.json");
    let p1 = frame_named(&fx, "text_broadcast");
    let p2 = frame_named(&fx, "position_packet");
    let mut stream = b"INFO  | boot\n".to_vec();
    stream.extend(frame(&p1));
    stream.extend(&[0x94, 0x00, 0x94, 0x94, 0x00]); // false starts
    stream.extend(frame(&p2));
    let mut dec = StreamDecoder::new();
    // feed one byte at a time: frames must come out whole
    let mut out = vec![];
    for b in &stream {
        out.extend(dec.push(&[*b]));
    }
    assert_eq!(out, vec![p1, p2]);
    assert!(dec.console_bytes >= 14);
}

#[test]
fn oversize_length_is_treated_as_corruption() {
    let mut dec = StreamDecoder::new();
    let mut s = vec![0x94, 0xC3, 0x7F, 0x00]; // 32512 bytes: invalid
    s.extend(frame(&[1, 2, 3]));
    assert_eq!(dec.push(&s), vec![vec![1, 2, 3]]);
    assert_eq!(dec.oversize_resyncs, 1);
}

#[test]
fn a_payload_that_contains_the_start_bytes_survives() {
    let payload = vec![0x94, 0xC3, 0x00, 0x02, 0x94, 0xC3];
    let mut dec = StreamDecoder::new();
    assert_eq!(dec.push(&frame(&payload)), vec![payload]);
}

#[test]
fn empty_frame_is_a_frame() {
    let mut dec = StreamDecoder::new();
    assert_eq!(dec.push(&[0x94, 0xC3, 0, 0]), vec![Vec::<u8>::new()]);
}

#[test]
fn our_outbound_bytes_equal_the_reference_library_encoding() {
    let fx = fixture("meshtastic.json");
    let o = &fx["outbound"];
    assert_eq!(encode_want_config(0x7a05_0001), hex(&o["want_config"]["hex"]));
    let t = &o["text_broadcast"];
    assert_eq!(encode_text(BROADCAST, 0, "check in ok", 0x0bad_f00d, false), hex(&t["hex"]));
    let t = &o["text_direct_channel2"];
    assert_eq!(encode_text(0x28af_67cc, 2, "on my way", 99, true), hex(&t["hex"]));
}

#[test]
fn handshake_is_wake_bytes_then_framed_want_config() {
    let hs = MeshtasticDriver::new().handshake(0x7a05_0001);
    assert_eq!(&hs[..32], &[0xC3u8; 32][..]);
    assert_eq!(&hs[32..36], &[0x94, 0xC3, 0x00, 0x06]);
    assert_eq!(&hs[36..], &encode_want_config(0x7a05_0001)[..]);
}
