// SPDX-License-Identifier: AGPL-3.0-or-later
//! Bluetooth LE transport for both networks, over BlueZ on the system D-Bus.
//!
//! Meshtastic (docs/development/device/client-api): service
//! `6ba1b218-15a8-461f-9fa8-5dcae273eafd` with characteristics ToRadio
//! (write), FromRadio (read, one `FromRadio` protobuf per read, empty when
//! the queue is empty) and FromNum (notify: new data is waiting). The
//! protobufs are written and read without the `0x94 0xC3` stream header.
//!
//! MeshCore (companion_protocol.md): Nordic UART service
//! `6E400001-B5A3-F393-E0A9-E50E24DCCA9E`; RX `...0002` is written by the
//! app, TX `...0003` notifies the app; one write or notification is exactly
//! one companion frame, with no length prefix.
//!
//! `BleLink` presents either as the same byte stream the serial link gives
//! (it adds and strips the stream headers), so the drivers and sessions are
//! unchanged. The GATT layer is the `Gatt` trait: `BluezGatt` is the real
//! one, `FakeGatt` is the test one. Pairing is left to BlueZ: an unpaired
//! device is paired through BlueZ's own agent, and a failure is reported in
//! words rather than worked around.

use crate::link::{Link, LinkKind};
use crate::meshtastic::{frame as mt_frame, StreamDecoder};
use crate::model::{NETWORK_MESHCORE, NETWORK_MESHTASTIC};
use std::collections::{HashMap, VecDeque};
use std::io;
use std::sync::{Arc, Mutex};
use std::time::{Duration, Instant};

pub const MT_TORADIO: &str = "f75c76d2-129e-4dad-a1dd-7866124401e7";
pub const MT_FROMRADIO: &str = "2c55e69e-4993-11ed-b878-0242ac120002";
pub const MT_FROMNUM: &str = "ed9da18c-a800-4f66-a670-aa7547e34453";
pub const MC_RX: &str = "6e400002-b5a3-f393-e0a9-e50e24dcca9e";
pub const MC_TX: &str = "6e400003-b5a3-f393-e0a9-e50e24dcca9e";

/// One GATT connection to a device. Characteristics are named by UUID.
pub trait Gatt: Send {
    fn name(&self) -> String;
    fn write(&mut self, uuid: &str, data: &[u8]) -> io::Result<()>;
    fn read(&mut self, uuid: &str) -> io::Result<Vec<u8>>;
    /// The next notification (characteristic uuid, value), or `None` after
    /// `wait`. `Err` when the device has disconnected.
    fn notify(&mut self, wait: Duration) -> io::Result<Option<(String, Vec<u8>)>>;
}

pub struct BleLink {
    gatt: Box<dyn Gatt>,
    network: &'static str,
    out: VecDeque<u8>,
    to_radio: StreamDecoder,
    mc_in: Vec<u8>,
    drain: bool,
    last_poll: Instant,
}

impl BleLink {
    pub fn new(gatt: Box<dyn Gatt>, network: &str) -> io::Result<Self> {
        let network = match network {
            NETWORK_MESHTASTIC => NETWORK_MESHTASTIC,
            NETWORK_MESHCORE => NETWORK_MESHCORE,
            other => {
                return Err(io::Error::new(
                    io::ErrorKind::InvalidInput,
                    format!("no BLE service for {other}"),
                ))
            }
        };
        Ok(BleLink {
            gatt,
            network,
            out: VecDeque::new(),
            to_radio: StreamDecoder::new(),
            mc_in: Vec::new(),
            drain: true,
            last_poll: Instant::now(),
        })
    }

    /// Read FromRadio until it returns an empty value.
    fn drain_from_radio(&mut self) -> io::Result<()> {
        for _ in 0..4096 {
            let v = self.gatt.read(MT_FROMRADIO)?;
            if v.is_empty() {
                break;
            }
            self.out.extend(mt_frame(&v));
        }
        self.drain = false;
        self.last_poll = Instant::now();
        Ok(())
    }
}

impl Link for BleLink {
    fn name(&self) -> String {
        format!("ble:{}", self.gatt.name())
    }
    fn kind(&self) -> LinkKind {
        LinkKind::Ble
    }

