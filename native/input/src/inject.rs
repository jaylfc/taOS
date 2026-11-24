// SPDX-License-Identifier: AGPL-3.0-or-later
//! Turns high level commands into pointer and keyboard requests.
//!
//! One thread owns the compositor connection. Everything else sends
//! `Command`s on a channel, so the order is exactly the order of arrival;
//! adjacent motion and scroll commands are merged (coalesced) so a burst of
//! touch events costs one request, but a button or key always keeps its place.
//! If the compositor goes away the connection is dropped, held buttons and
//! keys are forgotten (the new compositor has none), commands are dropped
//! (never queued for later: a click that arrives late is worse than lost) and
//! the engine reconnects on its own.

use crate::gesture::Btn;
use crate::keymap::{Keymap, SHIFT};
use std::collections::BTreeSet;
use std::panic::{catch_unwind, AssertUnwindSafe};
use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::mpsc::{self, RecvTimeoutError, SyncSender};
use std::sync::Arc;
use std::thread;
use std::time::{Duration, Instant};

pub trait Sink: Send {
    fn motion(&mut self, dx: f64, dy: f64);
    fn motion_abs(&mut self, x: f64, y: f64);
    fn button(&mut self, code: u32, down: bool);
    fn axis(&mut self, dx: f64, dy: f64);
    fn keymap(&mut self, text: &str) -> Result<(), String>;
    fn key(&mut self, code: u32, down: bool);
    fn modifiers(&mut self, mask: u32);
    fn flush(&mut self) -> Result<(), String>;
    /// A round trip to the compositor: fails when the connection is gone.
    fn ping(&mut self) -> Result<(), String>;
}

pub type Connector = Box<dyn FnMut() -> Result<Box<dyn Sink>, String> + Send>;

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum KeyAction {
    Tap,
    Down,
    Up,
}

#[derive(Clone, Debug, PartialEq)]
pub enum Command {
    Motion {
        dx: f64,
        dy: f64,
    },
    Abs {
        x: f64,
        y: f64,
    },
    Button {
        btn: Btn,
        down: bool,
    },
    Scroll {
        dx: f64,
        dy: f64,
    },
    Text(String),
    Key {
        key: String,
        mods: u32,
        action: KeyAction,
    },
    SetMods(u32),
    ReleaseAll,
}

const BTN_LEFT: u32 = 0x110;
const BTN_RIGHT: u32 = 0x111;
const BTN_MIDDLE: u32 = 0x112;
const MODIFIER_KEYS: [u32; 5] = [29, 42, 56, 97, 100];

fn btn_code(b: Btn) -> u32 {
    match b {
        Btn::Left => BTN_LEFT,
        Btn::Right => BTN_RIGHT,
        Btn::Middle => BTN_MIDDLE,
    }
}

/// Merge neighbours that mean the same thing as one.
pub fn coalesce(cmds: Vec<Command>) -> Vec<Command> {
    let mut out: Vec<Command> = Vec::with_capacity(cmds.len());
    for c in cmds {
        match (out.last_mut(), &c) {
            (Some(Command::Motion { dx, dy }), Command::Motion { dx: a, dy: b }) => {
                *dx += a;
                *dy += b;
            }
            (Some(Command::Scroll { dx, dy }), Command::Scroll { dx: a, dy: b }) => {
                *dx += a;
                *dy += b;
            }
            (Some(Command::Abs { x, y }), Command::Abs { x: a, y: b }) => {
                *x = *a;
                *y = *b;
            }
            _ => out.push(c),
        }
    }
    out
}

/// All the compositor-facing state, without the thread, so it can be tested
/// against a recording sink.
pub struct Core {
    connector: Connector,
    sink: Option<Box<dyn Sink>>,
    keymap: Keymap,
    uploaded: u64,
    /// The modifiers the controller wants held (the sticky toggles).
    mods: u32,
    sent_mods: u32,
    buttons: BTreeSet<u32>,
    keys: BTreeSet<u32>,
    last_try: Option<Instant>,
}

impl Core {
    pub fn new(connector: Connector) -> Core {
        Core {
            connector,
            sink: None,
            keymap: Keymap::new(),
            uploaded: 0,
            mods: 0,
            sent_mods: 0,
            buttons: BTreeSet::new(),
            keys: BTreeSet::new(),
            last_try: None,
        }
    }

    pub fn is_up(&self) -> bool {
        self.sink.is_some()
    }

