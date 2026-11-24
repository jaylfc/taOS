// SPDX-License-Identifier: AGPL-3.0-or-later
//! The keyboard map the engine uploads to the compositor.
//!
//! Layout: a fixed US style base (letters, digits, punctuation with shift, the
//! special keys and the four modifiers, at their Linux key codes) plus a pool
//! of spare key codes that the engine fills on demand with any other Unicode
//! character. The map is only re-sent when the pool changes (a character not
//! seen before), so ordinary typing never uploads anything.
//!
//! Shortcuts work because the base keys carry real keysyms: Ctrl+C is the `c`
//! key with the Control modifier held, Super+Return is the Return key with
//! Mod4 held.

pub const SHIFT: u32 = 1;
pub const CONTROL: u32 = 4;
pub const ALT: u32 = 8;
pub const SUPER: u32 = 64;

const POOL_FIRST: u32 = 200;
const POOL_LEN: usize = 48;

/// (code, plain, shifted, plain keysym, shifted keysym)
const PRINTABLE: &[(u32, char, char, &str, &str)] = &[
    (2, '1', '!', "1", "exclam"),
    (3, '2', '@', "2", "at"),
    (4, '3', '#', "3", "numbersign"),
    (5, '4', '$', "4", "dollar"),
    (6, '5', '%', "5", "percent"),
    (7, '6', '^', "6", "asciicircum"),
    (8, '7', '&', "7", "ampersand"),
    (9, '8', '*', "8", "asterisk"),
    (10, '9', '(', "9", "parenleft"),
    (11, '0', ')', "0", "parenright"),
    (12, '-', '_', "minus", "underscore"),
    (13, '=', '+', "equal", "plus"),
    (26, '[', '{', "bracketleft", "braceleft"),
    (27, ']', '}', "bracketright", "braceright"),
    (39, ';', ':', "semicolon", "colon"),
    (40, '\'', '"', "apostrophe", "quotedbl"),
    (41, '`', '~', "grave", "asciitilde"),
    (43, '\\', '|', "backslash", "bar"),
    (51, ',', '<', "comma", "less"),
    (52, '.', '>', "period", "greater"),
    (53, '/', '?', "slash", "question"),
];

const LETTER_ROWS: &[(u32, &str)] = &[(16, "qwertyuiop"), (30, "asdfghjkl"), (44, "zxcvbnm")];

/// (DOM key name, code, keysym)
const SPECIAL: &[(&str, u32, &str)] = &[
    ("Escape", 1, "Escape"),
    ("Backspace", 14, "BackSpace"),
    ("Tab", 15, "Tab"),
    ("Enter", 28, "Return"),
    ("Control", 29, "Control_L"),
    ("Shift", 42, "Shift_L"),
    ("Alt", 56, "Alt_L"),
    (" ", 57, "space"),
    ("F1", 59, "F1"),
    ("F2", 60, "F2"),
    ("F3", 61, "F3"),
    ("F4", 62, "F4"),
    ("F5", 63, "F5"),
    ("F6", 64, "F6"),
    ("F7", 65, "F7"),
    ("F8", 66, "F8"),
    ("F9", 67, "F9"),
    ("F10", 68, "F10"),
    ("F11", 87, "F11"),
    ("F12", 88, "F12"),
    ("ControlRight", 97, "Control_R"),
    ("AltRight", 100, "Alt_R"),
    ("Home", 102, "Home"),
    ("ArrowUp", 103, "Up"),
    ("PageUp", 104, "Prior"),
    ("ArrowLeft", 105, "Left"),
    ("ArrowRight", 106, "Right"),
    ("End", 107, "End"),
    ("ArrowDown", 108, "Down"),
    ("PageDown", 109, "Next"),
    ("Insert", 110, "Insert"),
    ("Delete", 111, "Delete"),
    ("Super", 125, "Super_L"),
    ("Menu", 127, "Menu"),
];

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub struct Strike {
    pub code: u32,
    pub shift: bool,
}

#[derive(Debug, PartialEq, Eq)]
pub enum KeyError {
    Unsupported(String),
}

