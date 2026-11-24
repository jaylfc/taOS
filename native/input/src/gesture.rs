// SPDX-License-Identifier: AGPL-3.0-or-later
//! Touch gesture interpretation: raw finger positions in, pointer actions out.
//!
//! This is a pure state machine. It never reads a clock; the caller passes
//! the time (milliseconds) with every event and calls `tick` for the one
//! gesture that fires without an event (tap and hold). That keeps it testable
//! and the same for every face that sends touches.
//!
//! Rules (one sequence runs from the first finger down to the last finger up):
//! * one finger moves the pointer, with speed based acceleration; a short
//!   still touch is a left click; a still touch held past the hold time
//!   starts a drag (left button down) that ends when the finger lifts;
//! * two fingers: moving them scrolls (smooth, natural direction by default);
//!   a short still two finger touch is a right click;
//! * three fingers: a horizontal swipe switches workspace once per sequence;
//!   a short still three finger touch is a middle click;
//! * once more than one finger has touched, the pointer never moves again in
//!   that sequence, so lifting one finger of a pair does not jump the pointer.

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum Phase {
    Down,
    Move,
    Up,
    Cancel,
}

#[derive(Clone, Copy, Debug)]
pub struct Touch {
    pub phase: Phase,
    pub id: u32,
    pub x: f64,
    pub y: f64,
    /// Milliseconds on any monotonic clock.
    pub t: u64,
}

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum Btn {
    Left,
    Right,
    Middle,
}

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum Haptic {
    Tap,
    Drag,
    Switch,
}

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum Dir {
    Next,
    Prev,
}

#[derive(Clone, Copy, Debug, PartialEq)]
pub enum Out {
    Move { dx: f64, dy: f64 },
    Button(Btn, bool),
    Scroll { dx: f64, dy: f64 },
    ScrollStop,
    Workspace(Dir),
    Haptic(Haptic),
}

#[derive(Clone, Debug)]
pub struct Config {
    /// Pointer pixels per finger pixel at slow speed.
    pub sens: f64,
    pub tap_ms: u64,
    /// Finger travel (CSS px) still counted as "not moved".
    pub slop: f64,
    pub hold_ms: u64,
    pub swipe_px: f64,
    pub natural_scroll: bool,
    pub scroll_gain: f64,
}

impl Default for Config {
    fn default() -> Config {
        Config {
            sens: 2.0,
            tap_ms: 250,
            slop: 8.0,
            hold_ms: 450,
            swipe_px: 90.0,
            natural_scroll: true,
            scroll_gain: 1.0,
        }
    }
}

impl Config {
    /// Keep every number inside a range where the gestures still work.
    pub fn clamped(mut self) -> Config {
        let c = |v: f64, lo: f64, hi: f64| if v.is_finite() { v.clamp(lo, hi) } else { lo };
        self.sens = c(self.sens, 0.3, 6.0);
        self.scroll_gain = c(self.scroll_gain, 0.1, 8.0);
        self.slop = c(self.slop, 2.0, 30.0);
        self.swipe_px = c(self.swipe_px, 30.0, 400.0);
        self.tap_ms = self.tap_ms.clamp(80, 600);
        self.hold_ms = self.hold_ms.clamp(200, 1500);
        self
    }
}

/// Pointer gain for a finger speed in px per ms: 1x when slow, up to 3x fast.
pub fn gain(speed: f64) -> f64 {
    1.0 + 2.0 * ((speed - 0.2) / 2.8).clamp(0.0, 1.0)
}

#[derive(Clone, Copy, Debug)]
struct Finger {
    id: u32,
    x0: f64,
    y0: f64,
    x: f64,
    y: f64,
    t: u64,
}

pub struct Recognizer {
    cfg: Config,
    fingers: Vec<Finger>,
    seq_max: usize,
    start_t: u64,
    max_travel: f64,
    moved: bool,
    hold_fired: bool,
    dragging: bool,
    scrolled: bool,
    swiped: bool,
    swipe_acc: f64,
    centroid: (f64, f64),
}