    /// Try to (re)connect, at most every 200 ms.
    pub fn ensure_connected(&mut self) -> bool {
        if self.sink.is_some() {
            return true;
        }
        if let Some(t) = self.last_try {
            if t.elapsed() < Duration::from_millis(200) {
                return false;
            }
        }
        self.last_try = Some(Instant::now());
        match (self.connector)() {
            Ok(s) => {
                self.sink = Some(s);
                self.uploaded = 0;
                self.sent_mods = 0;
                self.buttons.clear();
                self.keys.clear();
                true
            }
            Err(_) => false,
        }
    }

    /// Drop the connection and everything tied to it (also used when a
    /// library panics, for example on a closed stderr).
    pub fn lost(&mut self) {
        self.sink = None;
        self.uploaded = 0;
        self.sent_mods = 0;
        self.buttons.clear();
        self.keys.clear();
    }

    /// Periodic health check while idle.
    pub fn heartbeat(&mut self) {
        if !self.ensure_connected() {
            return;
        }
        if let Some(s) = self.sink.as_mut() {
            if s.ping().is_err() {
                self.lost();
            }
        }
    }

    pub fn apply_all(&mut self, cmds: Vec<Command>) {
        if !self.ensure_connected() {
            return; // dropped, on purpose
        }
        for c in coalesce(cmds) {
            if self.apply(c).is_err() {
                self.lost();
                return;
            }
        }
        if let Some(s) = self.sink.as_mut() {
            if s.flush().is_err() {
                self.lost();
            }
        }
    }

    fn sink(&mut self) -> Result<&mut Box<dyn Sink>, String> {
        self.sink
            .as_mut()
            .ok_or_else(|| "compositor unavailable".to_string())
    }

    fn ensure_keymap(&mut self) -> Result<(), String> {
        if self.uploaded != self.keymap.version() {
            let text = self.keymap.text();
            let v = self.keymap.version();
            self.sink()?.keymap(&text)?;
            self.uploaded = v;
            // A new keymap resets the compositor's keyboard state.
            self.sent_mods = 0;
        }
        Ok(())
    }

    fn ensure_mods(&mut self, mask: u32) -> Result<(), String> {
        self.ensure_keymap()?;
        if self.sent_mods != mask {
            self.sink()?.modifiers(mask);
            self.sent_mods = mask;
        }
        Ok(())
    }

    fn strike(&mut self, code: u32, mask: u32, down: bool, up: bool) -> Result<(), String> {
        self.ensure_mods(mask)?;
        if down {
            self.sink()?.key(code, true);
            self.keys.insert(code);
        }
        if up {
            self.sink()?.key(code, false);
            self.keys.remove(&code);
        }
        if MODIFIER_KEYS.contains(&code) || code == 125 {
            // The compositor recomputes the modifier state from the keys that
            // are down when a modifier key moves; put ours back.
            let m = self.sent_mods;
            self.sink()?.modifiers(m);
        }
        Ok(())
    }

    pub fn apply(&mut self, cmd: Command) -> Result<(), String> {
        match cmd {
            Command::Motion { dx, dy } => self.sink()?.motion(dx, dy),
            Command::Abs { x, y } => self.sink()?.motion_abs(x, y),
            Command::Button { btn, down } => {
                let code = btn_code(btn);
                if down {
                    self.buttons.insert(code);
                } else {
                    self.buttons.remove(&code);
                }
                self.sink()?.button(code, down);
            }
            Command::Scroll { dx, dy } => self.sink()?.axis(dx, dy),
            Command::Text(s) => {
                for ch in s.chars() {
                    // A character that cannot be typed is skipped, never
                    // replaced by another one.
                    let Ok(k) = self.keymap.char_strike(ch) else {
                        continue;
                    };
                    let mask = (self.mods & !SHIFT) | if k.shift { SHIFT } else { 0 };
                    self.ensure_keymap()?;
                    self.strike(k.code, mask, true, true)?;
                }
                let m = self.mods;
                self.ensure_mods(m)?;
            }
            Command::Key { key, mods, action } => {
                let Ok(k) = self.keymap.key_strike(&key) else {
                    return Ok(());
                };
                let mask = self.mods | mods | if k.shift { SHIFT } else { 0 };
                match action {
                    KeyAction::Tap => self.strike(k.code, mask, true, true)?,
                    KeyAction::Down => self.strike(k.code, mask, true, false)?,
                    KeyAction::Up => self.strike(k.code, mask, false, true)?,
                }
                if action != KeyAction::Down {
                    let m = self.mods;
                    self.ensure_mods(m)?;
                }
            }
            Command::SetMods(m) => {
                self.mods = m;
                self.ensure_mods(m)?;
            }
            Command::ReleaseAll => {
                if self.sink.is_some() {
                    let buttons: Vec<u32> = self.buttons.iter().copied().collect();
                    for b in buttons {
                        self.sink()?.button(b, false);
                    }
                    let keys: Vec<u32> = self.keys.iter().copied().collect();
                    for k in keys {
                        self.sink()?.key(k, false);
                    }
                    self.ensure_keymap()?;
                    self.sink()?.modifiers(0);
                    self.sent_mods = 0;
                }
                self.buttons.clear();
                self.keys.clear();
                self.mods = 0;
            }
        }
        Ok(())
    }
}

