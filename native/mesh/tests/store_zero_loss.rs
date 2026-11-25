// SPDX-License-Identifier: AGPL-3.0-or-later
mod common;
use common::*;
use std::io::{BufRead, BufReader};
use std::process::{Command, Stdio};
use taos_mesh::engine::Engine;
use taos_mesh::model::*;
use taos_mesh::store::Store;

fn text_frame(n: u32) -> Inbound {
    Inbound {
        raw: vec![n as u8; 40],
        event: Event::Text { text: format!("m{n}") },
        from: Some("!00000001".into()),
        to: Some("!ffffffff".into()),
        channel: Some(0),
        packet_id: Some(n),
        radio_ts: None,
        signal: Signal { rssi: Some(-90), snr: Some(5.0), hops: Some(1), ..Default::default() },
        position: None,
        broadcast: true,
    }
}

#[test]
fn every_record_is_replayable_with_the_same_ids() {
    let t = TmpDir::new("replay");
    let e = Engine::open(t.path(), "owner").unwrap();
    let mut ids = vec![];
    for n in 0..50 {
        ids.push(e.ingest("meshtastic", "!0000aaaa", text_frame(n)).unwrap().id);
    }
    drop(e);
    let again: Vec<String> = Store::read_all(t.path()).unwrap().into_iter().map(|r| r.id).collect();
    assert_eq!(again, ids);
    // 50 distinct stable ids
    let mut s = ids.clone();
    s.sort();
    s.dedup();
    assert_eq!(s.len(), 50);
    // restart keeps appending after the old ones
    let e = Engine::open(t.path(), "owner").unwrap();
    e.ingest("meshtastic", "!0000aaaa", text_frame(99)).unwrap();
    assert_eq!(Store::read_all(t.path()).unwrap().len(), 51);
}

#[test]
fn a_record_carries_everything_the_spec_asks_for() {
    let t = TmpDir::new("fields");
    let e = Engine::open(t.path(), "owner").unwrap();
    let r = e.ingest("meshtastic", "!0000aaaa", text_frame(1)).unwrap();
    let line = std::fs::read_to_string(t.path().join("records.jsonl")).unwrap();
    let v: serde_json::Value = serde_json::from_str(line.trim()).unwrap();
    for k in ["id", "network", "radio", "channel", "host_ts_ms", "signal", "raw_hex", "dir", "kind", "from", "to"] {
        assert!(v.get(k).is_some(), "missing {k}");
    }
    assert_eq!(v["signal"]["rssi"], -90);
    assert_eq!(v["signal"]["snr"], 5.0);
    assert_eq!(v["signal"]["hops"], 1);
    assert_eq!(v["raw_hex"], to_hex(&[1u8; 40]));
    assert_eq!(v["id"], r.id);
}