    fn read(&mut self, buf: &mut [u8]) -> io::Result<usize> {
        if self.out.is_empty() {
            if self.network == NETWORK_MESHTASTIC {
                match self.gatt.notify(Duration::from_millis(100))? {
                    Some((u, _)) if u == MT_FROMNUM => self.drain = true,
                    _ => {}
                }
                // FromNum notifications can be missed: poll once a second too
                if self.drain || self.last_poll.elapsed() > Duration::from_secs(1) {
                    self.drain_from_radio()?;
                }
            } else if let Some((u, v)) = self.gatt.notify(Duration::from_millis(200))? {
                if u == MC_TX {
                    self.out.extend([b'>', v.len() as u8, (v.len() >> 8) as u8]);
                    self.out.extend(v);
                }
            }
        }
        let n = buf.len().min(self.out.len());
        for (i, b) in self.out.drain(..n).enumerate() {
            buf[i] = b;
        }
        Ok(n)
    }

    /// Takes the stream form the sessions write (wake bytes and headers
    /// included) and writes each protobuf or frame to its characteristic.
    fn write_all(&mut self, data: &[u8]) -> io::Result<()> {
        if self.network == NETWORK_MESHTASTIC {
            for payload in self.to_radio.push(data) {
                self.gatt.write(MT_TORADIO, &payload)?;
                self.drain = true;
            }
        } else {
            self.mc_in.extend_from_slice(data);
            loop {
                match self.mc_in.iter().position(|b| *b == b'<') {
                    None => {
                        self.mc_in.clear();
                        break;
                    }
                    Some(i) => {
                        self.mc_in.drain(..i);
                    }
                }
                if self.mc_in.len() < 3 {
                    break;
                }
                let len = u16::from_le_bytes([self.mc_in[1], self.mc_in[2]]) as usize;
                if self.mc_in.len() < 3 + len {
                    break;
                }
                let payload: Vec<u8> = self.mc_in[3..3 + len].to_vec();
                self.mc_in.drain(..3 + len);
                self.gatt.write(MC_RX, &payload)?;
            }
        }
        Ok(())
    }
}

// ---- fake ----------------------------------------------------------------

#[derive(Default)]
pub struct FakeGattState {
    pub from_radio: VecDeque<Vec<u8>>,
    pub notifications: VecDeque<(String, Vec<u8>)>,
    /// (uuid, bytes) in write order
    pub writes: Vec<(String, Vec<u8>)>,
    pub connected: bool,
    /// Released into `from_radio` (with a FromNum notification) on the first ToRadio write.
    pub answer_on_write: VecDeque<Vec<u8>>,
}

/// A fake GATT device for tests.
#[derive(Clone, Default)]
pub struct FakeGatt {
    pub state: Arc<Mutex<FakeGattState>>,
}

impl FakeGatt {
    pub fn new() -> Self {
        let g = FakeGatt::default();
        g.state.lock().unwrap().connected = true;
        g
    }
    pub fn disconnect(&self) {
        self.state.lock().unwrap().connected = false;
    }
    pub fn queue_from_radio(&self, v: &[u8]) {
        let mut s = self.state.lock().unwrap();
        s.from_radio.push_back(v.to_vec());
        s.notifications
            .push_back((MT_FROMNUM.into(), vec![1, 0, 0, 0]));
    }
    pub fn notify_tx(&self, v: &[u8]) {
        self.state
            .lock()
            .unwrap()
            .notifications
            .push_back((MC_TX.into(), v.to_vec()));
    }
    pub fn writes(&self) -> Vec<(String, Vec<u8>)> {
        self.state.lock().unwrap().writes.clone()
    }
}

impl Gatt for FakeGatt {
    fn name(&self) -> String {
        "AA:BB:CC:DD:EE:FF".into()
    }
    fn write(&mut self, uuid: &str, data: &[u8]) -> io::Result<()> {
        let mut s = self.state.lock().unwrap();
        if !s.connected {
            return Err(io::Error::new(io::ErrorKind::NotConnected, "disconnected"));
        }
        s.writes.push((uuid.into(), data.to_vec()));
        if uuid == MT_TORADIO {
            if let Some(a) = s.answer_on_write.pop_front() {
                s.from_radio.push_back(a);
                s.notifications
                    .push_back((MT_FROMNUM.into(), vec![1, 0, 0, 0]));
            }
        }
        Ok(())
    }
    fn read(&mut self, uuid: &str) -> io::Result<Vec<u8>> {
        let mut s = self.state.lock().unwrap();
        if !s.connected {
            return Err(io::Error::new(io::ErrorKind::NotConnected, "disconnected"));
        }
        assert_eq!(uuid, MT_FROMRADIO);
        Ok(s.from_radio.pop_front().unwrap_or_default())
    }
    fn notify(&mut self, wait: Duration) -> io::Result<Option<(String, Vec<u8>)>> {
        let n = {
            let mut s = self.state.lock().unwrap();
            if !s.connected {
                return Err(io::Error::new(io::ErrorKind::NotConnected, "disconnected"));
            }
            s.notifications.pop_front()
        };
        if n.is_none() {
            std::thread::sleep(wait.min(Duration::from_millis(2)));
        }
        Ok(n)
    }
}

