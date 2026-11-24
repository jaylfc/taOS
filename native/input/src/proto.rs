// SPDX-License-Identifier: AGPL-3.0-or-later
//! The messages a controller sends, and how they become engine commands.
//!
//! One `Session` per controller connection. It owns the gesture recognizer
//! and turns each message into `Effects`; it never touches a socket or the
//! compositor, so all of this is plain data in, plain data out.

use crate::gesture::{Btn, Config, Dir, Haptic, Out, Phase, Recognizer, Touch};
use crate::inject::{Command, KeyAction};
use crate::keymap::Keymap;
use serde::{Deserialize, Serialize};

pub const MAX_TEXT_CHARS: usize = 2048;
const MAX_REL: f64 = 4000.0;

#[derive(Debug, Deserialize, PartialEq)]
#[serde(tag = "t", rename_all = "lowercase")]
pub enum ClientMsg {
    /// One finger event. `ts` is the page clock in milliseconds.
    Touch {
        p: TouchPhase,
        i: u32,
        x: f64,
        y: f64,
        ts: f64,
    },
    /// Raw relative pointer motion.
    Ptr {
        dx: f64,
        dy: f64,
    },
    /// Absolute pointer position, 0 to 1 over the whole desktop.
    Abs {
        x: f64,
        y: f64,
    },
    Btn {
        b: String,
        d: bool,
    },
    Scroll {
        dx: f64,
        dy: f64,
    },
    Text {
        s: String,
    },
    Key {
        k: String,
        #[serde(default)]
        m: Vec<String>,
        #[serde(default)]
        a: Option<String>,
    },
    /// The full set of held modifiers (the sticky toggles).
    Mods {
        m: Vec<String>,
    },
    Ws {
        d: String,
    },
    Cfg {
        sens: Option<f64>,
        natural: Option<bool>,
        scroll: Option<f64>,
    },
    Ping {
        n: u64,
    },
}

#[derive(Debug, Deserialize, PartialEq, Clone, Copy)]
#[serde(rename_all = "lowercase")]
pub enum TouchPhase {
    Down,
    Move,
    Up,
    Cancel,
}

#[derive(Debug, Serialize, PartialEq)]
#[serde(tag = "t", rename_all = "lowercase")]
pub enum ServerMsg {
    Hello {
        proto: u32,
        name: String,
        compositor: bool,
    },
    Haptic {
        k: &'static str,
    },
    Pong {
        n: u64,
    },
    State {
        compositor: bool,
    },
}

#[derive(Debug, Default, PartialEq)]
pub struct Effects {
    pub cmds: Vec<Command>,
    pub replies: Vec<ServerMsg>,
    pub workspace: Option<Dir>,
}

pub struct Session {
    rec: Recognizer,
    offset: i64,
    scratch: Vec<Out>,
}

fn finite(v: f64) -> bool {
    v.is_finite()
}

fn mods_mask(list: &[String]) -> Option<u32> {
    let mut m = 0;
    for s in list {
        m |= Keymap::mod_mask(s)?;
    }
    Some(m)
}

impl Default for Session {
    fn default() -> Session {
        Session::new()
    }
}

impl Session {
    pub fn new() -> Session {
        Session {
            rec: Recognizer::new(Config::default()),
            offset: 0,
            scratch: Vec::new(),
        }
    }

    fn absorb(&mut self, fx: &mut Effects) {
        for o in self.scratch.drain(..) {
            match o {
                Out::Move { dx, dy } => fx.cmds.push(Command::Motion { dx, dy }),
                Out::Button(btn, down) => fx.cmds.push(Command::Button { btn, down }),
                Out::Scroll { dx, dy } => fx.cmds.push(Command::Scroll { dx, dy }),
                // The end-of-scroll marker is deliberately not sent: sway drops
                // the scroll deltas that precede an axis_stop (seen on 1.10).
                Out::ScrollStop => {}
                Out::Workspace(d) => fx.workspace = Some(d),
                Out::Haptic(h) => fx.replies.push(ServerMsg::Haptic {
                    k: match h {
                        Haptic::Tap => "tap",
                        Haptic::Drag => "drag",
                        Haptic::Switch => "switch",
                    },
                }),
            }
        }
    }