/// The handle the rest of the engine uses.
#[derive(Clone)]
pub struct Injector {
    tx: SyncSender<Command>,
    up: Arc<AtomicBool>,
}

impl Injector {
    /// Start the injector thread. `connector` is called whenever there is no
    /// connection (at start, and again after the compositor goes away).
    pub fn spawn(connector: Connector) -> Injector {
        let (tx, rx) = mpsc::sync_channel::<Command>(4096);
        let up = Arc::new(AtomicBool::new(false));
        let up2 = up.clone();
        thread::Builder::new()
            .name("taos-input-inject".into())
            .spawn(move || {
                let mut core = Core::new(connector);
                up2.store(core.ensure_connected(), Ordering::Relaxed);
                loop {
                    match rx.recv_timeout(Duration::from_millis(500)) {
                        Ok(first) => {
                            let mut batch = vec![first];
                            while batch.len() < 512 {
                                match rx.try_recv() {
                                    Ok(c) => batch.push(c),
                                    Err(_) => break,
                                }
                            }
                            if catch_unwind(AssertUnwindSafe(|| core.apply_all(batch))).is_err() {
                                core.lost();
                            }
                        }
                        Err(RecvTimeoutError::Timeout) => {
                            if catch_unwind(AssertUnwindSafe(|| core.heartbeat())).is_err() {
                                core.lost();
                            }
                        }
                        Err(RecvTimeoutError::Disconnected) => break,
                    }
                    up2.store(core.is_up(), Ordering::Relaxed);
                }
            })
            .expect("spawn injector thread");
        Injector { tx, up }
    }

    /// Queue a command. A full queue drops it (the compositor is not keeping
    /// up; stale input is worth nothing) except that releases always go in.
    pub fn send(&self, c: Command) {
        match c {
            Command::ReleaseAll => {
                let _ = self.tx.send(c);
            }
            c => {
                let _ = self.tx.try_send(c);
            }
        }
    }

    pub fn compositor_up(&self) -> bool {
        self.up.load(Ordering::Relaxed)
    }
}

#[cfg(test)]
pub mod tests {
    use super::*;
    use std::sync::Mutex;

    pub type Log = Arc<Mutex<Vec<String>>>;

    pub struct RecSink {
        pub log: Log,
        pub fail: Arc<AtomicBool>,
    }

    impl RecSink {
        fn rec(&self, s: String) {
            self.log.lock().unwrap().push(s);
        }
    }

    impl Sink for RecSink {
        fn motion(&mut self, dx: f64, dy: f64) {
            self.rec(format!("motion {dx} {dy}"));
        }
        fn motion_abs(&mut self, x: f64, y: f64) {
            self.rec(format!("abs {x} {y}"));
        }
        fn button(&mut self, code: u32, down: bool) {
            self.rec(format!(
                "button {code:#x} {}",
                if down { "down" } else { "up" }
            ));
        }
        fn axis(&mut self, dx: f64, dy: f64) {
            self.rec(format!("axis {dx} {dy}"));
        }
        fn keymap(&mut self, text: &str) -> Result<(), String> {
            self.rec(format!("keymap {}", text.len()));
            Ok(())
        }
        fn key(&mut self, code: u32, down: bool) {
            self.rec(format!("key {code} {}", if down { "down" } else { "up" }));
        }
        fn modifiers(&mut self, mask: u32) {
            self.rec(format!("mods {mask}"));
        }
        fn flush(&mut self) -> Result<(), String> {
            if self.fail.load(Ordering::Relaxed) {
                Err("gone".into())
            } else {
                Ok(())
            }
        }
        fn ping(&mut self) -> Result<(), String> {
            self.flush()
        }
    }

    pub fn rig() -> (Core, Log, Arc<AtomicBool>) {
        let log: Log = Arc::new(Mutex::new(Vec::new()));
        let fail = Arc::new(AtomicBool::new(false));
        let (l, f) = (log.clone(), fail.clone());
        let connector: Connector = Box::new(move || {
            if f.load(Ordering::Relaxed) {
                return Err("down".into());
            }
            l.lock().unwrap().push("connect".into());
            Ok(Box::new(RecSink {
                log: l.clone(),
                fail: f.clone(),
            }) as Box<dyn Sink>)
        });
        (Core::new(connector), log, fail)
    }

