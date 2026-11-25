// SPDX-License-Identifier: AGPL-3.0-or-later
//! Device discovery: USB serial ports by VID/PID with a read-only
//! handshake, and a BLE scan for the documented service UUIDs through
//! BlueZ. Nothing here transmits on a radio.

use crate::link::Link;
use crate::meshcore::{self, MeshcoreDriver};
use crate::meshtastic::MeshtasticDriver;
use crate::model::{Event, NETWORK_MESHCORE, NETWORK_MESHTASTIC};
use serde::Serialize;
use std::fs;
use std::path::{Path, PathBuf};
use std::time::{Duration, Instant};

pub const MESHTASTIC_BLE_SERVICE: &str = "6ba1b218-15a8-461f-9fa8-5dcae273eafd";
pub const MESHCORE_BLE_SERVICE: &str = "6e400001-b5a3-f393-e0a9-e50e24dcca9e";

/// USB IDs of the bridge chips and native USB stacks found on the common
/// boards. A match only makes a port a candidate; the handshake decides.
pub const KNOWN_USB: &[(u16, u16, &str)] = &[
    (0x10c4, 0xea60, "Silicon Labs CP210x"),
    (0x1a86, 0x7523, "WCH CH340"),
    (0x1a86, 0x55d4, "WCH CH9102"),
    (0x303a, 0x1001, "Espressif USB serial/JTAG"),
    (0x303a, 0x0002, "Espressif USB CDC"),
    (0x0403, 0x6001, "FTDI FT232"),
    (0x0403, 0x6015, "FTDI FT230X"),
];
/// Vendors whose every product is a candidate (nRF52 boards ship a UF2
/// bootloader with its own vendor id).
pub const KNOWN_VENDORS: &[(u16, &str)] = &[
    (0x239a, "Adafruit/nRF52 bootloader"),
    (0x2886, "Seeed Studio"),
    (0x1915, "Nordic Semiconductor"),
];

#[derive(Debug, Clone, Serialize, PartialEq)]
pub struct SerialCandidate {
    pub path: String,
    pub vid: u16,
    pub pid: u16,
    pub product: Option<String>,
    pub manufacturer: Option<String>,
    pub hint: String,
}

fn read_trim(p: &Path) -> Option<String> {
    fs::read_to_string(p).ok().map(|s| s.trim().to_string())
}

/// Walk `<sys>/class/tty/*` and read the USB ids from the parent USB device.
/// `sys_root` is `/sys` in production and a fixture directory in tests;
/// `dev_root` is `/dev`.
pub fn scan_serial(sys_root: &Path, dev_root: &Path) -> Vec<SerialCandidate> {
    let mut out = Vec::new();
    let Ok(rd) = fs::read_dir(sys_root.join("class/tty")) else {
        return out;
    };
    for e in rd.flatten() {
        let name = e.file_name().to_string_lossy().into_owned();
        if !(name.starts_with("ttyACM") || name.starts_with("ttyUSB")) {
            continue;
        }
        let dev = e.path().join("device");
        let Ok(real) = fs::canonicalize(&dev) else {
            continue;
        };
        // the interface directory is one level below the USB device
        let mut cur: Option<PathBuf> = Some(real);
        let mut ids = None;
        for _ in 0..4 {
            let Some(p) = cur else { break };
            if let (Some(v), Some(pi)) = (
                read_trim(&p.join("idVendor")),
                read_trim(&p.join("idProduct")),
            ) {
                if let (Ok(v), Ok(pi)) = (u16::from_str_radix(&v, 16), u16::from_str_radix(&pi, 16))
                {
                    ids = Some((
                        v,
                        pi,
                        read_trim(&p.join("product")),
                        read_trim(&p.join("manufacturer")),
                    ));
                    break;
                }
            }
            cur = p.parent().map(|x| x.to_path_buf());
        }
        let Some((vid, pid, product, manufacturer)) = ids else {
            continue;
        };
        let hint = KNOWN_USB
            .iter()
            .find(|(v, p, _)| *v == vid && *p == pid)
            .map(|x| x.2.to_string())
            .or_else(|| {
                KNOWN_VENDORS
                    .iter()
                    .find(|(v, _)| *v == vid)
                    .map(|x| x.1.to_string())
            });
        if let Some(hint) = hint {
            out.push(SerialCandidate {
                path: dev_root.join(&name).display().to_string(),
                vid,
                pid,
                product,
                manufacturer,
                hint,
            });
        }
    }
    out.sort_by(|a, b| a.path.cmp(&b.path));
    out
}

#[derive(Debug, Clone, Serialize, PartialEq)]
pub struct Probe {
    pub network: Option<String>,
    pub radio_id: Option<String>,
    pub detail: String,
    /// Every byte the radio sent during the probe. The caller must hand
    /// these to the session it opens so nothing the radio already gave up
    /// (queued messages) is lost.
    #[serde(skip)]
    pub received: Vec<u8>,
}

fn read_for(link: &mut dyn Link, window: Duration, mut on_bytes: impl FnMut(&[u8]) -> bool) {
    let end = Instant::now() + window;
    let mut buf = [0u8; 512];
    while Instant::now() < end {
        match link.read(&mut buf) {
            Ok(0) => {}
            Ok(n) => {
                if on_bytes(&buf[..n]) {
                    return;
                }
            }
            Err(_) => return,
        }
    }
}