impl Recognizer {
    pub fn new(cfg: Config) -> Recognizer {
        Recognizer {
            cfg: cfg.clamped(),
            fingers: Vec::new(),
            seq_max: 0,
            start_t: 0,
            max_travel: 0.0,
            moved: false,
            hold_fired: false,
            dragging: false,
            scrolled: false,
            swiped: false,
            swipe_acc: 0.0,
            centroid: (0.0, 0.0),
        }
    }

    pub fn set_config(&mut self, cfg: Config) {
        self.cfg = cfg.clamped();
    }

    pub fn config(&self) -> &Config {
        &self.cfg
    }

    fn centroid_now(&self) -> (f64, f64) {
        let n = self.fingers.len().max(1) as f64;
        (
            self.fingers.iter().map(|f| f.x).sum::<f64>() / n,
            self.fingers.iter().map(|f| f.y).sum::<f64>() / n,
        )
    }

    /// When the next `tick` could produce something, if anything is pending.
    pub fn next_deadline(&self) -> Option<u64> {
        if self.seq_max == 1 && self.fingers.len() == 1 && !self.moved && !self.hold_fired {
            Some(self.start_t + self.cfg.hold_ms)
        } else {
            None
        }
    }

    pub fn tick(&mut self, now: u64, out: &mut Vec<Out>) {
        if let Some(d) = self.next_deadline() {
            if now >= d {
                self.hold_fired = true;
                self.dragging = true;
                out.push(Out::Button(Btn::Left, true));
                out.push(Out::Haptic(Haptic::Drag));
            }
        }
    }

    /// Forget everything and release whatever is held (a dropped connection).
    pub fn reset(&mut self, out: &mut Vec<Out>) {
        self.end_sequence(out, false, 0);
        self.fingers.clear();
        self.seq_max = 0;
    }

    pub fn touch(&mut self, ev: Touch, out: &mut Vec<Out>) {
        match ev.phase {
            Phase::Down => self.down(ev),
            Phase::Move => self.moved_to(ev, out),
            Phase::Up => self.up(ev, out),
            Phase::Cancel => {
                self.fingers.clear();
                self.end_sequence(out, false, ev.t);
                self.seq_max = 0;
            }
        }
    }

    fn down(&mut self, ev: Touch) {
        if self.fingers.iter().any(|f| f.id == ev.id) {
            return;
        }
        if self.fingers.is_empty() {
            self.seq_max = 0;
            self.start_t = ev.t;
            self.max_travel = 0.0;
            self.moved = false;
            self.hold_fired = false;
            self.dragging = false;
            self.scrolled = false;
            self.swiped = false;
            self.swipe_acc = 0.0;
        }
        if self.fingers.len() >= 5 {
            return;
        }
        self.fingers.push(Finger {
            id: ev.id,
            x0: ev.x,
            y0: ev.y,
            x: ev.x,
            y: ev.y,
            t: ev.t,
        });
        self.seq_max = self.seq_max.max(self.fingers.len());
        self.centroid = self.centroid_now();
    }