// ---- BlueZ ---------------------------------------------------------------

type Props = HashMap<String, zbus::zvariant::OwnedValue>;
type Managed = HashMap<zbus::zvariant::OwnedObjectPath, HashMap<String, Props>>;

enum Event {
    Value(String, Vec<u8>),
    Disconnected,
}

/// GATT through BlueZ over the system bus. Never exercised against a real
/// radio in this repository's tests (no BLE radio is attached to the build
/// host); the logic above it is tested against `FakeGatt`.
pub struct BluezGatt {
    conn: zbus::blocking::Connection,
    address: String,
    chars: HashMap<String, String>,
    rx: std::sync::mpsc::Receiver<Event>,
}

fn io_err<E: std::fmt::Display>(e: E) -> io::Error {
    io::Error::other(e.to_string())
}

fn managed(conn: &zbus::blocking::Connection) -> io::Result<Managed> {
    let m = conn
        .call_method(
            Some("org.bluez"),
            "/",
            Some("org.freedesktop.DBus.ObjectManager"),
            "GetManagedObjects",
            &(),
        )
        .map_err(io_err)?;
    m.body().deserialize::<Managed>().map_err(io_err)
}

fn prop_str(p: &Props, k: &str) -> Option<String> {
    p.get(k)
        .and_then(|v| String::try_from(v.try_clone().ok()?).ok())
}
fn prop_bool(p: &Props, k: &str) -> Option<bool> {
    p.get(k)
        .and_then(|v| bool::try_from(v.try_clone().ok()?).ok())
}

impl BluezGatt {
    /// Connect to the device with this address, pairing through BlueZ if it
    /// is not paired yet, wait for its services, and subscribe to the
    /// notifying characteristic of `network`.
    pub fn connect(address: &str, network: &str) -> io::Result<Self> {
        let conn = zbus::blocking::Connection::system()
            .map_err(|e| io_err(format!("no system bus: {e}")))?;
        let objs = managed(&conn).map_err(|e| io_err(format!("BlueZ is not available: {e}")))?;
        let dev = objs
            .iter()
            .find(|(_, i)| {
                i.get("org.bluez.Device1")
                    .and_then(|p| prop_str(p, "Address"))
                    .is_some_and(|a| a.eq_ignore_ascii_case(address))
            })
            .map(|(p, _)| p.to_string())
            .ok_or_else(|| {
                io_err(format!(
                    "BlueZ does not know {address}: scan for it in the system Bluetooth settings first"
                ))
            })?;
        let dev1 = objs[&zbus::zvariant::OwnedObjectPath::try_from(dev.clone()).map_err(io_err)?]
            ["org.bluez.Device1"]
            .clone();
        let call = |path: &str, iface: &str, method: &str| {
            conn.call_method(Some("org.bluez"), path, Some(iface), method, &())
        };
        if prop_bool(&dev1, "Paired") != Some(true) {
            call(&dev, "org.bluez.Device1", "Pair").map_err(|e| {
                io_err(format!(
                    "pairing through BlueZ failed ({e}); pair the radio in the system Bluetooth settings (it may ask for a PIN) and try again"
                ))
            })?;
        }
        if prop_bool(&dev1, "Connected") != Some(true) {
            call(&dev, "org.bluez.Device1", "Connect").map_err(io_err)?;
        }
        // wait for GATT services to resolve
        let deadline = Instant::now() + Duration::from_secs(15);
        loop {
            let o = managed(&conn)?;
            let ok = o
                .iter()
                .find(|(p, _)| p.as_str() == dev)
                .and_then(|(_, i)| i.get("org.bluez.Device1"))
                .and_then(|p| prop_bool(p, "ServicesResolved"))
                == Some(true);
            if ok {
                break;
            }
            if Instant::now() > deadline {
                return Err(io_err("BLE services did not resolve in 15 s"));
            }
            std::thread::sleep(Duration::from_millis(250));
        }
        let objs = managed(&conn)?;
        let mut chars = HashMap::new();
        for (p, i) in &objs {
            if !p.as_str().starts_with(&dev) {
                continue;
            }
            if let Some(c) = i.get("org.bluez.GattCharacteristic1") {
                if let Some(u) = prop_str(c, "UUID") {
                    chars.insert(u.to_lowercase(), p.to_string());
                }
            }
        }
        let (need, notify_uuid): (&[&str], &str) = if network == NETWORK_MESHTASTIC {
            (&[MT_TORADIO, MT_FROMRADIO, MT_FROMNUM], MT_FROMNUM)
        } else {
            (&[MC_RX, MC_TX], MC_TX)
        };
        for u in need {
            if !chars.contains_key(*u) {
                return Err(io_err(format!(
                    "the device does not offer the {network} characteristic {u}"
                )));
            }
        }
        // signals first, then StartNotify, so no notification is missed
        let (tx, rx) = std::sync::mpsc::channel();
        let by_path: HashMap<String, String> =
            chars.iter().map(|(u, p)| (p.clone(), u.clone())).collect();
        let rule = zbus::MatchRule::builder()
            .msg_type(zbus::message::Type::Signal)
            .interface("org.freedesktop.DBus.Properties")
            .map_err(io_err)?
            .member("PropertiesChanged")
            .map_err(io_err)?
            .path_namespace(dev.as_str())
            .map_err(io_err)?
            .build();
        let it = zbus::blocking::MessageIterator::for_match_rule(rule, &conn, Some(64))
            .map_err(io_err)?;
        let dev_path = dev.clone();
        std::thread::spawn(move || {
            for m in it {
                let Ok(m) = m else { continue };
                let h = m.header();
                let path = h.path().map(|p| p.to_string()).unwrap_or_default();
                let Ok((iface, changed, _inv)) =
                    m.body().deserialize::<(String, Props, Vec<String>)>()
                else {
                    continue;
                };
                if iface == "org.bluez.GattCharacteristic1" {
                    if let (Some(u), Some(v)) = (by_path.get(&path), changed.get("Value")) {
                        if let Ok(v) = Vec::<u8>::try_from(v.try_clone().unwrap()) {
                            if tx.send(Event::Value(u.clone(), v)).is_err() {
                                return;
                            }
                        }
                    }
                } else if iface == "org.bluez.Device1"
                    && path == dev_path
                    && prop_bool(&changed, "Connected") == Some(false)
                {
                    let _ = tx.send(Event::Disconnected);
                    return;
                }
            }
        });
        call(
            &chars[notify_uuid],
            "org.bluez.GattCharacteristic1",
            "StartNotify",
        )
        .map_err(io_err)?;
        Ok(BluezGatt {
            conn,
            address: address.to_string(),
            chars,
            rx,
        })
    }

