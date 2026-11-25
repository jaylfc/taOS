// SPDX-License-Identifier: AGPL-3.0-or-later
//! `radios.release`: the engine lets go of a port (and stops reconnecting it) so the firmware flasher can have it.
mod common;
use common::*;
use serde_json::json;
use std::io;
use std::sync::atomic::{AtomicUsize, Ordering};
use std::sync::Arc;
use std::time::{Duration, Instant};
use taos_mesh::api::Daemon;
use taos_mesh::engine::Engine;
use taos_mesh::link::{Connector, FakeLink, Link, LinkKind};
use taos_mesh::meshtastic::frame;

struct Fake {
    link: FakeLink,
    opens: AtomicUsize,
}
impl Connector for Fake {
    fn describe(&self) -> String {
        "/dev/ttyFAKE0".into()
    }
    fn kind(&self) -> LinkKind {
        LinkKind::Serial
    }
    fn connect(&self) -> io::Result<Box<dyn Link>> {
        self.opens.fetch_add(1, Ordering::SeqCst);
        Ok(Box::new(self.link.clone()))
    }
}

#[test]
fn release_closes_the_radio_and_it_is_not_reconnected() {
    let t = TmpDir::new("release");
    std::env::set_var("XDG_DATA_HOME", t.path());
    let engine = Engine::open(&t.path().join("data"), "person:jay").unwrap();
    let d = Arc::new(Daemon::new(engine.clone(), false));
    let fx = fixture("meshtastic_device.json");
    let link = FakeLink::new("/dev/ttyFAKE0");
    link.reply_on_write(&{
        let mut s = vec![];
        for n in ["my_info", "config_complete"] {
            s.extend(frame(&frame_named(&fx, n)));
        }
        s
    });
    let c = Arc::new(Fake {
        link,
        opens: AtomicUsize::new(0),
    });
    d.attach_supervised(c.clone(), "meshtastic", None);
    let end = Instant::now() + Duration::from_secs(5);
    while !engine.radios().iter().any(|r| r.connected && r.ready) {
        assert!(Instant::now() < end, "radio never connected");
        std::thread::sleep(Duration::from_millis(20));
    }
    let r = d.handle_trusted(&json!({"id": 1, "method": "radios.release", "principal": "owner", "params": {"device": "/dev/ttyFAKE0"}}).to_string());
    assert_eq!(r["ok"], true, "{r}");
    assert_eq!(r["result"]["was_open"], true);
    assert!(
        engine.radios().is_empty(),
        "the stale entry is gone: {:?}",
        engine.radios().len()
    );
    let opens = c.opens.load(Ordering::SeqCst);
    std::thread::sleep(Duration::from_millis(1500));
    assert_eq!(
        c.opens.load(Ordering::SeqCst),
        opens,
        "a released radio is never reopened"
    );
}

#[test]
fn only_the_owner_may_release() {
    let t = TmpDir::new("release2");
    let engine = Engine::open(&t.path().join("data"), "person:jay").unwrap();
    let d = Daemon::new(engine, false);
    let r = d.handle_trusted(&json!({"id": 1, "method": "radios.release", "principal": "agent:scout", "params": {"device": "/dev/ttyFAKE0"}}).to_string());
    assert_eq!(r["ok"], false);
}

#[test]
fn connecting_a_port_that_is_already_open_does_not_open_it_twice() {
    let t = TmpDir::new("release3");
    std::env::set_var("XDG_DATA_HOME", t.path());
    let engine = Engine::open(&t.path().join("data"), "person:jay").unwrap();
    let d = Arc::new(Daemon::new(engine.clone(), false));
    let fx = fixture("meshtastic_device.json");
    let link = FakeLink::new("/dev/ttyFAKE0");
    link.reply_on_write(&frame(&frame_named(&fx, "my_info")));
    let c = Arc::new(Fake {
        link,
        opens: AtomicUsize::new(0),
    });
    d.attach_supervised(c.clone(), "meshtastic", None);
    let end = Instant::now() + Duration::from_secs(5);
    while !engine.radios().iter().any(|r| r.connected) {
        assert!(Instant::now() < end, "radio never connected");
        std::thread::sleep(Duration::from_millis(20));
    }
    let r = d.handle_trusted(&json!({"id": 1, "method": "connect", "principal": "owner", "params": {"device": "/dev/ttyFAKE0", "network": "meshtastic"}}).to_string());
    assert_eq!(r["ok"], true, "{r}");
    assert_eq!(r["result"]["already_open"], true);
    assert_eq!(
        c.opens.load(Ordering::SeqCst),
        1,
        "the port was opened once"
    );
}