    fn lines(log: &Log) -> Vec<String> {
        std::mem::take(&mut *log.lock().unwrap())
    }

    #[test]
    fn coalescing_merges_neighbours_but_never_reorders() {
        let c = coalesce(vec![
            Command::Motion { dx: 1.0, dy: 2.0 },
            Command::Motion { dx: 3.0, dy: 4.0 },
            Command::Button {
                btn: Btn::Left,
                down: true,
            },
            Command::Motion { dx: 1.0, dy: 1.0 },
            Command::Scroll { dx: 0.0, dy: 5.0 },
            Command::Scroll { dx: 0.0, dy: 5.0 },
            Command::Abs { x: 0.1, y: 0.1 },
            Command::Abs { x: 0.5, y: 0.6 },
        ]);
        assert_eq!(
            c,
            vec![
                Command::Motion { dx: 4.0, dy: 6.0 },
                Command::Button {
                    btn: Btn::Left,
                    down: true
                },
                Command::Motion { dx: 1.0, dy: 1.0 },
                Command::Scroll { dx: 0.0, dy: 10.0 },
                Command::Abs { x: 0.5, y: 0.6 },
            ]
        );
    }

    #[test]
    fn plain_text_uses_the_base_layout_with_shift_only_where_needed() {
        let (mut core, log, _) = rig();
        core.apply_all(vec![Command::Text("aB".into())]);
        let l = lines(&log);
        assert_eq!(l[0], "connect");
        assert!(l[1].starts_with("keymap "));
        assert_eq!(
            l[2..],
            [
                "key 30 down",
                "key 30 up",
                "mods 1",
                "key 48 down",
                "key 48 up",
                "mods 0"
            ]
            .map(String::from)
        );
    }

    #[test]
    fn a_new_character_uploads_the_map_once_and_then_types() {
        let (mut core, log, _) = rig();
        core.apply_all(vec![Command::Text("é".into())]);
        let first = lines(&log);
        assert_eq!(first.iter().filter(|l| l.starts_with("keymap")).count(), 1);
        assert!(
            first.iter().any(|l| l == "key 200 down") && first.iter().any(|l| l == "key 200 up")
        );
        core.apply_all(vec![Command::Text("é".into())]);
        let second = lines(&log);
        assert_eq!(
            second.iter().filter(|l| l.starts_with("keymap")).count(),
            0,
            "known character: no upload"
        );
        core.apply_all(vec![Command::Text("😀".into())]);
        let third = lines(&log);
        assert_eq!(third.iter().filter(|l| l.starts_with("keymap")).count(), 1);
        assert!(third.iter().any(|l| l == "key 201 down"));
    }

    #[test]
    fn ctrl_c_is_the_c_key_with_the_control_mask() {
        let (mut core, log, _) = rig();
        core.apply_all(vec![Command::Key {
            key: "c".into(),
            mods: crate::keymap::CONTROL,
            action: KeyAction::Tap,
        }]);
        let l = lines(&log);
        let i = l
            .iter()
            .position(|x| x == "mods 4")
            .expect("control mask set");
        assert_eq!(l[i + 1], "key 46 down");
        assert_eq!(l[i + 2], "key 46 up");
        assert_eq!(l.last().unwrap(), "mods 0");
    }

    #[test]
    fn super_tap_presses_the_super_key_under_the_super_mask_and_restores() {
        let (mut core, log, _) = rig();
        core.apply_all(vec![Command::Key {
            key: "Super".into(),
            mods: 0,
            action: KeyAction::Tap,
        }]);
        let l = lines(&log);
        assert!(l.contains(&"key 125 down".to_string()) && l.contains(&"key 125 up".to_string()));
        assert_eq!(l.last().unwrap(), "mods 0");
    }

    #[test]
    fn sticky_modifiers_apply_to_text_but_shift_is_decided_per_character() {
        let (mut core, log, _) = rig();
        core.apply_all(vec![
            Command::SetMods(crate::keymap::CONTROL | SHIFT),
            Command::Text("c".into()),
        ]);
        let l = lines(&log);
        assert!(l.contains(&"mods 5".to_string()));
        assert!(
            l.contains(&"mods 4".to_string()),
            "shift dropped for a lower case letter"
        );
        assert!(l.contains(&"key 46 down".to_string()));
        assert_eq!(
            l.last().unwrap(),
            "mods 5",
            "sticky set restored after the text"
        );
    }