    fn moved_to(&mut self, ev: Touch, out: &mut Vec<Out>) {
        let Some(i) = self.fingers.iter().position(|f| f.id == ev.id) else {
            return;
        };
        let prev = self.fingers[i];
        {
            let f = &mut self.fingers[i];
            f.x = ev.x;
            f.y = ev.y;
            f.t = ev.t;
        }
        let travel = (ev.x - prev.x0).hypot(ev.y - prev.y0);
        self.max_travel = self.max_travel.max(travel);
        let n = self.fingers.len();
        match self.seq_max {
            1 => {
                let (dx, dy) = (ev.x - prev.x, ev.y - prev.y);
                if !self.dragging && !self.moved {
                    if travel > self.cfg.slop {
                        self.moved = true;
                    } else {
                        return;
                    }
                }
                if dx != 0.0 || dy != 0.0 {
                    let dt = ev.t.saturating_sub(prev.t).clamp(1, 64) as f64;
                    let g = gain(dx.hypot(dy) / dt) * self.cfg.sens;
                    out.push(Out::Move {
                        dx: dx * g,
                        dy: dy * g,
                    });
                }
            }
            2 if n == 2 => {
                let c = self.centroid_now();
                let (dx, dy) = (c.0 - self.centroid.0, c.1 - self.centroid.1);
                self.centroid = c;
                if !self.scrolled && self.max_travel > self.cfg.slop {
                    self.scrolled = true;
                }
                if self.scrolled && (dx != 0.0 || dy != 0.0) {
                    let s = if self.cfg.natural_scroll { -1.0 } else { 1.0 } * self.cfg.scroll_gain;
                    out.push(Out::Scroll {
                        dx: dx * s,
                        dy: dy * s,
                    });
                }
            }
            k if k >= 3 && n == 3 => {
                let c = self.centroid_now();
                let dx = c.0 - self.centroid.0;
                self.centroid = c;
                if !self.swiped {
                    self.swipe_acc += dx;
                    if self.swipe_acc.abs() > self.cfg.swipe_px {
                        self.swiped = true;
                        out.push(Out::Workspace(if self.swipe_acc < 0.0 {
                            Dir::Next
                        } else {
                            Dir::Prev
                        }));
                        out.push(Out::Haptic(Haptic::Switch));
                    }
                }
            }
            _ => {}
        }
    }

    fn up(&mut self, ev: Touch, out: &mut Vec<Out>) {
        let Some(i) = self.fingers.iter().position(|f| f.id == ev.id) else {
            return;
        };
        self.fingers.remove(i);
        if self.fingers.is_empty() {
            self.end_sequence(out, true, ev.t);
            self.seq_max = 0;
        } else {
            self.centroid = self.centroid_now();
        }
    }

