// SPDX-License-Identifier: AGPL-3.0-or-later
//! Test tool for the end to end tests: a fullscreen Wayland window that
//! writes every pointer and keyboard event it receives to a file, one JSON
//! object per line, with the receive time in microseconds since the epoch.
//!
//!   probe <log-file>

use serde_json::json;
use std::fs::File;
use std::io::Write;
use std::os::fd::AsFd;
use std::time::{SystemTime, UNIX_EPOCH};
use wayland_client::globals::{registry_queue_init, GlobalListContents};
use wayland_client::protocol::{
    wl_buffer, wl_compositor, wl_keyboard, wl_pointer, wl_registry, wl_seat, wl_shm, wl_shm_pool,
    wl_surface,
};
use wayland_client::{delegate_noop, Connection, Dispatch, QueueHandle, WEnum};
use wayland_protocols::xdg::shell::client::{xdg_surface, xdg_toplevel, xdg_wm_base};

struct App {
    log: File,
    configured: bool,
    size: (i32, i32),
    pointer: bool,
    keyboard: bool,
}

impl App {
    fn emit(&mut self, v: serde_json::Value) {
        let us = SystemTime::now()
            .duration_since(UNIX_EPOCH)
            .map(|d| d.as_micros())
            .unwrap_or(0) as u64;
        let mut v = v;
        v["us"] = json!(us);
        let _ = writeln!(self.log, "{v}");
        let _ = self.log.flush();
    }
}

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
delegate_noop!(App: wl_compositor::WlCompositor);
delegate_noop!(App: ignore wl_shm::WlShm);
delegate_noop!(App: wl_shm_pool::WlShmPool);
delegate_noop!(App: ignore wl_buffer::WlBuffer);
delegate_noop!(App: ignore wl_surface::WlSurface);

impl Dispatch<xdg_wm_base::XdgWmBase, ()> for App {
    fn event(
        _: &mut Self,
        base: &xdg_wm_base::XdgWmBase,
        e: xdg_wm_base::Event,
        _: &(),
        _: &Connection,
        _: &QueueHandle<Self>,
    ) {
        if let xdg_wm_base::Event::Ping { serial } = e {
            base.pong(serial);
        }
    }
}

impl Dispatch<xdg_surface::XdgSurface, ()> for App {
    fn event(
        s: &mut Self,
        surf: &xdg_surface::XdgSurface,
        e: xdg_surface::Event,
        _: &(),
        _: &Connection,
        _: &QueueHandle<Self>,
    ) {
        if let xdg_surface::Event::Configure { serial } = e {
            surf.ack_configure(serial);
            s.configured = true;
        }
    }
}

impl Dispatch<xdg_toplevel::XdgToplevel, ()> for App {
    fn event(
        s: &mut Self,
        _: &xdg_toplevel::XdgToplevel,
        e: xdg_toplevel::Event,
        _: &(),
        _: &Connection,
        _: &QueueHandle<Self>,
    ) {
        if let xdg_toplevel::Event::Configure { width, height, .. } = e {
            if width > 0 && height > 0 {
                s.size = (width, height);
            }
        }
    }
}

impl Dispatch<wl_seat::WlSeat, ()> for App {
    fn event(
        s: &mut Self,
        seat: &wl_seat::WlSeat,
        e: wl_seat::Event,
        _: &(),
        _: &Connection,
        qh: &QueueHandle<Self>,
    ) {
        if let wl_seat::Event::Capabilities {
            capabilities: WEnum::Value(c),
        } = e
        {
            if c.contains(wl_seat::Capability::Pointer) && !s.pointer {
                s.pointer = true;
                seat.get_pointer(qh, ());
            }
            if c.contains(wl_seat::Capability::Keyboard) && !s.keyboard {
                s.keyboard = true;
                seat.get_keyboard(qh, ());
            }
        }
    }
}

