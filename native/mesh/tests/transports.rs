// SPDX-License-Identifier: AGPL-3.0-or-later
//! BLE transport (fake GATT), reconnect with back-off, HUPCL, and fsync order.
mod common;
use common::*;
use serde_json::json;
use std::collections::VecDeque;
use std::io;
use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::{Arc, Mutex};
use std::time::Duration;
use taos_mesh::ble::*;
use taos_mesh::engine::{Engine, Filter, Session};
use taos_mesh::link::{Connector, FakeLink, Link, LinkKind};
use taos_mesh::meshtastic::frame;
use taos_mesh::model::*;
use taos_mesh::store::{LogFile, Store};
use taos_mesh::supervisor::{run, Backoff, Supervised};

fn mt_fixture(name: &str) -> Vec<u8> {
    frame_named(&fixture("meshtastic.json"), name)
}

// ---- BLE -----------------------------------------------------------------

#[test]
fn meshtastic_over_ble_writes_bare_protobufs_and_reads_fromradio_until_empty() {
    let t = TmpDir::new("ble-mt");
    let e = Engine::open(t.path(), "person:jay").unwrap();
    let g = FakeGatt::new();
    // the radio answers the first ToRadio with its config, one FromRadio per read
    for n in [
        "my_info_heltec_v2_1",
        "node_info_heltec_v2_1",
        "text_broadcast",
    ] {
        g.state
            .lock()
            .unwrap()
            .answer_on_write
            .push_back(mt_fixture(n));
    }
    let link = BleLink::new(Box::new(g.clone()), "meshtastic").unwrap();
    assert_eq!(link.kind(), LinkKind::Ble);
    let mut s = Session::new(e.clone(), Box::new(link), "meshtastic").unwrap();
    s.pump().unwrap(); // handshake
                       // first write is the want_config protobuf only: no wake bytes, no 0x94 0xC3 header
    let w = g.writes();
    assert_eq!(w[0].0, MT_TORADIO);
    assert_eq!(
        w[0].1,
        taos_mesh::meshtastic::encode_want_config(0x7a05_0001)
    );
    // the radio queued three FromRadio values; they arrive over the next pumps
    for _ in 0..3 {
        s.pump().unwrap();
    }
    assert_eq!(s.radio_id(), "!28af67cc");
    let recs = e.messages("owner", &Filter::default()).unwrap();
    assert_eq!(
        recs.len(),
        1,
        "only the first answer was queued (one per write)"
    );
    g.queue_from_radio(&mt_fixture("text_broadcast"));
    g.queue_from_radio(&mt_fixture("position_packet"));
    for _ in 0..3 {
        s.pump().unwrap();
    }
    let kinds: Vec<_> = e
        .messages("owner", &Filter::default())
        .unwrap()
        .into_iter()
        .map(|r| r.kind)
        .collect();
    assert!(
        kinds.contains(&"text".to_string()) && kinds.contains(&"position".to_string()),
        "{kinds:?}"
    );
    // the stored raw bytes are the FromRadio protobuf, identical to the serial path
    let r = e
        .messages(
            "owner",
            &Filter {
                kind: Some("text".into()),
                ..Default::default()
            },
        )
        .unwrap();
    assert_eq!(r[0].raw_hex, to_hex(&mt_fixture("text_broadcast")));
}

#[test]
fn a_meshtastic_send_over_ble_is_one_toradio_write() {
    let t = TmpDir::new("ble-send");
    let e = Engine::open(t.path(), "person:jay").unwrap();
    let g = FakeGatt::new();
    g.state
        .lock()
        .unwrap()
        .answer_on_write
        .push_back(mt_fixture("my_info_heltec_v2_1"));
    let mut s = Session::new(
        e.clone(),
        Box::new(BleLink::new(Box::new(g.clone()), "meshtastic").unwrap()),
        "meshtastic",
    )
    .unwrap();
    for _ in 0..3 {
        s.pump().unwrap();
    }
    let rec = e
        .send_text("owner", "meshtastic", None, None, Some(0), "over ble")
        .unwrap();
    s.pump().unwrap();
    let w = g.writes();
    let last = w.last().unwrap();
    assert_eq!(last.0, MT_TORADIO);
    assert_eq!(to_hex(&last.1), rec.raw_hex);
    assert!(
        !last.1.starts_with(&[0x94, 0xC3]),
        "no stream header on a GATT write"
    );
}