    /// Handle one message. `now` is the engine clock in milliseconds.
    /// Malformed numbers or unknown names are ignored, never guessed at.
    pub fn on_message(&mut self, msg: ClientMsg, now: u64) -> Effects {
        let mut fx = Effects::default();
        match msg {
            ClientMsg::Touch { p, i, x, y, ts } => {
                if !(finite(x) && finite(y) && finite(ts))
                    || i > 31
                    || x.abs() > 1e5
                    || y.abs() > 1e5
                {
                    return fx;
                }
                let t = ts.max(0.0) as u64;
                self.offset = now as i64 - t as i64;
                let phase = match p {
                    TouchPhase::Down => Phase::Down,
                    TouchPhase::Move => Phase::Move,
                    TouchPhase::Up => Phase::Up,
                    TouchPhase::Cancel => Phase::Cancel,
                };
                self.rec.touch(
                    Touch {
                        phase,
                        id: i,
                        x,
                        y,
                        t,
                    },
                    &mut self.scratch,
                );
                self.absorb(&mut fx);
            }
            ClientMsg::Ptr { dx, dy } => {
                if finite(dx) && finite(dy) {
                    fx.cmds.push(Command::Motion {
                        dx: dx.clamp(-MAX_REL, MAX_REL),
                        dy: dy.clamp(-MAX_REL, MAX_REL),
                    });
                }
            }
            ClientMsg::Abs { x, y } => {
                if finite(x) && finite(y) {
                    fx.cmds.push(Command::Abs {
                        x: x.clamp(0.0, 1.0),
                        y: y.clamp(0.0, 1.0),
                    });
                }
            }
            ClientMsg::Btn { b, d } => {
                let btn = match b.as_str() {
                    "left" => Btn::Left,
                    "right" => Btn::Right,
                    "middle" => Btn::Middle,
                    _ => return fx,
                };
                fx.cmds.push(Command::Button { btn, down: d });
            }
            ClientMsg::Scroll { dx, dy } => {
                if finite(dx) && finite(dy) {
                    fx.cmds.push(Command::Scroll {
                        dx: dx.clamp(-MAX_REL, MAX_REL),
                        dy: dy.clamp(-MAX_REL, MAX_REL),
                    });
                }
            }
            ClientMsg::Text { s } => {
                let s: String = s.chars().take(MAX_TEXT_CHARS).collect();
                if !s.is_empty() {
                    fx.cmds.push(Command::Text(s));
                }
            }
            ClientMsg::Key { k, m, a } => {
                let Some(mods) = mods_mask(&m) else { return fx };
                let action = match a.as_deref() {
                    None | Some("tap") => KeyAction::Tap,
                    Some("down") => KeyAction::Down,
                    Some("up") => KeyAction::Up,
                    _ => return fx,
                };
                if k.chars().count() <= 24 && !k.is_empty() {
                    fx.cmds.push(Command::Key {
                        key: k,
                        mods,
                        action,
                    });
                }
            }
            ClientMsg::Mods { m } => {
                if let Some(mask) = mods_mask(&m) {
                    fx.cmds.push(Command::SetMods(mask));
                }
            }
            ClientMsg::Ws { d } => {
                fx.workspace = match d.as_str() {
                    "next" => Some(Dir::Next),
                    "prev" => Some(Dir::Prev),
                    _ => None,
                };
            }
            ClientMsg::Cfg {
                sens,
                natural,
                scroll,
            } => {
                let mut c = self.rec.config().clone();
                if let Some(v) = sens {
                    c.sens = v;
                }
                if let Some(v) = natural {
                    c.natural_scroll = v;
                }
                if let Some(v) = scroll {
                    c.scroll_gain = v;
                }
                self.rec.set_config(c);
            }
            ClientMsg::Ping { n } => fx.replies.push(ServerMsg::Pong { n }),
        }
        fx
    }

    /// Called regularly; fires the hold gesture when its time comes.
    pub fn tick(&mut self, now: u64) -> Effects {
        let mut fx = Effects::default();
        let client_now = (now as i64 - self.offset).max(0) as u64;
        self.rec.tick(client_now, &mut self.scratch);
        self.absorb(&mut fx);
        fx
    }

