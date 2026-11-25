// SPDX-License-Identifier: AGPL-3.0-or-later
//! Host links to a radio: USB serial, TCP, BLE (in `ble.rs`), and a
//! scripted fake for tests. A `Connector` makes a fresh link each time the
//! supervisor (re)connects.

use std::collections::VecDeque;
use std::io::{self, Read, Write};
use std::net::{TcpStream, ToSocketAddrs};
use std::sync::{Arc, Mutex};
use std::time::Duration;

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum LinkKind {
    Serial,
    Tcp,
    Ble,
}

pub trait Link: Send {
    fn name(&self) -> String;
    fn kind(&self) -> LinkKind;
    /// Read what is available. `Ok(0)` means nothing arrived within the
    /// link's short timeout; a closed or failed link is an `Err`.
    fn read(&mut self, buf: &mut [u8]) -> io::Result<usize>;
    fn write_all(&mut self, data: &[u8]) -> io::Result<()>;
}

/// Makes a new link on demand; the supervisor calls it again after a drop.
pub trait Connector: Send + Sync {
    /// Stable text for this device ("/dev/ttyACM0", "tcp:host:4403", "ble:AA:BB...").
    fn describe(&self) -> String;
    fn kind(&self) -> LinkKind;
    fn connect(&self) -> io::Result<Box<dyn Link>>;
}

pub struct SerialConnector(pub String);
impl Connector for SerialConnector {
    fn describe(&self) -> String {
        self.0.clone()
    }
    fn kind(&self) -> LinkKind {
        LinkKind::Serial
    }
    fn connect(&self) -> io::Result<Box<dyn Link>> {
        Ok(Box::new(SerialLink::open(&self.0)?))
    }
}

pub struct TcpConnector(pub String);
impl Connector for TcpConnector {
    fn describe(&self) -> String {
        format!("tcp:{}", self.0)
    }
    fn kind(&self) -> LinkKind {
        LinkKind::Tcp
    }
    fn connect(&self) -> io::Result<Box<dyn Link>> {
        Ok(Box::new(TcpLink::connect(&self.0)?))
    }
}

fn timeout_to_zero(r: io::Result<usize>) -> io::Result<usize> {
    match r {
        Ok(0) => Err(io::Error::new(io::ErrorKind::UnexpectedEof, "link closed")),
        Ok(n) => Ok(n),
        Err(e)
            if matches!(
                e.kind(),
                io::ErrorKind::TimedOut | io::ErrorKind::WouldBlock
            ) =>
        {
            Ok(0)
        }
        Err(e) => Err(e),
    }
}

pub struct SerialLink {
    name: String,
    port: Box<dyn serialport::SerialPort>,
}

/// Clear HUPCL on an open terminal so closing it does not drop DTR (which
/// resets many ESP32 boards).
pub fn clear_hupcl_fd(fd: std::os::fd::RawFd) -> io::Result<()> {
    use nix::sys::termios::{tcgetattr, tcsetattr, ControlFlags, SetArg};
    let mut t = tcgetattr(fd).map_err(io::Error::from)?;
    t.control_flags.remove(ControlFlags::HUPCL);
    tcsetattr(fd, SetArg::TCSANOW, &t).map_err(io::Error::from)
}

/// Clear HUPCL on a device by path, as the reference Meshtastic client does
/// before it opens the port (it opens the file, clears the flag, closes it).
pub fn clear_hupcl_path(path: &str) -> io::Result<()> {
    use std::os::fd::AsRawFd;
    use std::os::unix::fs::OpenOptionsExt;
    let f = std::fs::OpenOptions::new()
        .read(true)
        .custom_flags(nix::libc::O_NOCTTY | nix::libc::O_NONBLOCK)
        .open(path)?;
    clear_hupcl_fd(f.as_raw_fd())
}

impl SerialLink {
    /// Open a serial port at 115200 baud (both protocols' default). HUPCL is
    /// cleared before the open (as the reference client does) and again on
    /// the open port. The open itself can still pulse DTR on some adapters;
    /// that is the kernel's behaviour and the reference client has it too.
    pub fn open(path: &str) -> io::Result<Self> {
        use std::os::fd::AsRawFd;
        clear_hupcl_path(path)?;
        let port = serialport::new(path, 115_200)
            .timeout(Duration::from_millis(200))
            .open_native()
            .map_err(|e| io::Error::other(e.to_string()))?;
        clear_hupcl_fd(port.as_raw_fd())?;
        Ok(SerialLink {
            name: path.to_string(),
            port: Box::new(port),
        })
    }
}