fn mc_self_info() -> Vec<u8> {
    hex(&fixture("meshcore.json")["frames"][0]["hex"])
}

#[test]
fn meshcore_over_ble_one_write_or_notification_is_one_frame() {
    let t = TmpDir::new("ble-mc");
    let e = Engine::open(t.path(), "person:jay").unwrap();
    let g = FakeGatt::new();
    let mut s = Session::new(
        e.clone(),
        Box::new(BleLink::new(Box::new(g.clone()), "meshcore").unwrap()),
        "meshcore",
    )
    .unwrap();
    s.pump().unwrap(); // handshake: APP_START, DEVICE_QUERY, GET_CONTACTS, SYNC
    let w = g.writes();
    assert_eq!(w.len(), 4);
    assert!(w.iter().all(|x| x.0 == MC_RX));
    assert_eq!(
        w[1].1,
        vec![0x16, 0x03],
        "bare DEVICE_QUERY, no '<' and no length"
    );
    g.notify_tx(&mc_self_info());
    let msg = {
        let fx = fixture("meshcore.json");
        let f = fx["frames"]
            .as_array()
            .unwrap()
            .iter()
            .find(|f| f["name"] == "channel_msg_v3")
            .unwrap()
            .clone();
        hex(&f["hex"])
    };
    g.notify_tx(&msg);
    for _ in 0..3 {
        s.pump().unwrap();
    }
    assert_eq!(s.radio_id(), "010203040506");
    let texts = e
        .messages(
            "owner",
            &Filter {
                kind: Some("text".into()),
                ..Default::default()
            },
        )
        .unwrap();
    assert_eq!(texts[0].text.as_deref(), Some("Bravo: water found"));
    // the driver asked for the next message: one more bare 0x0A write
    assert!(g.writes().iter().any(|x| x.1 == vec![0x0A]));
    let rec = e
        .send_text("owner", "meshcore", None, None, Some(1), "hi")
        .unwrap();
    s.pump().unwrap();
    let last = g.writes().pop().unwrap();
    assert_eq!(to_hex(&last.1), rec.raw_hex);
}

#[test]
fn a_ble_disconnect_is_a_link_error_the_session_reports() {
    let t = TmpDir::new("ble-drop");
    let e = Engine::open(t.path(), "person:jay").unwrap();
    let g = FakeGatt::new();
    let mut s = Session::new(
        e.clone(),
        Box::new(BleLink::new(Box::new(g.clone()), "meshtastic").unwrap()),
        "meshtastic",
    )
    .unwrap();
    s.pump().unwrap();
    g.disconnect();
    assert!(s.pump().is_err());
    assert!(!e.radios()[0].connected);
}

#[test]
fn ble_for_an_unknown_network_is_refused() {
    assert!(BleLink::new(Box::new(FakeGatt::new()), "reticulum").is_err());
}

// ---- back-off and reconnect -------------------------------------------------

#[test]
fn backoff_doubles_to_a_cap_and_resets() {
    let mut b = Backoff::new(Duration::from_secs(1), Duration::from_secs(10));
    let d: Vec<u64> = (0..6).map(|_| b.next_delay().as_secs()).collect();
    assert_eq!(d, vec![1, 2, 4, 8, 10, 10]);
    assert_eq!(b.attempt(), 6);
    b.reset();
    assert_eq!(b.next_delay().as_secs(), 1);
    let mut huge = Backoff::standard();
    for _ in 0..100 {
        assert!(huge.next_delay() <= Duration::from_secs(60));
    }
}