impl Dispatch<wl_pointer::WlPointer, ()> for App {
    fn event(
        s: &mut Self,
        _: &wl_pointer::WlPointer,
        e: wl_pointer::Event,
        _: &(),
        _: &Connection,
        _: &QueueHandle<Self>,
    ) {
        match e {
            wl_pointer::Event::Enter {
                surface_x,
                surface_y,
                ..
            } => s.emit(json!({"e": "enter", "x": surface_x, "y": surface_y})),
            wl_pointer::Event::Motion {
                surface_x,
                surface_y,
                ..
            } => s.emit(json!({"e": "motion", "x": surface_x, "y": surface_y})),
            wl_pointer::Event::Button { button, state, .. } => {
                let down = matches!(state, WEnum::Value(wl_pointer::ButtonState::Pressed));
                s.emit(json!({"e": "button", "b": button, "down": down}))
            }
            wl_pointer::Event::Axis { axis, value, .. } => {
                let vertical = matches!(axis, WEnum::Value(wl_pointer::Axis::VerticalScroll));
                s.emit(json!({"e": "axis", "v": vertical, "value": value}))
            }
            wl_pointer::Event::AxisStop { .. } => s.emit(json!({"e": "axis_stop"})),
            _ => {}
        }
    }
}

impl Dispatch<wl_keyboard::WlKeyboard, ()> for App {
    fn event(
        s: &mut Self,
        _: &wl_keyboard::WlKeyboard,
        e: wl_keyboard::Event,
        _: &(),
        _: &Connection,
        _: &QueueHandle<Self>,
    ) {
        match e {
            wl_keyboard::Event::Keymap { size, .. } => s.emit(json!({"e": "keymap", "size": size})),
            wl_keyboard::Event::Enter { .. } => s.emit(json!({"e": "kbd_enter"})),
            wl_keyboard::Event::Key { key, state, .. } => {
                let down = matches!(state, WEnum::Value(wl_keyboard::KeyState::Pressed));
                s.emit(json!({"e": "key", "k": key, "down": down}))
            }
            wl_keyboard::Event::Modifiers { mods_depressed, .. } => {
                s.emit(json!({"e": "mods", "depressed": mods_depressed}))
            }
            _ => {}
        }
    }
}

fn main() {
    let path = std::env::args().nth(1).expect("probe <log-file>");
    let conn = Connection::connect_to_env().expect("wayland connection");
    let (globals, mut queue) = registry_queue_init::<App>(&conn).expect("registry");
    let qh = queue.handle();
    let mut app = App {
        log: File::create(&path).expect("log file"),
        configured: false,
        size: (1280, 720),
        pointer: false,
        keyboard: false,
    };
    let comp: wl_compositor::WlCompositor = globals.bind(&qh, 1..=4, ()).expect("compositor");
    let shm: wl_shm::WlShm = globals.bind(&qh, 1..=1, ()).expect("shm");
    let wm: xdg_wm_base::XdgWmBase = globals.bind(&qh, 1..=1, ()).expect("xdg_wm_base");
    let _seat: wl_seat::WlSeat = globals.bind(&qh, 1..=5, ()).expect("seat");
    let surface = comp.create_surface(&qh, ());
    let xs = wm.get_xdg_surface(&surface, &qh, ());
    let top = xs.get_toplevel(&qh, ());
    top.set_app_id("taos-probe".into());
    top.set_title("taos-probe".into());
    top.set_fullscreen(None);
    surface.commit();
    while !app.configured {
        queue.blocking_dispatch(&mut app).expect("dispatch");
    }
    let (w, h) = app.size;
    let stride = w * 4;
    let size = (stride * h) as u64;
    let file = tempfile_in_runtime();
    file.set_len(size).expect("size");
    let pool = shm.create_pool(file.as_fd(), size as i32, &qh, ());
    let buffer = pool.create_buffer(0, w, h, stride, wl_shm::Format::Xrgb8888, &qh, ());
    surface.attach(Some(&buffer), 0, 0);
    surface.commit();
    queue.roundtrip(&mut app).expect("roundtrip");
    app.emit(json!({"e": "ready", "w": w, "h": h}));
    loop {
        if queue.blocking_dispatch(&mut app).is_err() {
            app.emit(json!({"e": "closed"}));
            return;
        }
    }
}

fn tempfile_in_runtime() -> File {
    let dir = std::env::var_os("XDG_RUNTIME_DIR")
        .map(std::path::PathBuf::from)
        .unwrap_or_else(std::env::temp_dir);
    let p = dir.join(format!(".taos-probe-shm-{}", std::process::id()));
    let f = std::fs::OpenOptions::new()
        .read(true)
        .write(true)
        .create_new(true)
        .open(&p)
        .expect("shm file");
    let _ = std::fs::remove_file(&p);
    f
}