    /// The connection ended: lift whatever was held.
    pub fn finish(&mut self) -> Effects {
        let mut fx = Effects::default();
        self.rec.reset(&mut self.scratch);
        self.absorb(&mut fx);
        fx.cmds.push(Command::ReleaseAll);
        fx
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn parse(s: &str) -> ClientMsg {
        serde_json::from_str(s).unwrap()
    }

    #[test]
    fn wire_format_parses() {
        assert_eq!(parse(r#"{"t":"ping","n":7}"#), ClientMsg::Ping { n: 7 });
        assert_eq!(
            parse(r#"{"t":"touch","p":"down","i":0,"x":1.5,"y":2,"ts":10}"#),
            ClientMsg::Touch {
                p: TouchPhase::Down,
                i: 0,
                x: 1.5,
                y: 2.0,
                ts: 10.0
            }
        );
        assert_eq!(
            parse(r#"{"t":"key","k":"c","m":["ctrl"]}"#),
            ClientMsg::Key {
                k: "c".into(),
                m: vec!["ctrl".into()],
                a: None
            }
        );
        assert!(serde_json::from_str::<ClientMsg>(r#"{"t":"nuke"}"#).is_err());
        assert!(serde_json::from_str::<ClientMsg>(r#"{"t":"text"}"#).is_err());
    }

    #[test]
    fn server_messages_serialise_flat() {
        assert_eq!(
            serde_json::to_string(&ServerMsg::Haptic { k: "tap" }).unwrap(),
            r#"{"t":"haptic","k":"tap"}"#
        );
        assert_eq!(
            serde_json::to_string(&ServerMsg::Pong { n: 3 }).unwrap(),
            r#"{"t":"pong","n":3}"#
        );
    }

    #[test]
    fn a_tap_becomes_a_click_and_a_haptic_using_the_page_clock() {
        let mut s = Session::new();
        let touch = |p, ts| ClientMsg::Touch {
            p,
            i: 0,
            x: 10.0,
            y: 10.0,
            ts,
        };
        s.on_message(touch(TouchPhase::Down, 5000.0), 100_000);
        let fx = s.on_message(touch(TouchPhase::Up, 5090.0), 100_400); // arrival jitter must not matter
        assert_eq!(
            fx.cmds,
            vec![
                Command::Button {
                    btn: Btn::Left,
                    down: true
                },
                Command::Button {
                    btn: Btn::Left,
                    down: false
                }
            ]
        );
        assert_eq!(fx.replies, vec![ServerMsg::Haptic { k: "tap" }]);
    }

    #[test]
    fn hold_fires_from_tick_on_the_page_timeline() {
        let mut s = Session::new();
        s.on_message(
            ClientMsg::Touch {
                p: TouchPhase::Down,
                i: 0,
                x: 0.0,
                y: 0.0,
                ts: 1000.0,
            },
            50_000,
        );
        assert!(s.tick(50_300).cmds.is_empty());
        let fx = s.tick(50_460);
        assert_eq!(
            fx.cmds,
            vec![Command::Button {
                btn: Btn::Left,
                down: true
            }]
        );
        let fx = s.finish();
        assert_eq!(
            fx.cmds,
            vec![
                Command::Button {
                    btn: Btn::Left,
                    down: false
                },
                Command::ReleaseAll
            ]
        );
    }

    #[test]
    fn swipe_asks_for_a_workspace_change_not_a_command_for_the_compositor() {
        let mut s = Session::new();
        let mut last = Effects::default();
        for i in 0..3u32 {
            s.on_message(
                ClientMsg::Touch {
                    p: TouchPhase::Down,
                    i,
                    x: 100.0 + 30.0 * i as f64,
                    y: 0.0,
                    ts: 0.0,
                },
                0,
            );
        }
        for step in 1..=6 {
            for i in 0..3u32 {
                let fx = s.on_message(
                    ClientMsg::Touch {
                        p: TouchPhase::Move,
                        i,
                        x: 100.0 + 30.0 * i as f64 - 25.0 * step as f64,
                        y: 0.0,
                        ts: 20.0 * step as f64,
                    },
                    20 * step,
                );
                if fx.workspace.is_some() {
                    last = fx;
                }
            }
        }
        assert_eq!(last.workspace, Some(Dir::Next));
    }

    #[test]
    fn keys_text_and_modifiers() {
        let mut s = Session::new();
        let fx = s.on_message(parse(r#"{"t":"key","k":"c","m":["ctrl","shift"]}"#), 0);
        assert_eq!(
            fx.cmds,
            vec![Command::Key {
                key: "c".into(),
                mods: 5,
                action: KeyAction::Tap
            }]
        );
        let fx = s.on_message(parse(r#"{"t":"mods","m":["super"]}"#), 0);
        assert_eq!(fx.cmds, vec![Command::SetMods(64)]);
        let fx = s.on_message(parse(r#"{"t":"text","s":"héllo"}"#), 0);
        assert_eq!(fx.cmds, vec![Command::Text("héllo".into())]);
    }

    #[test]
    fn nonsense_is_ignored_not_guessed() {
        let mut s = Session::new();
        for raw in [
            r#"{"t":"key","k":"c","m":["hyper"]}"#,
            r#"{"t":"key","k":"c","a":"sideways"}"#,
            r#"{"t":"key","k":"","m":[]}"#,
            r#"{"t":"btn","b":"fourth","d":true}"#,
            r#"{"t":"mods","m":["bogus"]}"#,
            r#"{"t":"ws","d":"sideways"}"#,
            r#"{"t":"text","s":""}"#,
        ] {
            assert_eq!(s.on_message(parse(raw), 0), Effects::default(), "{raw}");
        }
        let fx = s.on_message(
            ClientMsg::Ptr {
                dx: f64::NAN,
                dy: 1.0,
            },
            0,
        );
        assert!(fx.cmds.is_empty());
        let fx = s.on_message(
            ClientMsg::Touch {
                p: TouchPhase::Down,
                i: 99,
                x: 0.0,
                y: 0.0,
                ts: 0.0,
            },
            0,
        );
        assert!(fx.cmds.is_empty() && fx.replies.is_empty());
    }

    #[test]
    fn huge_values_are_clamped_and_long_text_is_cut() {
        let mut s = Session::new();
        let fx = s.on_message(
            ClientMsg::Ptr {
                dx: 1e12,
                dy: -1e12,
            },
            0,
        );
        assert_eq!(
            fx.cmds,
            vec![Command::Motion {
                dx: 4000.0,
                dy: -4000.0
            }]
        );
        let fx = s.on_message(
            ClientMsg::Text {
                s: "a".repeat(10_000),
            },
            0,
        );
        match &fx.cmds[0] {
            Command::Text(t) => assert_eq!(t.chars().count(), MAX_TEXT_CHARS),
            other => panic!("{other:?}"),
        }
        let fx = s.on_message(ClientMsg::Abs { x: 9.0, y: -1.0 }, 0);
        assert_eq!(fx.cmds, vec![Command::Abs { x: 1.0, y: 0.0 }]);
    }

    #[test]
    fn ping_is_answered_with_the_same_number() {
        let mut s = Session::new();
        assert_eq!(
            s.on_message(ClientMsg::Ping { n: 42 }, 0).replies,
            vec![ServerMsg::Pong { n: 42 }]
        );
    }

    #[test]
    fn config_changes_the_gesture_feel() {
        let mut s = Session::new();
        s.on_message(
            ClientMsg::Cfg {
                sens: Some(1.0),
                natural: Some(false),
                scroll: None,
            },
            0,
        );
        s.on_message(
            ClientMsg::Touch {
                p: TouchPhase::Down,
                i: 0,
                x: 0.0,
                y: 0.0,
                ts: 0.0,
            },
            0,
        );
        let fx = s.on_message(
            ClientMsg::Touch {
                p: TouchPhase::Move,
                i: 0,
                x: 20.0,
                y: 0.0,
                ts: 400.0,
            },
            400,
        );
        match fx.cmds[0] {
            Command::Motion { dx, .. } => assert!(
                (20.0..22.0).contains(&dx),
                "sens 1.0 at slow speed is about 1:1, got {dx}"
            ),
            ref o => panic!("{o:?}"),
        }
    }
}