/// Read-only handshake: ask a Meshtastic radio for its configuration; if no
/// Meshtastic frame comes back, ask for a MeshCore APP_START/DEVICE_QUERY.
/// Neither request makes a radio transmit.
pub fn probe(link: &mut dyn Link, window: Duration) -> Probe {
    let mut d = MeshtasticDriver::new();
    let hs = d.handshake(0x7a05_0000);
    if let Err(e) = link.write_all(&hs) {
        return Probe {
            network: None,
            radio_id: None,
            detail: format!("write failed: {e}"),
            received: vec![],
        };
    }
    let mut seen_frames = 0;
    let mut received: Vec<u8> = Vec::new();
    read_for(link, window, |b| {
        received.extend_from_slice(b);
        seen_frames += d.feed(b).len();
        d.my_node.is_some()
    });
    if d.my_node.is_some() {
        return Probe {
            network: Some(NETWORK_MESHTASTIC.into()),
            radio_id: d.radio_id(),
            detail: format!("{seen_frames} frames"),
            received,
        };
    }
    let mut mcd = MeshcoreDriver::new("taos-mesh");
    for c in [meshcore::app_start("taos-mesh"), meshcore::device_query()] {
        if link.write_all(&meshcore::frame_to_radio(&c)).is_err() {
            break;
        }
    }
    let mut found = false;
    read_for(link, window, |b| {
        received.extend_from_slice(b);
        let (frames, _) = mcd.feed_stream(b);
        for f in frames {
            if matches!(f.event, Event::Control { ref what } if what == "self_info" || what == "device_info")
            {
                found = true;
            }
        }
        found && mcd.self_prefix.is_some()
    });
    if found {
        return Probe {
            network: Some(NETWORK_MESHCORE.into()),
            radio_id: mcd.radio_id(),
            detail: "companion protocol answered".into(),
            received,
        };
    }
    Probe {
        network: None,
        radio_id: None,
        detail: "no mesh protocol answered".into(),
        received,
    }
}

// ---- BLE ---------------------------------------------------------------

#[derive(Debug, Clone, Serialize, PartialEq)]
pub struct BleDevice {
    pub address: String,
    pub name: Option<String>,
    pub network: String,
    pub rssi: Option<i32>,
}

#[derive(Debug, Clone, Serialize, PartialEq)]
#[serde(tag = "state", rename_all = "snake_case")]
pub enum BleScan {
    /// No BlueZ, or BlueZ without an adapter. Not an error: many hosts have none.
    NoAdapter {
        why: String,
    },
    Devices {
        devices: Vec<BleDevice>,
    },
}

/// Where BLE device facts come from. The real backend asks BlueZ over
/// D-Bus through `busctl`; tests use a fixture.
pub trait BleBackend {
    /// BlueZ `GetManagedObjects` as JSON, or an error string when the bus or
    /// BlueZ is absent.
    fn managed_objects(&self) -> Result<serde_json::Value, String>;
}

pub struct BluezBusctl;

impl BleBackend for BluezBusctl {
    fn managed_objects(&self) -> Result<serde_json::Value, String> {
        let out = std::process::Command::new("busctl")
            .args([
                "--system",
                "--auto-start=no",
                "--json=short",
                "call",
                "org.bluez",
                "/",
                "org.freedesktop.DBus.ObjectManager",
                "GetManagedObjects",
            ])
            .output()
            .map_err(|e| format!("busctl: {e}"))?;
        if !out.status.success() {
            return Err(String::from_utf8_lossy(&out.stderr).trim().to_string());
        }
        serde_json::from_slice(&out.stdout).map_err(|e| e.to_string())
    }
}

fn dig<'a>(v: &'a serde_json::Value, k: &str) -> Option<&'a serde_json::Value> {
    v.get(k).and_then(|x| x.get("data"))
}

/// Read what BlueZ already knows (a passive read: it does not start
/// discovery). Devices advertising either service UUID are returned.
pub fn scan_ble(b: &dyn BleBackend) -> BleScan {
    let v = match b.managed_objects() {
        Ok(v) => v,
        Err(e) => return BleScan::NoAdapter { why: e },
    };
    let Some(objs) = v
        .get("data")
        .and_then(|d| d.get(0))
        .and_then(|d| d.as_object())
    else {
        return BleScan::NoAdapter {
            why: "unexpected BlueZ reply".into(),
        };
    };
    if !objs
        .values()
        .any(|ifs| ifs.get("org.bluez.Adapter1").is_some())
    {
        return BleScan::NoAdapter {
            why: "BlueZ has no Bluetooth adapter".into(),
        };
    }
    let mut devices = Vec::new();
    for ifs in objs.values() {
        let Some(d) = ifs.get("org.bluez.Device1") else {
            continue;
        };
        let uuids: Vec<String> = dig(d, "UUIDs")
            .and_then(|u| u.as_array())
            .map(|a| {
                a.iter()
                    .filter_map(|x| x.as_str().map(|s| s.to_lowercase()))
                    .collect()
            })
            .unwrap_or_default();
        let network = if uuids.iter().any(|u| u == MESHTASTIC_BLE_SERVICE) {
            NETWORK_MESHTASTIC
        } else if uuids.iter().any(|u| u == MESHCORE_BLE_SERVICE) {
            NETWORK_MESHCORE
        } else {
            continue;
        };
        devices.push(BleDevice {
            address: dig(d, "Address")
                .and_then(|x| x.as_str())
                .unwrap_or("")
                .to_string(),
            name: dig(d, "Name").and_then(|x| x.as_str()).map(String::from),
            network: network.into(),
            rssi: dig(d, "RSSI").and_then(|x| x.as_i64()).map(|x| x as i32),
        });
    }
    BleScan::Devices { devices }
}