    #[test]
    fn release_all_lifts_buttons_keys_and_modifiers() {
        let (mut core, log, _) = rig();
        core.apply_all(vec![
            Command::Button {
                btn: Btn::Left,
                down: true,
            },
            Command::SetMods(crate::keymap::CONTROL),
            Command::Key {
                key: "Escape".into(),
                mods: 0,
                action: KeyAction::Down,
            },
        ]);
        lines(&log);
        core.apply_all(vec![Command::ReleaseAll]);
        let l = lines(&log);
        assert!(l.contains(&"button 0x110 up".to_string()));
        assert!(l.contains(&"key 1 up".to_string()));
        assert_eq!(l.last().unwrap(), "mods 0");
        core.apply_all(vec![Command::ReleaseAll]);
        let again = lines(&log);
        assert!(
            !again.iter().any(|l| l.starts_with("button")),
            "nothing held, nothing released twice"
        );
    }

    #[test]
    fn compositor_loss_drops_commands_and_forgets_held_state_then_recovers() {
        let (mut core, log, fail) = rig();
        core.apply_all(vec![Command::Button {
            btn: Btn::Left,
            down: true,
        }]);
        lines(&log);
        fail.store(true, Ordering::Relaxed); // flush now fails: the compositor died
        core.apply_all(vec![Command::Motion { dx: 1.0, dy: 1.0 }]);
        assert!(!core.is_up());
        // While it is down, commands are dropped, not queued.
        core.apply_all(vec![Command::Motion { dx: 9.0, dy: 9.0 }]);
        lines(&log);
        // It comes back.
        fail.store(false, Ordering::Relaxed);
        std::thread::sleep(Duration::from_millis(250));
        core.apply_all(vec![Command::Text("a".into())]);
        let l = lines(&log);
        assert_eq!(l[0], "connect");
        assert!(
            l.iter().any(|x| x.starts_with("keymap")),
            "keymap re-uploaded to the new compositor"
        );
        assert!(
            !l.iter().any(|x| x.starts_with("motion")),
            "no stale motion replayed"
        );
        // The old held button is not 'released' on the new compositor.
        core.apply_all(vec![Command::ReleaseAll]);
        assert!(!lines(&log).iter().any(|x| x.starts_with("button")));
    }

    #[test]
    fn untypable_characters_are_skipped_not_replaced() {
        let (mut core, log, _) = rig();
        core.apply_all(vec![Command::Text("a\u{7}b".into())]);
        let l = lines(&log);
        let downs: Vec<_> = l
            .iter()
            .filter(|x| x.ends_with(" down") && x.starts_with("key"))
            .collect();
        assert_eq!(downs, vec!["key 30 down", "key 48 down"]);
    }

    #[test]
    fn a_panic_inside_the_compositor_library_does_not_kill_the_injector() {
        struct Bomb;
        impl Sink for Bomb {
            fn motion(&mut self, _: f64, _: f64) {}
            fn motion_abs(&mut self, _: f64, _: f64) {}
            fn button(&mut self, _: u32, _: bool) {}
            fn axis(&mut self, _: f64, _: f64) {}
            fn keymap(&mut self, _: &str) -> Result<(), String> {
                Ok(())
            }
            fn key(&mut self, _: u32, _: bool) {}
            fn modifiers(&mut self, _: u32) {}
            fn flush(&mut self) -> Result<(), String> {
                Ok(())
            }
            fn ping(&mut self) -> Result<(), String> {
                panic!("stderr closed");
            }
        }
        let first = Arc::new(AtomicBool::new(true));
        let f = first.clone();
        let second: Log = Arc::new(Mutex::new(Vec::new()));
        let second2 = second.clone();
        let connector: Connector = Box::new(move || {
            if f.swap(false, Ordering::SeqCst) {
                Ok(Box::new(Bomb) as Box<dyn Sink>)
            } else {
                Ok(Box::new(RecSink {
                    log: second2.clone(),
                    fail: Arc::new(AtomicBool::new(false)),
                }) as Box<dyn Sink>)
            }
        });
        let inj = Injector::spawn(connector);
        // The first heartbeat panics; the thread must carry on and reconnect.
        std::thread::sleep(Duration::from_millis(1200)); // idle: the heartbeat pings the first sink and it panics
        for _ in 0..10 {
            std::thread::sleep(Duration::from_millis(100));
            inj.send(Command::Motion { dx: 1.0, dy: 0.0 });
        }
        assert!(
            second
                .lock()
                .unwrap()
                .iter()
                .any(|l| l.starts_with("motion")),
            "the injector thread died with the panic"
        );
    }
}
