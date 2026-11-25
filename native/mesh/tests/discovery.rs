// SPDX-License-Identifier: AGPL-3.0-or-later
mod common;
use common::*;
use std::fs;
use std::path::Path;
use std::time::Duration;
use taos_mesh::discovery::*;
use taos_mesh::link::FakeLink;
use taos_mesh::meshtastic::frame;

/// Build a fake /sys and /dev tree with one USB serial port.
fn fake_sys(root: &Path, tty: &str, vid: &str, pid: &str) {
    let usb = root.join("devices/pci0/usb1/1-2");
    let iface = usb.join("1-2:1.0");
    let tty_dev = iface.join(tty);
    fs::create_dir_all(&tty_dev).unwrap();
    fs::write(usb.join("idVendor"), format!("{vid}\n")).unwrap();
    fs::write(usb.join("idProduct"), format!("{pid}\n")).unwrap();
    fs::write(usb.join("product"), "Test Radio\n").unwrap();
    fs::write(usb.join("manufacturer"), "Acme\n").unwrap();
    let class = root.join("class/tty");
    fs::create_dir_all(&class).unwrap();
    let c = class.join(tty);
    fs::create_dir_all(&c).unwrap();
    std::os::unix::fs::symlink(&iface, c.join("device")).unwrap();
}

#[test]
fn usb_serial_ports_are_found_by_vid_pid() {
    let t = TmpDir::new("sys");
    fake_sys(t.path(), "ttyACM0", "303a", "1001");
    let got = scan_serial(t.path(), Path::new("/dev"));
    assert_eq!(got.len(), 1);
    assert_eq!(got[0].path, "/dev/ttyACM0");
    assert_eq!((got[0].vid, got[0].pid), (0x303a, 0x1001));
    assert_eq!(got[0].product.as_deref(), Some("Test Radio"));
    assert!(got[0].hint.contains("Espressif"));
}

#[test]
fn unrelated_serial_devices_and_missing_sysfs_are_ignored_cleanly() {
    let t = TmpDir::new("sys2");
    fake_sys(t.path(), "ttyUSB3", "1234", "5678");
    assert!(scan_serial(t.path(), Path::new("/dev")).is_empty());
    assert!(scan_serial(&t.path().join("does-not-exist"), Path::new("/dev")).is_empty());
}

#[test]
fn vendor_wide_match_for_nrf52_boards() {
    let t = TmpDir::new("sys3");
    fake_sys(t.path(), "ttyACM1", "239a", "8029");
    assert_eq!(scan_serial(t.path(), Path::new("/dev")).len(), 1);
}

#[test]
fn probe_recognises_a_meshtastic_radio_and_keeps_every_byte_it_read() {
    let fx = fixture("meshtastic.json");
    let link = FakeLink::new("fake");
    let mut stream = b"boot noise".to_vec();
    stream.extend(frame(&frame_named(&fx, "my_info_heltec_v2_1")));
    stream.extend(frame(&frame_named(&fx, "node_info_heltec_v2_1")));
    link.reply_on_write(&stream);
    let mut l = link.clone();
    let p = probe(&mut l, Duration::from_millis(300));
    assert_eq!(p.network.as_deref(), Some("meshtastic"));
    assert_eq!(p.radio_id.as_deref(), Some("!28af67cc"));
    assert_eq!(p.received, stream, "what the probe read is handed on so no queued frame is lost");
    // the probe only sent the read-only handshake
    let w = link.written_bytes();
    assert_eq!(&w[..32], &[0xC3u8; 32][..]);
}

#[test]
fn probe_recognises_a_meshcore_radio() {
    let fx = fixture("meshcore.json");
    let link = FakeLink::new("fake-mc");
    let self_info = hex(&fx["frames"][0]["hex"]);
    let mut reply = vec![b'>', self_info.len() as u8, 0];
    reply.extend(&self_info);
    // The radio stays silent for the Meshtastic wake bytes and answers the
    // MeshCore commands: release the reply on the third write (handshake
    // frame, then APP_START, then DEVICE_QUERY).
    let mut l = link.clone();
    let h = std::thread::spawn({
        let link = link.clone();
        move || {
            // wait until the probe has written the MeshCore commands
            for _ in 0..200 {
                if link.written.lock().unwrap().len() >= 3 {
                    link.push(&reply);
                    return;
                }
                std::thread::sleep(Duration::from_millis(5));
            }
        }
    });
    let p = probe(&mut l, Duration::from_millis(400));
    h.join().unwrap();
    assert_eq!(p.network.as_deref(), Some("meshcore"), "{p:?}");
    assert_eq!(p.radio_id.as_deref(), Some("010203040506"));
}