struct Scripted {
    steps: Mutex<VecDeque<io::Result<FakeLink>>>,
    label: String,
    calls: Mutex<u32>,
}
impl Connector for Scripted {
    fn describe(&self) -> String {
        self.label.clone()
    }
    fn kind(&self) -> LinkKind {
        LinkKind::Serial
    }
    fn connect(&self) -> io::Result<Box<dyn Link>> {
        *self.calls.lock().unwrap() += 1;
        match self.steps.lock().unwrap().pop_front() {
            Some(Ok(l)) => Ok(Box::new(l)),
            Some(Err(e)) => Err(e),
            None => Err(io::Error::new(io::ErrorKind::NotFound, "device gone")),
        }
    }
}

fn radio_link(texts: &[&str], then_drop: bool) -> FakeLink {
    let l = FakeLink::new("/dev/ttyACM0");
    let mut first = frame(&mt_fixture("my_info_heltec_v2_1"));
    first.extend(frame(&mt_fixture("node_info_heltec_v2_1")));
    l.reply_on_write(&first);
    for t in texts {
        let name = if *t == "a" {
            "text_broadcast"
        } else {
            "text_direct_utf8"
        };
        l.push(&frame(&mt_fixture(name)));
    }
    if then_drop {
        l.push_error(io::ErrorKind::BrokenPipe);
    }
    l
}

/// (delay seconds, radio state, attempt, last error) at each wait
type Seen = (u64, String, u32, Option<String>);

#[test]
fn a_dropped_link_is_retried_with_back_off_and_the_state_is_visible() {
    let t = TmpDir::new("sup");
    let e = Engine::open(t.path(), "person:jay").unwrap();
    let connector = Arc::new(Scripted {
        steps: Mutex::new(VecDeque::from(vec![
            Err(io::Error::new(io::ErrorKind::NotFound, "no such device")),
            Err(io::Error::new(io::ErrorKind::NotFound, "no such device")),
            Ok(radio_link(&["a"], true)),
            Err(io::Error::new(io::ErrorKind::NotFound, "unplugged")),
            Ok(radio_link(&["b"], false)),
        ])),
        label: "/dev/ttyACM0".into(),
        calls: Mutex::new(0),
    });
    let stop = Arc::new(AtomicBool::new(false));
    let seen: Arc<Mutex<Vec<Seen>>> = Arc::default();
    let sup = Supervised {
        engine: e.clone(),
        connector: connector.clone(),
        network: "meshtastic".into(),
        transmit: true,
        stop: stop.clone(),
        backoff: Backoff::standard(),
        first: None,
    };
    let (e2, seen2, stop2, c2) = (e.clone(), seen.clone(), stop.clone(), connector.clone());
    let h = std::thread::spawn(move || {
        run(sup, &move |d| {
            let r = &e2.radios()[0];
            seen2.lock().unwrap().push((
                d.as_secs(),
                r.state.clone(),
                r.attempt,
                r.last_error.clone(),
            ));
            let _ = (&c2, &stop2);
            std::thread::sleep(Duration::from_millis(30));
        })
    });
    // the last session has no end of its own: stop once both texts are stored
    let end = std::time::Instant::now() + Duration::from_secs(20);
    loop {
        let n = e
            .messages(
                "owner",
                &Filter {
                    kind: Some("text".into()),
                    ..Default::default()
                },
            )
            .unwrap()
            .len();
        if n >= 2 || std::time::Instant::now() > end {
            break;
        }
        std::thread::sleep(Duration::from_millis(20));
    }
    stop.store(true, Ordering::Relaxed);
    h.join().unwrap();
    let seen = seen.lock().unwrap().clone();
    let delays: Vec<u64> = seen.iter().map(|s| s.0).collect();
    // 2 failed connects (1s, 2s), then the first good session stored a frame
    // (back-off reset) and dropped (1s), then a failed connect (2s), then good
    assert_eq!(&delays[..4], &[1, 2, 1, 2], "{seen:?}");
    assert!(seen.iter().all(|s| s.1 == "reconnecting"));
    assert_eq!(seen[0].2, 1);
    assert_eq!(seen[1].2, 2);
    assert!(seen[0].3.as_ref().unwrap().contains("no such device"));
    // frames from both sessions are stored, under the radio's own id
    let texts: Vec<_> = e
        .messages(
            "owner",
            &Filter {
                kind: Some("text".into()),
                ..Default::default()
            },
        )
        .unwrap()
        .into_iter()
        .map(|r| r.text.unwrap())
        .collect();
    assert_eq!(texts, vec!["hello mesh", "caf\u{e9} \u{2713}"]);
    let radios = e.radios();
    assert_eq!(
        radios.len(),
        1,
        "one radio entry across reconnects: {radios:?}"
    );
    assert_eq!(radios[0].id, "!28af67cc");
    assert_eq!(radios[0].state, "offline");
    assert_eq!(radios[0].last_error.as_deref(), Some("stopped"));
}

