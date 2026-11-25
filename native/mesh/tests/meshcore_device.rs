// SPDX-License-Identifier: AGPL-3.0-or-later
//! MeshCore: firmware, board model, radio parameters and channels read from the companion radio.
//! Provenance: these frames are built by hand from the byte layouts in meshcore-dev/MeshCore docs/companion_protocol.md
//! (PACKET_DEVICE_INFO, PACKET_SELF_INFO, PACKET_CHANNEL_INFO). They were NOT recorded from a real radio.
use taos_mesh::meshcore::*;

fn padded(s: &str, n: usize) -> Vec<u8> {
    let mut v = s.as_bytes().to_vec();
    v.resize(n, 0);
    v
}

fn device_info() -> Vec<u8> {
    let mut f = vec![RESP_DEVICE_INFO, 9, 50, 8, 0, 0, 0, 0];
    f.extend(padded("26 Nov 2026", 12));
    f.extend(padded("Heltec V3", 40));
    f.extend(padded("v1.9.1", 20));
    f
}

fn self_info() -> Vec<u8> {
    let mut f = vec![RESP_SELF_INFO, 1, 20, 22];
    f.extend((1u8..=32).collect::<Vec<u8>>());
    f.extend(0i32.to_le_bytes());
    f.extend(0i32.to_le_bytes());
    f.extend([0, 0, 0, 0]);
    f.extend(869_618u32.to_le_bytes());
    f.extend(62_500u32.to_le_bytes());
    f.extend([8, 8]);
    f.extend(b"Trail Node");
    f
}

fn channel_info(i: u8, name: &str) -> Vec<u8> {
    let mut f = vec![RESP_CHANNEL_INFO, i];
    f.extend(padded(name, 32));
    f.extend([7u8; 16]);
    f
}

#[test]
fn device_info_self_info_and_channels_are_read() {
    let mut d = MeshcoreDriver::new("t");
    let (_, cmds) = d.feed_frame(device_info());
    // read-only follow-up: one channel query per slot (at most 8)
    assert_eq!(cmds.len(), 8);
    assert_eq!(cmds[0], vec![CMD_GET_CHANNEL, 0]);
    let _ = d.feed_frame(self_info());
    let _ = d.feed_frame(channel_info(0, "Public"));
    let _ = d.feed_frame(channel_info(1, "crew"));
    let _ = d.feed_frame(channel_info(2, ""));
    let v = d.device.snapshot();
    assert_eq!(v["network"], "meshcore");
    assert_eq!(v["hardware"], "Heltec V3");
    assert_eq!(v["firmware"], "v1.9.1 (26 Nov 2026)");
    assert_eq!(v["name"], "Trail Node");
    assert_eq!(v["radio"]["freq_mhz"], 869.618);
    assert_eq!(v["radio"]["bw_khz"], 62.5);
    assert_eq!(v["radio"]["sf"], 8);
    assert_eq!(v["radio"]["cr"], 8);
    let ch = v["channels"].as_array().unwrap();
    assert_eq!(ch.len(), 2, "an unnamed slot is not a channel");
    assert_eq!(ch[1]["name"], "crew");
    assert_eq!(v["writable"], serde_json::json!([]), "MeshCore settings are read only for now");
}
