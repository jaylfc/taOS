// SPDX-License-Identifier: AGPL-3.0-or-later
//! Two radios at once, one per network: a send addressed to a MeshCore thread is written to the MeshCore radio only, and a
//! send to a Meshtastic thread to the Meshtastic radio only. Fake links; nothing leaves the machine.
mod common;
use common::*;
use serde_json::{json, Value};
use std::sync::Arc;
use std::time::{Duration, Instant};
use taos_mesh::api::Daemon;
use taos_mesh::engine::Engine;
use taos_mesh::link::FakeLink;
use taos_mesh::meshtastic::frame;

fn call(d: &Daemon, m: &str, p: Value) -> Value {
    d.handle_trusted(&json!({"id": 1, "method": m, "principal": "owner", "params": p}).to_string())
}

fn wait_new(l: &FakeLink, before: usize) -> usize {
    let end = Instant::now() + Duration::from_secs(3);
    while l.written_bytes().len() <= before && Instant::now() < end {
        std::thread::sleep(Duration::from_millis(20));
    }
    std::thread::sleep(Duration::from_millis(200));
    l.written_bytes().len()
}

#[test]
fn a_send_goes_only_to_the_radio_of_its_network() {
    let t = TmpDir::new("multi");
    let e = Engine::open(&t.path().join("data"), "person:jay").unwrap();
    let d = Arc::new(Daemon::new(e.clone(), false));
    let mt = FakeLink::new("fake-mt");
    mt.reply_on_write(&frame(&frame_named(
        &fixture("meshtastic_device.json"),
        "my_info",
    )));
    let mc = FakeLink::new("fake-mc");
    d.attach(Box::new(mt.clone()), "meshtastic", vec![])
        .unwrap();
    d.attach(Box::new(mc.clone()), "meshcore", vec![]).unwrap();
    let end = Instant::now() + Duration::from_secs(5);
    while e.radios().len() < 2 {
        assert!(Instant::now() < end, "both radios register");
        std::thread::sleep(Duration::from_millis(20));
    }
    let nets: Vec<String> = e.radios().iter().map(|r| r.network.clone()).collect();
    assert!(
        nets.contains(&"meshtastic".to_string()) && nets.contains(&"meshcore".to_string()),
        "{nets:?}"
    );
    std::thread::sleep(Duration::from_millis(300));
    let (b_mt, b_mc) = (mt.written_bytes().len(), mc.written_bytes().len());
    let r = call(
        &d,
        "app.send",
        json!({"thread": "ch:meshcore:0", "text": "to the hill"}),
    );
    assert_eq!(r["result"]["accepted"], true, "{r}");
    let n_mc = wait_new(&mc, b_mc);
    assert!(n_mc > b_mc, "the MeshCore radio got the send");
    assert_eq!(
        mt.written_bytes().len(),
        b_mt,
        "the Meshtastic radio got nothing"
    );
    let (b_mt, b_mc) = (mt.written_bytes().len(), mc.written_bytes().len());
    let r = call(
        &d,
        "app.send",
        json!({"thread": "ch:meshtastic:0", "text": "to the ridge"}),
    );
    assert_eq!(r["result"]["accepted"], true, "{r}");
    assert!(
        wait_new(&mt, b_mt) > b_mt,
        "the Meshtastic radio got the send"
    );
    assert_eq!(
        mc.written_bytes().len(),
        b_mc,
        "the MeshCore radio got nothing"
    );
}