#[test]
fn probe_of_a_silent_device_says_so() {
    let link = FakeLink::new("silent");
    let mut l = link.clone();
    let p = probe(&mut l, Duration::from_millis(60));
    assert_eq!(p.network, None);
    assert!(p.detail.contains("no mesh protocol"));
}

#[test]
fn probe_of_a_dead_link_reports_the_write_error() {
    struct Dead;
    impl taos_mesh::link::Link for Dead {
        fn name(&self) -> String {
            "dead".into()
        }
        fn kind(&self) -> taos_mesh::link::LinkKind {
            taos_mesh::link::LinkKind::Serial
        }
        fn read(&mut self, _: &mut [u8]) -> std::io::Result<usize> {
            Err(std::io::Error::other("gone"))
        }
        fn write_all(&mut self, _: &[u8]) -> std::io::Result<()> {
            Err(std::io::Error::other("gone"))
        }
    }
    let p = probe(&mut Dead, Duration::from_millis(50));
    assert!(p.detail.contains("write failed"));
}

struct Fixture(Result<serde_json::Value, String>);
impl BleBackend for Fixture {
    fn managed_objects(&self) -> Result<serde_json::Value, String> {
        self.0.clone()
    }
}

fn dev(addr: &str, name: &str, uuids: &[&str], rssi: i64) -> serde_json::Value {
    serde_json::json!({"org.bluez.Device1": {
        "Address": {"type": "s", "data": addr}, "Name": {"type": "s", "data": name},
        "UUIDs": {"type": "as", "data": uuids}, "RSSI": {"type": "n", "data": rssi}}})
}

#[test]
fn ble_scan_finds_devices_advertising_either_documented_service() {
    let objs = serde_json::json!({"type": "a{oa{sa{sv}}}", "data": [{
        "/org/bluez": {"org.bluez.AgentManager1": {}},
        "/org/bluez/hci0": {"org.bluez.Adapter1": {"Address": {"type": "s", "data": "00:11:22:33:44:55"}}},
        "/org/bluez/hci0/dev_A": dev("AA:AA:AA:AA:AA:01", "Meshtastic_abcd", &["6BA1B218-15A8-461F-9FA8-5DCAE273EAFD"], -60),
        "/org/bluez/hci0/dev_B": dev("AA:AA:AA:AA:AA:02", "MeshCore-Alpha", &["6e400001-b5a3-f393-e0a9-e50e24dcca9e"], -72),
        "/org/bluez/hci0/dev_C": dev("AA:AA:AA:AA:AA:03", "Headphones", &["0000110b-0000-1000-8000-00805f9b34fb"], -40),
    }]});
    let BleScan::Devices { devices } = scan_ble(&Fixture(Ok(objs))) else { panic!("expected devices") };
    assert_eq!(devices.len(), 2);
    let nets: Vec<_> = devices.iter().map(|d| (d.name.clone().unwrap(), d.network.clone())).collect();
    assert!(nets.contains(&("Meshtastic_abcd".into(), "meshtastic".into())));
    assert!(nets.contains(&("MeshCore-Alpha".into(), "meshcore".into())));
}

#[test]
fn a_host_without_a_ble_adapter_is_reported_not_crashed() {
    let no_adapter = serde_json::json!({"type": "a{oa{sa{sv}}}", "data": [{"/org/bluez": {"org.bluez.AgentManager1": {}}}]});
    assert!(matches!(scan_ble(&Fixture(Ok(no_adapter))), BleScan::NoAdapter { .. }));
    assert!(matches!(scan_ble(&Fixture(Err("busctl: not found".into()))), BleScan::NoAdapter { why } if why.contains("busctl")));
    assert!(matches!(scan_ble(&Fixture(Ok(serde_json::json!({"nonsense": 1})))), BleScan::NoAdapter { .. }));
}

#[test]
fn the_real_backend_on_this_host_never_panics() {
    // Whatever this machine has (BlueZ, no BlueZ, no busctl) the answer is a value.
    let _ = scan_ble(&BluezBusctl);
}