#[test]
fn reconnect_state_goes_through_the_api_and_the_app_view() {
    let t = TmpDir::new("sup-api");
    let e = Engine::open(t.path(), "person:jay").unwrap();
    e.register_radio("/dev/ttyACM0", "meshtastic", "/dev/ttyACM0", "usb");
    e.radio_reconnecting("/dev/ttyACM0", 3, now_ms() + 4000, "connect failed: gone");
    let d = Arc::new(taos_mesh::api::Daemon {
        engine: e,
        receive_only: false,
        stop: Default::default(),
    });
    let r = d.handle(&json!({"id": 1, "method": "radios"}).to_string());
    let radio = &r["result"][0];
    assert_eq!(radio["state"], "reconnecting");
    assert_eq!(radio["attempt"], 3);
    assert!(radio["next_retry_ms"].as_u64().unwrap() > now_ms());
    assert_eq!(radio["last_error"], "connect failed: gone");
    let r = d.handle(&json!({"id": 1, "method": "app.radios"}).to_string());
    assert_eq!(
        r["result"][0]["state"], "connecting",
        "the app knows connected, connecting, offline"
    );
}

// ---- HUPCL ---------------------------------------------------------------------

#[test]
fn hupcl_is_cleared_on_a_terminal() {
    use nix::sys::termios::{tcgetattr, tcsetattr, ControlFlags, SetArg};
    let pty = nix::pty::openpty(None, None).unwrap();
    let mut t = tcgetattr(pty.slave).unwrap();
    t.control_flags.insert(ControlFlags::HUPCL);
    tcsetattr(pty.slave, SetArg::TCSANOW, &t).unwrap();
    assert!(tcgetattr(pty.slave)
        .unwrap()
        .control_flags
        .contains(ControlFlags::HUPCL));
    let path = nix::unistd::ttyname(pty.slave).unwrap();
    taos_mesh::link::clear_hupcl_path(path.to_str().unwrap()).unwrap();
    assert!(
        !tcgetattr(pty.slave)
            .unwrap()
            .control_flags
            .contains(ControlFlags::HUPCL),
        "HUPCL still set"
    );
}

#[test]
fn opening_a_serial_link_clears_hupcl_on_the_real_open_path() {
    use nix::sys::termios::{tcgetattr, tcsetattr, ControlFlags, SetArg};
    let pty = nix::pty::openpty(None, None).unwrap();
    let mut t = tcgetattr(pty.slave).unwrap();
    t.control_flags.insert(ControlFlags::HUPCL);
    tcsetattr(pty.slave, SetArg::TCSANOW, &t).unwrap();
    let path = nix::unistd::ttyname(pty.slave).unwrap();
    let link = taos_mesh::link::SerialLink::open(path.to_str().unwrap()).unwrap();
    assert!(!tcgetattr(pty.slave)
        .unwrap()
        .control_flags
        .contains(ControlFlags::HUPCL));
    drop(link);
}