impl Link for SerialLink {
    fn name(&self) -> String {
        self.name.clone()
    }
    fn kind(&self) -> LinkKind {
        LinkKind::Serial
    }
    fn read(&mut self, buf: &mut [u8]) -> io::Result<usize> {
        match self.port.read(buf) {
            Ok(0) => Ok(0),
            other => timeout_to_zero(other),
        }
    }
    fn write_all(&mut self, data: &[u8]) -> io::Result<()> {
        self.port.write_all(data)?;
        self.port.flush()
    }
}

pub struct TcpLink {
    name: String,
    s: TcpStream,
}

impl TcpLink {
    pub fn connect(addr: &str) -> io::Result<Self> {
        let a = addr
            .to_socket_addrs()?
            .next()
            .ok_or_else(|| io::Error::new(io::ErrorKind::InvalidInput, "no address"))?;
        let s = TcpStream::connect_timeout(&a, Duration::from_secs(5))?;
        s.set_read_timeout(Some(Duration::from_millis(200)))?;
        s.set_nodelay(true)?;
        Ok(TcpLink {
            name: format!("tcp:{addr}"),
            s,
        })
    }
}

impl Link for TcpLink {
    fn name(&self) -> String {
        self.name.clone()
    }
    fn kind(&self) -> LinkKind {
        LinkKind::Tcp
    }
    fn read(&mut self, buf: &mut [u8]) -> io::Result<usize> {
        timeout_to_zero(self.s.read(buf))
    }
    fn write_all(&mut self, data: &[u8]) -> io::Result<()> {
        self.s.write_all(data)
    }
}

/// A scripted in-memory radio for tests. `script` holds the chunks the
/// "radio" will deliver, one per `read`; everything written is captured.
#[derive(Clone, Default)]
pub struct FakeLink {
    pub name: String,
    pub inbox: Arc<Mutex<VecDeque<Result<Vec<u8>, io::ErrorKind>>>>,
    pub written: Arc<Mutex<Vec<Vec<u8>>>>,
    /// Chunks released when the first write arrives (a radio that answers a
    /// handshake rather than speaking unprompted).
    pub on_write: Arc<Mutex<VecDeque<Vec<u8>>>>,
}

impl FakeLink {
    pub fn new(name: &str) -> Self {
        FakeLink {
            name: name.into(),
            ..Default::default()
        }
    }
    pub fn push(&self, chunk: &[u8]) {
        self.inbox.lock().unwrap().push_back(Ok(chunk.to_vec()));
    }
    pub fn push_error(&self, k: io::ErrorKind) {
        self.inbox.lock().unwrap().push_back(Err(k));
    }
    pub fn reply_on_write(&self, chunk: &[u8]) {
        self.on_write.lock().unwrap().push_back(chunk.to_vec());
    }
    pub fn written_bytes(&self) -> Vec<u8> {
        self.written
            .lock()
            .unwrap()
            .iter()
            .flatten()
            .copied()
            .collect()
    }
}

impl Link for FakeLink {
    fn name(&self) -> String {
        self.name.clone()
    }
    fn kind(&self) -> LinkKind {
        LinkKind::Serial
    }
    fn read(&mut self, buf: &mut [u8]) -> io::Result<usize> {
        let next = self.inbox.lock().unwrap().pop_front();
        match next {
            None => {
                std::thread::sleep(Duration::from_millis(2));
                Ok(0)
            }
            Some(Err(k)) => Err(io::Error::new(k, "fake link error")),
            Some(Ok(mut c)) => {
                let n = c.len().min(buf.len());
                buf[..n].copy_from_slice(&c[..n]);
                if n < c.len() {
                    let rest = c.split_off(n);
                    self.inbox.lock().unwrap().push_front(Ok(rest));
                }
                Ok(n)
            }
        }
    }
    fn write_all(&mut self, data: &[u8]) -> io::Result<()> {
        self.written.lock().unwrap().push(data.to_vec());
        let mut q = self.on_write.lock().unwrap();
        if let Some(c) = q.pop_front() {
            self.inbox.lock().unwrap().push_back(Ok(c));
        }
        Ok(())
    }
}