    fn end_sequence(&mut self, out: &mut Vec<Out>, allow_tap: bool, now: u64) {
        if self.scrolled {
            out.push(Out::ScrollStop);
            self.scrolled = false;
        }
        if self.dragging {
            out.push(Out::Button(Btn::Left, false));
            self.dragging = false;
            return;
        }
        if !allow_tap || self.seq_max == 0 {
            return;
        }
        let dur = now.saturating_sub(self.start_t);
        let s = self.cfg.slop;
        let click = match self.seq_max {
            1 if !self.moved
                && !self.hold_fired
                && dur <= self.cfg.tap_ms
                && self.max_travel <= s =>
            {
                Some(Btn::Left)
            }
            2 if dur <= self.cfg.tap_ms + 50 && self.max_travel <= s * 1.5 => Some(Btn::Right),
            3 if !self.swiped && dur <= self.cfg.tap_ms + 100 && self.max_travel <= s * 2.0 => {
                Some(Btn::Middle)
            }
            _ => None,
        };
        if let Some(b) = click {
            out.push(Out::Button(b, true));
            out.push(Out::Button(b, false));
            out.push(Out::Haptic(Haptic::Tap));
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    struct Rig {
        r: Recognizer,
        out: Vec<Out>,
    }

    impl Rig {
        fn new() -> Rig {
            Rig {
                r: Recognizer::new(Config::default()),
                out: Vec::new(),
            }
        }
        fn ev(&mut self, phase: Phase, id: u32, x: f64, y: f64, t: u64) {
            self.r.touch(Touch { phase, id, x, y, t }, &mut self.out);
        }
        fn down(&mut self, id: u32, x: f64, y: f64, t: u64) {
            self.ev(Phase::Down, id, x, y, t)
        }
        fn mv(&mut self, id: u32, x: f64, y: f64, t: u64) {
            self.ev(Phase::Move, id, x, y, t)
        }
        fn up(&mut self, id: u32, x: f64, y: f64, t: u64) {
            self.ev(Phase::Up, id, x, y, t)
        }
        fn take(&mut self) -> Vec<Out> {
            std::mem::take(&mut self.out)
        }
        fn buttons(&self) -> Vec<(Btn, bool)> {
            self.out
                .iter()
                .filter_map(|o| {
                    if let Out::Button(b, d) = o {
                        Some((*b, *d))
                    } else {
                        None
                    }
                })
                .collect()
        }
        fn moves(&self) -> Vec<(f64, f64)> {
            self.out
                .iter()
                .filter_map(|o| {
                    if let Out::Move { dx, dy } = o {
                        Some((*dx, *dy))
                    } else {
                        None
                    }
                })
                .collect()
        }
        fn scrolls(&self) -> Vec<(f64, f64)> {
            self.out
                .iter()
                .filter_map(|o| {
                    if let Out::Scroll { dx, dy } = o {
                        Some((*dx, *dy))
                    } else {
                        None
                    }
                })
                .collect()
        }
    }

    #[test]
    fn short_still_touch_is_a_left_click() {
        let mut g = Rig::new();
        g.down(1, 100.0, 100.0, 1000);
        g.mv(1, 102.0, 101.0, 1050);
        g.up(1, 102.0, 101.0, 1100);
        assert_eq!(g.buttons(), vec![(Btn::Left, true), (Btn::Left, false)]);
        assert!(
            g.moves().is_empty(),
            "movement inside the slop is swallowed"
        );
        assert!(g.out.contains(&Out::Haptic(Haptic::Tap)));
    }

    #[test]
    fn a_slow_touch_is_not_a_click() {
        let mut g = Rig::new();
        g.down(1, 0.0, 0.0, 0);
        g.up(1, 0.0, 0.0, 300);
        assert!(g.buttons().is_empty());
    }

    #[test]
    fn one_finger_moves_the_pointer_in_the_finger_direction() {
        let mut g = Rig::new();
        g.down(1, 100.0, 100.0, 0);
        g.mv(1, 130.0, 100.0, 100); // crosses the slop: moved, 30 px in 100 ms
        let m = g.moves();
        assert_eq!(m.len(), 1);
        assert!(
            m[0].0 > 30.0 && m[0].1 == 0.0,
            "scaled up by sensitivity: {:?}",
            m
        );
        g.mv(1, 130.0, 110.0, 200);
        let m = g.moves();
        assert!(m[1].1 > 0.0 && m[1].0 == 0.0);
        g.up(1, 130.0, 110.0, 300);
        assert!(g.buttons().is_empty(), "a move is never a click");
    }

    #[test]
    fn acceleration_grows_with_speed_and_is_bounded() {
        assert_eq!(gain(0.0), 1.0);
        assert_eq!(gain(0.2), 1.0);
        assert!(gain(1.0) > gain(0.5));
        assert_eq!(gain(3.0), 3.0);
        assert_eq!(gain(100.0), 3.0);
        let mut slow = Rig::new();
        slow.down(1, 0.0, 0.0, 0);
        slow.mv(1, 20.0, 0.0, 400);
        slow.mv(1, 30.0, 0.0, 800); // 10 px / 400 ms
        let mut fast = Rig::new();
        fast.down(1, 0.0, 0.0, 0);
        fast.mv(1, 20.0, 0.0, 40);
        fast.mv(1, 50.0, 0.0, 50); // 30 px / 10 ms
        let s = slow.moves()[1].0 / 10.0;
        let f = fast.moves()[1].0 / 30.0;
        assert!(
            f > s * 2.5,
            "fast flick must travel much further per finger px ({f} vs {s})"
        );
    }

    #[test]
    fn hold_starts_a_drag_that_ends_on_release() {
        let mut g = Rig::new();
        g.down(1, 50.0, 50.0, 0);
        assert_eq!(g.r.next_deadline(), Some(450));
        g.r.tick(449, &mut g.out);
        assert!(g.buttons().is_empty());
        g.r.tick(450, &mut g.out);
        assert_eq!(g.buttons(), vec![(Btn::Left, true)]);
        assert!(g.out.contains(&Out::Haptic(Haptic::Drag)));
        g.take();
        g.mv(1, 53.0, 50.0, 500); // inside the slop but dragging: moves at once
        assert_eq!(g.moves().len(), 1);
        g.mv(1, 90.0, 80.0, 600);
        g.up(1, 90.0, 80.0, 700);
        assert_eq!(g.buttons(), vec![(Btn::Left, false)]);
        assert_eq!(g.r.next_deadline(), None);
    }

    #[test]
    fn moving_before_the_hold_time_cancels_the_hold() {
        let mut g = Rig::new();
        g.down(1, 0.0, 0.0, 0);
        g.mv(1, 40.0, 0.0, 100);
        assert_eq!(g.r.next_deadline(), None);
        g.r.tick(10_000, &mut g.out);
        assert!(g.buttons().is_empty());
    }

    #[test]
    fn two_finger_tap_is_a_right_click() {
        let mut g = Rig::new();
        g.down(1, 100.0, 100.0, 0);
        g.down(2, 140.0, 100.0, 10);
        g.up(1, 100.0, 100.0, 90);
        g.up(2, 140.0, 100.0, 110);
        assert_eq!(g.buttons(), vec![(Btn::Right, true), (Btn::Right, false)]);
        assert!(g.moves().is_empty());
    }

    #[test]
    fn two_finger_drag_scrolls_naturally_and_ends_with_a_stop() {
        let mut g = Rig::new();
        g.down(1, 100.0, 200.0, 0);
        g.down(2, 140.0, 200.0, 5);
        g.mv(1, 100.0, 180.0, 50);
        g.mv(2, 140.0, 180.0, 50);
        g.mv(1, 100.0, 150.0, 100);
        g.mv(2, 140.0, 150.0, 100);
        let s = g.scrolls();
        assert!(!s.is_empty());
        let total: f64 = s.iter().map(|v| v.1).sum();
        assert!(
            total > 0.0,
            "fingers moving up scroll the page down (natural): {total}"
        );
        g.up(1, 100.0, 150.0, 150);
        g.up(2, 140.0, 150.0, 160);
        assert!(g.out.contains(&Out::ScrollStop));
        assert!(g.buttons().is_empty(), "a scroll is not a right click");
        assert!(g.moves().is_empty());
    }

    #[test]
    fn scroll_direction_follows_the_natural_setting() {
        let cfg = Config {
            natural_scroll: false,
            ..Config::default()
        };
        let mut g = Rig {
            r: Recognizer::new(cfg),
            out: vec![],
        };
        g.down(1, 0.0, 100.0, 0);
        g.down(2, 30.0, 100.0, 0);
        g.mv(1, 0.0, 60.0, 50);
        g.mv(2, 30.0, 60.0, 50);
        assert!(g.scrolls().iter().map(|v| v.1).sum::<f64>() < 0.0);
    }

    #[test]
    fn small_two_finger_jitter_does_not_scroll_until_past_the_slop() {
        let mut g = Rig::new();
        g.down(1, 0.0, 0.0, 0);
        g.down(2, 30.0, 0.0, 0);
        g.mv(1, 0.0, 3.0, 30);
        g.mv(2, 30.0, 3.0, 30);
        assert!(g.scrolls().is_empty());
    }

    #[test]
    fn lifting_one_of_two_fingers_does_not_move_the_pointer() {
        let mut g = Rig::new();
        g.down(1, 0.0, 0.0, 0);
        g.down(2, 30.0, 0.0, 0);
        g.mv(1, 0.0, 40.0, 50);
        g.mv(2, 30.0, 40.0, 50);
        g.up(1, 0.0, 40.0, 60);
        g.take();
        g.mv(2, 80.0, 90.0, 70);
        assert!(g.moves().is_empty());
        assert!(g.scrolls().is_empty());
    }

    #[test]
    fn three_finger_swipe_switches_workspace_once() {
        let mut g = Rig::new();
        for i in 0..3 {
            g.down(i, 100.0 + 30.0 * i as f64, 300.0, 0);
        }
        for step in 1..=6 {
            for i in 0..3 {
                g.mv(
                    i,
                    100.0 + 30.0 * i as f64 - 25.0 * step as f64,
                    300.0,
                    20 * step,
                );
            }
        }
        let ws: Vec<_> = g
            .out
            .iter()
            .filter(|o| matches!(o, Out::Workspace(_)))
            .collect();
        assert_eq!(
            ws,
            vec![&Out::Workspace(Dir::Next)],
            "swipe left = next, once"
        );
        for i in 0..3 {
            g.up(i, 0.0, 300.0, 400);
        }
        assert!(g.buttons().is_empty());
    }

    #[test]
    fn three_finger_swipe_right_goes_back() {
        let mut g = Rig::new();
        for i in 0..3 {
            g.down(i, 100.0 + 30.0 * i as f64, 300.0, 0);
        }
        for step in 1..=6 {
            for i in 0..3 {
                g.mv(
                    i,
                    100.0 + 30.0 * i as f64 + 25.0 * step as f64,
                    300.0,
                    20 * step,
                );
            }
        }
        assert!(g.out.contains(&Out::Workspace(Dir::Prev)));
    }

    #[test]
    fn three_finger_tap_is_a_middle_click() {
        let mut g = Rig::new();
        for i in 0..3 {
            g.down(i, 10.0 * i as f64, 0.0, 0);
        }
        for i in 0..3 {
            g.up(i, 0.0, 0.0, 100);
        }
        assert_eq!(g.buttons(), vec![(Btn::Middle, true), (Btn::Middle, false)]);
    }

    #[test]
    fn cancel_and_reset_release_a_drag_and_never_click() {
        let mut g = Rig::new();
        g.down(1, 0.0, 0.0, 0);
        g.r.tick(500, &mut g.out);
        g.take();
        g.ev(Phase::Cancel, 1, 0.0, 0.0, 520);
        assert_eq!(g.buttons(), vec![(Btn::Left, false)]);

        let mut g = Rig::new();
        g.down(1, 0.0, 0.0, 0);
        g.r.tick(500, &mut g.out);
        g.take();
        g.r.reset(&mut g.out);
        assert_eq!(g.buttons(), vec![(Btn::Left, false)]);
        g.r.reset(&mut g.out);
        assert_eq!(g.buttons().len(), 1, "a second reset releases nothing more");
    }

    #[test]
    fn a_second_finger_during_a_drag_does_not_break_it() {
        let mut g = Rig::new();
        g.down(1, 0.0, 0.0, 0);
        g.r.tick(500, &mut g.out);
        g.take();
        g.down(2, 50.0, 50.0, 520);
        g.up(2, 50.0, 50.0, 540);
        g.up(1, 0.0, 0.0, 600);
        assert_eq!(g.buttons(), vec![(Btn::Left, false)]);
    }

    #[test]
    fn strays_and_repeats_are_ignored() {
        let mut g = Rig::new();
        g.mv(9, 1.0, 1.0, 0);
        g.up(9, 1.0, 1.0, 0);
        g.down(1, 0.0, 0.0, 0);
        g.down(1, 5.0, 5.0, 1);
        g.up(1, 0.0, 0.0, 50);
        assert_eq!(g.buttons().len(), 2);
    }

    #[test]
    fn config_is_clamped_to_usable_ranges() {
        let c = Config {
            sens: f64::NAN,
            tap_ms: 0,
            slop: 1e9,
            hold_ms: 1,
            swipe_px: -3.0,
            natural_scroll: true,
            scroll_gain: 1e9,
        }
        .clamped();
        assert!(
            c.sens >= 0.3
                && c.slop <= 30.0
                && c.tap_ms >= 80
                && c.hold_ms >= 200
                && c.swipe_px >= 30.0
                && c.scroll_gain <= 8.0
        );
    }
}