impl std::fmt::Display for KeyError {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        match self {
            KeyError::Unsupported(s) => write!(f, "unsupported key: {s}"),
        }
    }
}

pub struct Keymap {
    slots: Vec<Option<(char, u64)>>,
    tick: u64,
    version: u64,
}

impl Default for Keymap {
    fn default() -> Keymap {
        Keymap::new()
    }
}

fn static_char(ch: char) -> Option<Strike> {
    for &(code, plain, shifted, _, _) in PRINTABLE {
        if ch == plain {
            return Some(Strike { code, shift: false });
        }
        if ch == shifted {
            return Some(Strike { code, shift: true });
        }
    }
    if ch == ' ' {
        return Some(Strike {
            code: 57,
            shift: false,
        });
    }
    if ch.is_ascii_alphabetic() {
        let lower = ch.to_ascii_lowercase();
        for &(first, row) in LETTER_ROWS {
            if let Some(i) = row.find(lower) {
                return Some(Strike {
                    code: first + i as u32,
                    shift: ch.is_ascii_uppercase(),
                });
            }
        }
    }
    None
}

fn keysym_name(ch: char) -> String {
    format!("U{:04X}", ch as u32)
}

impl Keymap {
    pub fn new() -> Keymap {
        Keymap {
            slots: vec![None; POOL_LEN],
            tick: 0,
            version: 1,
        }
    }

    /// Bumps whenever `text()` changes.
    pub fn version(&self) -> u64 {
        self.version
    }

    /// The key (and whether Shift is needed) that types this character,
    /// filling a pool slot with it if it is not on the base layout.
    pub fn char_strike(&mut self, ch: char) -> Result<Strike, KeyError> {
        match ch {
            '\n' => {
                return Ok(Strike {
                    code: 28,
                    shift: false,
                })
            }
            '\t' => {
                return Ok(Strike {
                    code: 15,
                    shift: false,
                })
            }
            c if (c as u32) < 0x20 || c as u32 == 0x7f => {
                return Err(KeyError::Unsupported(format!(
                    "control character {:#x}",
                    c as u32
                )))
            }
            _ => {}
        }
        if let Some(s) = static_char(ch) {
            return Ok(s);
        }
        self.tick += 1;
        if let Some(i) = self
            .slots
            .iter()
            .position(|s| matches!(s, Some((c, _)) if *c == ch))
        {
            self.slots[i] = Some((ch, self.tick));
            return Ok(Strike {
                code: POOL_FIRST + i as u32,
                shift: false,
            });
        }
        let i = match self.slots.iter().position(|s| s.is_none()) {
            Some(i) => i,
            None => self
                .slots
                .iter()
                .enumerate()
                .min_by_key(|(_, s)| s.map(|(_, t)| t).unwrap_or(0))
                .map(|(i, _)| i)
                .unwrap_or(0),
        };
        self.slots[i] = Some((ch, self.tick));
        self.version += 1;
        Ok(Strike {
            code: POOL_FIRST + i as u32,
            shift: false,
        })
    }

    /// A DOM `KeyboardEvent.key` value: a named key ("Escape", "ArrowLeft",
    /// "F5", "Super") or a single character.
    pub fn key_strike(&mut self, key: &str) -> Result<Strike, KeyError> {
        if let Some(&(_, code, _)) = SPECIAL.iter().find(|(n, _, _)| *n == key) {
            return Ok(Strike { code, shift: false });
        }
        let mut it = key.chars();
        match (it.next(), it.next()) {
            (Some(c), None) => self.char_strike(c),
            _ => Err(KeyError::Unsupported(key.chars().take(24).collect())),
        }
    }

    /// Modifier name to its xkb mask bit.
    pub fn mod_mask(name: &str) -> Option<u32> {
        match name {
            "shift" => Some(SHIFT),
            "ctrl" | "control" => Some(CONTROL),
            "alt" => Some(ALT),
            "super" | "meta" | "win" => Some(SUPER),
            _ => None,
        }
    }