// ---- fsync -----------------------------------------------------------------------

#[derive(Clone, Default)]
struct Recorder {
    events: Arc<Mutex<Vec<String>>>,
    fail_sync: Arc<AtomicBool>,
    /// checked inside sync: how many records has the subscriber been told about so far?
    told: Arc<Mutex<Option<std::sync::mpsc::Receiver<Record>>>>,
    told_n: Arc<Mutex<usize>>,
}
impl LogFile for Recorder {
    fn write_all(&mut self, d: &[u8]) -> io::Result<()> {
        self.events
            .lock()
            .unwrap()
            .push(format!("write {}", d.len()));
        Ok(())
    }
    fn sync_data(&mut self) -> io::Result<()> {
        if let Some(r) = self.told.lock().unwrap().as_ref() {
            while r.try_recv().is_ok() {
                *self.told_n.lock().unwrap() += 1;
            }
        }
        let told = *self.told_n.lock().unwrap();
        self.events
            .lock()
            .unwrap()
            .push(format!("sync (told {told})"));
        if self.fail_sync.load(Ordering::Relaxed) {
            return Err(io::Error::other("fsync failed"));
        }
        Ok(())
    }
}

fn text(n: u32) -> Inbound {
    Inbound {
        raw: vec![n as u8; 8],
        event: Event::Text {
            text: format!("m{n}"),
        },
        from: Some("!00000001".into()),
        to: Some("!ffffffff".into()),
        channel: Some(0),
        packet_id: Some(n),
        radio_ts: None,
        signal: Signal::default(),
        position: None,
        broadcast: true,
        trust: None,
    }
}

#[test]
fn every_acknowledged_record_was_written_then_fsynced_before_the_ack() {
    let t = TmpDir::new("fsync");
    let rec = Recorder::default();
    let store = Store::with_file(&t.path().join("records.jsonl"), Box::new(rec.clone()));
    let e = Engine::open_with_store(t.path(), "owner", store).unwrap();
    let rx = e.subscribe();
    *rec.told.lock().unwrap() = Some(rx);
    for n in 0..3 {
        e.ingest("meshtastic", "!0000aaaa", text(n)).unwrap();
        let ev = rec.events.lock().unwrap().clone();
        // at the moment ingest returned Ok, this record's write and sync are both on the log, in order
        assert_eq!(ev.len(), 2 * (n as usize + 1), "{ev:?}");
        assert!(ev[ev.len() - 2].starts_with("write"), "{ev:?}");
        assert!(ev[ev.len() - 1].starts_with("sync"), "{ev:?}");
        // while this record was being synced the subscriber had heard of the n records before it, not this one
        assert!(
            ev[ev.len() - 1].contains(&format!("(told {n})")),
            "listeners hear about a record only after its fsync: {ev:?}"
        );
    }
}

#[test]
fn a_failed_fsync_is_an_error_not_an_ack() {
    let t = TmpDir::new("fsync-fail");
    let rec = Recorder::default();
    rec.fail_sync.store(true, Ordering::Relaxed);
    let store = Store::with_file(&t.path().join("records.jsonl"), Box::new(rec.clone()));
    let e = Engine::open_with_store(t.path(), "owner", store).unwrap();
    let rx = e.subscribe();
    let err = e.ingest("meshtastic", "!0000aaaa", text(1)).unwrap_err();
    assert!(err.to_string().contains("fsync failed"), "{err}");
    assert!(
        rx.try_recv().is_err(),
        "nobody was told about a record that is not durable"
    );
    assert_eq!(e.health()["pending_unwritten"], 1);
    assert_eq!(e.health()["ok"], false);
}