    fn path(&self, uuid: &str) -> io::Result<&String> {
        self.chars
            .get(uuid)
            .ok_or_else(|| io_err(format!("no characteristic {uuid}")))
    }
}

impl Gatt for BluezGatt {
    fn name(&self) -> String {
        self.address.clone()
    }
    fn write(&mut self, uuid: &str, data: &[u8]) -> io::Result<()> {
        let p = self.path(uuid)?.clone();
        let opts: HashMap<&str, zbus::zvariant::Value> = HashMap::new();
        self.conn
            .call_method(
                Some("org.bluez"),
                p.as_str(),
                Some("org.bluez.GattCharacteristic1"),
                "WriteValue",
                &(data.to_vec(), opts),
            )
            .map(|_| ())
            .map_err(io_err)
    }
    fn read(&mut self, uuid: &str) -> io::Result<Vec<u8>> {
        let p = self.path(uuid)?.clone();
        let opts: HashMap<&str, zbus::zvariant::Value> = HashMap::new();
        let m = self
            .conn
            .call_method(
                Some("org.bluez"),
                p.as_str(),
                Some("org.bluez.GattCharacteristic1"),
                "ReadValue",
                &(opts,),
            )
            .map_err(io_err)?;
        m.body().deserialize::<Vec<u8>>().map_err(io_err)
    }
    fn notify(&mut self, wait: Duration) -> io::Result<Option<(String, Vec<u8>)>> {
        match self.rx.recv_timeout(wait) {
            Ok(Event::Value(u, v)) => Ok(Some((u, v))),
            Ok(Event::Disconnected) => Err(io::Error::new(
                io::ErrorKind::NotConnected,
                "BLE device disconnected",
            )),
            Err(std::sync::mpsc::RecvTimeoutError::Timeout) => Ok(None),
            Err(_) => Err(io::Error::new(
                io::ErrorKind::NotConnected,
                "BlueZ signal stream ended",
            )),
        }
    }
}

pub struct BleConnector {
    pub address: String,
    pub network: String,
}

impl crate::link::Connector for BleConnector {
    fn describe(&self) -> String {
        format!("ble:{}", self.address)
    }
    fn kind(&self) -> LinkKind {
        LinkKind::Ble
    }
    fn connect(&self) -> io::Result<Box<dyn Link>> {
        let g = BluezGatt::connect(&self.address, &self.network)?;
        Ok(Box::new(BleLink::new(Box::new(g), &self.network)?))
    }
}