    /// The keymap in xkb text form, for `zwp_virtual_keyboard_v1.keymap`.
    pub fn text(&self) -> String {
        let mut codes: Vec<u32> = Vec::new();
        let mut sym = String::new();
        for &(code, _, _, a, b) in PRINTABLE {
            codes.push(code);
            sym.push_str(&format!(
                "  key <K{code}> {{ type=\"TWO_LEVEL\", symbols[Group1]=[ {a}, {b} ] }};\n"
            ));
        }
        for &(first, row) in LETTER_ROWS {
            for (i, c) in row.chars().enumerate() {
                let code = first + i as u32;
                codes.push(code);
                sym.push_str(&format!(
                    "  key <K{code}> {{ type=\"TWO_LEVEL\", symbols[Group1]=[ {c}, {} ] }};\n",
                    c.to_ascii_uppercase()
                ));
            }
        }
        for &(_, code, s) in SPECIAL {
            codes.push(code);
            sym.push_str(&format!("  key <K{code}> {{ [ {s} ] }};\n"));
        }
        for (i, slot) in self.slots.iter().enumerate() {
            let code = POOL_FIRST + i as u32;
            codes.push(code);
            if let Some((c, _)) = slot {
                sym.push_str(&format!("  key <K{code}> {{ [ {} ] }};\n", keysym_name(*c)));
            }
        }
        let mut s = String::from(
            "xkb_keymap {\nxkb_keycodes \"taos\" {\n  minimum = 8;\n  maximum = 255;\n",
        );
        for c in &codes {
            s.push_str(&format!("  <K{c}> = {};\n", c + 8));
        }
        s.push_str("};\nxkb_types \"taos\" { include \"complete\" };\nxkb_compat \"taos\" { include \"complete\" };\n");
        s.push_str("xkb_symbols \"taos\" {\n");
        s.push_str(&sym);
        s.push_str("  modifier_map Shift { <K42> };\n  modifier_map Control { <K29>, <K97> };\n");
        s.push_str(
            "  modifier_map Mod1 { <K56>, <K100> };\n  modifier_map Mod4 { <K125> };\n};\n};\n",
        );
        s
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn ascii_uses_the_base_layout_and_never_changes_the_map() {
        let mut k = Keymap::new();
        let v = k.version();
        assert_eq!(
            k.char_strike('a').unwrap(),
            Strike {
                code: 30,
                shift: false
            }
        );
        assert_eq!(
            k.char_strike('A').unwrap(),
            Strike {
                code: 30,
                shift: true
            }
        );
        assert_eq!(
            k.char_strike('q').unwrap(),
            Strike {
                code: 16,
                shift: false
            }
        );
        assert_eq!(
            k.char_strike('m').unwrap(),
            Strike {
                code: 50,
                shift: false
            }
        );
        assert_eq!(
            k.char_strike('!').unwrap(),
            Strike {
                code: 2,
                shift: true
            }
        );
        assert_eq!(
            k.char_strike('1').unwrap(),
            Strike {
                code: 2,
                shift: false
            }
        );
        assert_eq!(
            k.char_strike(' ').unwrap(),
            Strike {
                code: 57,
                shift: false
            }
        );
        assert_eq!(
            k.char_strike('?').unwrap(),
            Strike {
                code: 53,
                shift: true
            }
        );
        assert_eq!(k.char_strike('\n').unwrap().code, 28);
        assert_eq!(k.char_strike('\t').unwrap().code, 15);
        assert_eq!(k.version(), v);
    }

    #[test]
    fn every_printable_ascii_character_is_on_the_base_layout() {
        let mut k = Keymap::new();
        for b in 0x20u8..0x7f {
            k.char_strike(b as char).unwrap();
        }
        assert_eq!(k.version(), 1, "no pool slot was needed for ASCII");
    }

    #[test]
    fn other_characters_take_a_pool_slot_once() {
        let mut k = Keymap::new();
        let s = k.char_strike('é').unwrap();
        assert!(s.code >= POOL_FIRST && !s.shift);
        assert_eq!(k.version(), 2);
        assert!(k.text().contains("U00E9"));
        assert_eq!(k.char_strike('é').unwrap(), s);
        assert_eq!(k.version(), 2, "a known character does not change the map");
        let e = k.char_strike('😀').unwrap();
        assert_ne!(e.code, s.code);
        assert!(k.text().contains("U1F600"));
        let z = k.char_strike('日').unwrap();
        assert!(k.text().contains("U65E5"));
        assert_eq!(k.version(), 4);
        assert_ne!(z.code, e.code);
    }

    #[test]
    fn a_full_pool_evicts_the_least_recently_used_character() {
        let mut k = Keymap::new();
        let first = k.char_strike('\u{4e00}').unwrap();
        for i in 1..POOL_LEN as u32 {
            k.char_strike(char::from_u32(0x4e00 + i).unwrap()).unwrap();
        }
        // Touch the first one so the second becomes the oldest.
        assert_eq!(k.char_strike('\u{4e00}').unwrap(), first);
        let fresh = k.char_strike('\u{5000}').unwrap();
        assert!(k.text().contains("U5000"));
        assert!(!k.text().contains("U4E01"), "the oldest was replaced");
        assert!(k.text().contains("U4E00"), "the recently used one was kept");
        assert!(fresh.code >= POOL_FIRST);
    }

    #[test]
    fn control_characters_are_refused_not_typed() {
        let mut k = Keymap::new();
        assert!(k.char_strike('\u{7}').is_err());
        assert!(k.char_strike('\u{7f}').is_err());
        assert!(k.char_strike('\u{1b}').is_err());
    }

    #[test]
    fn named_keys_and_single_characters() {
        let mut k = Keymap::new();
        assert_eq!(k.key_strike("Escape").unwrap().code, 1);
        assert_eq!(k.key_strike("Tab").unwrap().code, 15);
        assert_eq!(k.key_strike("ArrowLeft").unwrap().code, 105);
        assert_eq!(k.key_strike("Super").unwrap().code, 125);
        assert_eq!(k.key_strike("F5").unwrap().code, 63);
        assert_eq!(
            k.key_strike("c").unwrap(),
            Strike {
                code: 46,
                shift: false
            }
        );
        assert!(k.key_strike("NoSuchKey").is_err());
        assert!(k.key_strike("").is_err());
    }

    #[test]
    fn modifier_names_map_to_the_xkb_bits() {
        assert_eq!(Keymap::mod_mask("shift"), Some(1));
        assert_eq!(Keymap::mod_mask("ctrl"), Some(4));
        assert_eq!(Keymap::mod_mask("alt"), Some(8));
        assert_eq!(Keymap::mod_mask("super"), Some(64));
        assert_eq!(Keymap::mod_mask("hyper"), None);
    }

    #[test]
    fn the_text_declares_every_code_it_uses_and_stays_in_range() {
        let mut k = Keymap::new();
        k.char_strike('é').unwrap();
        let t = k.text();
        for line in t.lines().filter(|l| l.trim_start().starts_with("key <")) {
            let name = line.split('<').nth(1).unwrap().split('>').next().unwrap();
            assert!(
                t.contains(&format!("<{name}> =")),
                "{name} used but not declared"
            );
        }
        for line in t.lines().filter(|l| l.contains("> = ")) {
            let n: u32 = line
                .split("= ")
                .nth(1)
                .unwrap()
                .trim_end_matches(';')
                .parse()
                .unwrap();
            assert!((8..=255).contains(&n), "{line}");
        }
        assert!(t.contains("modifier_map Mod4 { <K125> }"));
    }

    #[test]
    fn base_codes_are_unique_and_below_the_pool() {
        let mut seen = std::collections::HashSet::new();
        for &(c, ..) in PRINTABLE {
            assert!(seen.insert(c) && c < POOL_FIRST);
        }
        for &(first, row) in LETTER_ROWS {
            for i in 0..row.len() as u32 {
                assert!(seen.insert(first + i));
            }
        }
        for &(_, c, _) in SPECIAL {
            assert!(seen.insert(c) && c < POOL_FIRST, "dup or high code {c}");
        }
    }
}
