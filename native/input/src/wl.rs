// SPDX-License-Identifier: AGPL-3.0-or-later
//! The real compositor sink: wlroots virtual pointer and virtual keyboard.

use crate::inject::Sink;
use std::fs::{self, OpenOptions};
use std::io::Write;
use std::os::fd::AsFd;
use std::os::unix::fs::OpenOptionsExt;
use std::path::PathBuf;
use std::time::Instant;
use wayland_client::globals::{registry_queue_init, GlobalListContents};
use wayland_client::protocol::{wl_pointer, wl_registry, wl_seat};
use wayland_client::{delegate_noop, Connection, Dispatch, EventQueue, QueueHandle};
use wayland_protocols_misc::zwp_virtual_keyboard_v1::client::{
    zwp_virtual_keyboard_manager_v1::ZwpVirtualKeyboardManagerV1,
    zwp_virtual_keyboard_v1::ZwpVirtualKeyboardV1,
};
use wayland_protocols_wlr::virtual_pointer::v1::client::{
    zwlr_virtual_pointer_manager_v1::ZwlrVirtualPointerManagerV1,
    zwlr_virtual_pointer_v1::ZwlrVirtualPointerV1,
};

struct App;

impl Dispatch<wl_registry::WlRegistry, GlobalListContents> for App {
    fn event(
        _: &mut Self,
        _: &wl_registry::WlRegistry,
        _: wl_registry::Event,
        _: &GlobalListContents,
        _: &Connection,
        _: &QueueHandle<Self>,
    ) {
    }
}
delegate_noop!(App: ignore wl_seat::WlSeat);
delegate_noop!(App: ZwlrVirtualPointerManagerV1);
delegate_noop!(App: ZwlrVirtualPointerV1);
delegate_noop!(App: ZwpVirtualKeyboardManagerV1);
delegate_noop!(App: ZwpVirtualKeyboardV1);

pub struct WaylandSink {
    conn: Connection,
    queue: EventQueue<App>,
    app: App,
    pointer: ZwlrVirtualPointerV1,
    keyboard: ZwpVirtualKeyboardV1,
    t0: Instant,
    runtime_dir: PathBuf,
}

impl WaylandSink {
    /// Connect to the compositor named by WAYLAND_DISPLAY and create one
    /// virtual pointer and one virtual keyboard on its first seat.
    pub fn connect() -> Result<WaylandSink, String> {
        let conn = Connection::connect_to_env()
            .map_err(|e| format!("cannot reach the compositor: {e}"))?;
        let (globals, mut queue) =
            registry_queue_init::<App>(&conn).map_err(|e| format!("registry: {e}"))?;
        let qh = queue.handle();
        let seat: wl_seat::WlSeat = globals
            .bind(&qh, 1..=1, ())
            .map_err(|e| format!("no seat: {e}"))?;
        let pm: ZwlrVirtualPointerManagerV1 = globals
            .bind(&qh, 1..=2, ())
            .map_err(|e| format!("compositor lacks wlr virtual pointer: {e}"))?;
        let km: ZwpVirtualKeyboardManagerV1 = globals
            .bind(&qh, 1..=1, ())
            .map_err(|e| format!("compositor lacks virtual keyboard: {e}"))?;
        let pointer = pm.create_virtual_pointer(Some(&seat), &qh, ());
        let keyboard = km.create_virtual_keyboard(&seat, &qh, ());
        let mut app = App;
        queue
            .roundtrip(&mut app)
            .map_err(|e| format!("compositor did not answer: {e}"))?;
        let runtime_dir = std::env::var_os("XDG_RUNTIME_DIR")
            .map(PathBuf::from)
            .unwrap_or_else(std::env::temp_dir);
        Ok(WaylandSink {
            conn,
            queue,
            app,
            pointer,
            keyboard,
            t0: Instant::now(),
            runtime_dir,
        })
    }

    fn now(&self) -> u32 {
        self.t0.elapsed().as_millis() as u32
    }
}

impl Sink for WaylandSink {
    fn motion(&mut self, dx: f64, dy: f64) {
        self.pointer.motion(self.now(), dx, dy);
        self.pointer.frame();
    }

    fn motion_abs(&mut self, x: f64, y: f64) {
        const EXT: u32 = 65535;
        let px = (x.clamp(0.0, 1.0) * EXT as f64) as u32;
        let py = (y.clamp(0.0, 1.0) * EXT as f64) as u32;
        self.pointer.motion_absolute(self.now(), px, py, EXT, EXT);
        self.pointer.frame();
    }

    fn button(&mut self, code: u32, down: bool) {
        let state = if down {
            wl_pointer::ButtonState::Pressed
        } else {
            wl_pointer::ButtonState::Released
        };
        self.pointer.button(self.now(), code, state);
        self.pointer.frame();
    }

    fn axis(&mut self, dx: f64, dy: f64) {
        let t = self.now();
        self.pointer.axis_source(wl_pointer::AxisSource::Finger);
        if dx != 0.0 {
            self.pointer.axis(t, wl_pointer::Axis::HorizontalScroll, dx);
        }
        if dy != 0.0 {
            self.pointer.axis(t, wl_pointer::Axis::VerticalScroll, dy);
        }
        self.pointer.frame();
    }

    fn keymap(&mut self, text: &str) -> Result<(), String> {
        let mut name = [0u8; 6];
        crate::auth::random_bytes(&mut name).map_err(|e| e.to_string())?;
        let path = self
            .runtime_dir
            .join(format!(".taos-input-keymap-{}", crate::auth::hex(&name)));
        let mut f = OpenOptions::new()
            .read(true)
            .write(true)
            .create_new(true)
            .mode(0o600)
            .open(&path)
            .map_err(|e| format!("keymap file: {e}"))?;
        // The compositor holds its own descriptor once it maps the file; the
        // name is not needed after this.
        let _ = fs::remove_file(&path);
        let mut bytes = text.as_bytes().to_vec();
        bytes.push(0);
        f.write_all(&bytes)
            .map_err(|e| format!("keymap write: {e}"))?;
        // wl_keyboard.keymap_format.xkb_v1 == 1
        self.keyboard.keymap(1, f.as_fd(), bytes.len() as u32);
        Ok(())
    }

    fn key(&mut self, code: u32, down: bool) {
        self.keyboard
            .key(self.now(), code, if down { 1 } else { 0 });
    }

    fn modifiers(&mut self, mask: u32) {
        self.keyboard.modifiers(mask, 0, 0, 0);
    }

    fn flush(&mut self) -> Result<(), String> {
        self.conn
            .flush()
            .map_err(|e| format!("compositor connection lost: {e}"))
    }

    fn ping(&mut self) -> Result<(), String> {
        self.queue
            .roundtrip(&mut self.app)
            .map(|_| ())
            .map_err(|e| format!("compositor connection lost: {e}"))
    }
}