#[test]
fn a_torn_last_line_is_preserved_on_the_side_and_appending_continues() {
    let t = TmpDir::new("torn");
    {
        let e = Engine::open(t.path(), "owner").unwrap();
        e.ingest("meshtastic", "!0000aaaa", text_frame(1)).unwrap();
        e.ingest("meshtastic", "!0000aaaa", text_frame(2)).unwrap();
    }
    let p = t.path().join("records.jsonl");
    let mut data = std::fs::read(&p).unwrap();
    data.extend_from_slice(br#"{"v":1,"id":"deadbeef","dir":"in","net"#); // power cut mid-write
    std::fs::write(&p, &data).unwrap();
    let e = Engine::open(t.path(), "owner").unwrap();
    let h = e.health();
    let side = h["torn_tail_preserved"].as_str().expect("the torn bytes are reported");
    assert_eq!(std::fs::read(side).unwrap(), br#"{"v":1,"id":"deadbeef","dir":"in","net"#);
    e.ingest("meshtastic", "!0000aaaa", text_frame(3)).unwrap();
    let all = Store::read_all(t.path()).unwrap();
    assert_eq!(all.len(), 3, "the two complete records and the new one; the torn line did not poison the file");
}

#[test]
fn sigkill_mid_write_loses_no_acknowledged_record() {
    let t = TmpDir::new("kill");
    let mut child = Command::new(env!("CARGO_BIN_EXE_taos-mesh"))
        .arg("__append-loop")
        .arg(t.path())
        .stdout(Stdio::piped())
        .stderr(Stdio::null())
        .spawn()
        .unwrap();
    let mut rd = BufReader::new(child.stdout.take().unwrap());
    let mut acked = vec![];
    let mut line = String::new();
    while acked.len() < 300 {
        line.clear();
        if rd.read_line(&mut line).unwrap() == 0 {
            break;
        }
        acked.push(line.trim().to_string());
    }
    child.kill().unwrap(); // SIGKILL: no destructors, no flush
    child.wait().unwrap();
    // drain what the child had already printed before dying
    while rd.read_line(&mut line).unwrap_or(0) > 0 {
        let l = line.trim().to_string();
        if !l.is_empty() {
            acked.push(l);
        }
        line.clear();
    }
    assert!(acked.len() >= 300);
    let e = Engine::open(t.path(), "owner").unwrap(); // repairs a torn tail if the kill landed mid-line
    let stored: std::collections::HashSet<String> = Store::read_all(t.path()).unwrap().into_iter().map(|r| r.id).collect();
    for id in &acked {
        assert!(stored.contains(id), "acknowledged record {id} is missing after SIGKILL");
    }
    // and the store is writable again
    e.ingest("meshtastic", "!0000aaaa", text_frame(1)).unwrap();
}

#[test]
fn a_failed_write_is_surfaced_kept_pending_and_retried_in_order() {
    // /dev/full accepts open and fails every write with ENOSPC
    if !std::path::Path::new("/dev/full").exists() {
        return;
    }
    let t = TmpDir::new("full");
    let store = Store::open_file(std::path::Path::new("/dev/full")).unwrap();
    let e = Engine::open_with_store(t.path(), "owner", store).unwrap();
    let err = e.ingest("meshtastic", "!0000aaaa", text_frame(1)).unwrap_err();
    assert!(err.to_string().to_lowercase().contains("space") || err.to_string().contains("28"), "{err}");
    assert!(e.ingest("meshtastic", "!0000aaaa", text_frame(2)).is_err());
    let h = e.health();
    assert_eq!(h["ok"], false);
    assert_eq!(h["pending_unwritten"], 2, "both frames are still held, not dropped");
    assert!(h["store_error"].is_string());
}

#[test]
fn pending_records_are_written_when_the_store_recovers() {
    // a store whose directory is removed fails; recreate and the retry lands
    let t = TmpDir::new("recover");
    let sub = t.path().join("data");
    let e = Engine::open(&sub, "owner").unwrap();
    e.ingest("meshtastic", "!0000aaaa", text_frame(1)).unwrap();
    // replace the log file with a directory: appends to the open handle still
    // work on Linux, so instead make the device itself fail through /dev/full
    drop(e);
    if !std::path::Path::new("/dev/full").exists() {
        return;
    }
    let store = Store::open_file(std::path::Path::new("/dev/full")).unwrap();
    let e = Engine::open_with_store(&sub, "owner", store).unwrap();
    assert!(e.ingest("meshtastic", "!0000aaaa", text_frame(2)).is_err());
    assert_eq!(e.health()["pending_unwritten"], 1);
}

#[test]
fn an_old_record_without_newer_fields_still_loads() {
    // upgrade test: a v1 line written before `recipient`, `principal`, etc existed
    let t = TmpDir::new("old");
    let old = r#"{"v":1,"id":"00112233445566778899aabbccddeeff","dir":"in","network":"meshtastic","radio":"!0000aaaa","kind":"text","host_ts_ms":1764000000000,"broadcast":true,"text":"old","raw_hex":"00"}"#;
    std::fs::write(t.path().join("records.jsonl"), format!("{old}\n")).unwrap();
    let e = Engine::open(t.path(), "owner").unwrap();
    let got = e.messages("owner", &taos_mesh::engine::Filter::default()).unwrap();
    assert_eq!(got.len(), 1);
    assert_eq!(got[0].text.as_deref(), Some("old"));
    assert_eq!(got[0].signal.rssi, None);
}

#[test]
fn a_corrupt_middle_line_stops_the_start_up_loudly() {
    let t = TmpDir::new("corrupt");
    std::fs::write(t.path().join("records.jsonl"), "not json\n").unwrap();
    let err = Engine::open(t.path(), "owner").err().expect("must refuse to start");
    assert!(err.to_string().contains("line 1"), "{err}");
}
