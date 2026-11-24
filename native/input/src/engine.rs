// SPDX-License-Identifier: AGPL-3.0-or-later
//! Shared engine state: who is paired, who is connected, what is injected.

use crate::auth::Auth;
use crate::gesture::Dir;
use crate::inject::Injector;
use crate::netpolicy::Policy;
use crate::secure::Identity;
use crate::swayipc;
use std::path::PathBuf;
use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::{Arc, Mutex};
use std::time::Instant;

pub struct Engine {
    pub auth: Auth,
    pub identity: Identity,
    /// Host header values this engine answers to (its own address and port).
    pub hosts: Vec<String>,
    pub injector: Injector,
    pub policy: Policy,
    pub url: String,
    pub theme: Option<(PathBuf, String)>,
    pub started: Instant,
    active: Mutex<Option<(String, Arc<AtomicBool>)>>,
    stop: AtomicBool,
}

impl Engine {
    pub fn new(
        auth: Auth,
        identity: Identity,
        hosts: Vec<String>,
        injector: Injector,
        policy: Policy,
        url: String,
        theme: Option<(PathBuf, String)>,
    ) -> Arc<Engine> {
        Arc::new(Engine {
            auth,
            identity,
            hosts,
            injector,
            policy,
            url,
            theme,
            started: Instant::now(),
            active: Mutex::new(None),
            stop: AtomicBool::new(false),
        })
    }

    /// Milliseconds since the engine started (the engine clock).
    pub fn now_ms(&self) -> u64 {
        self.started.elapsed().as_millis() as u64
    }

    /// Make this device the one active controller. A controller that was
    /// already connected is told to leave (one connection at a time, so one
    /// phone's disconnect can never release another phone's held keys).
    pub fn claim(&self, device: &str) -> Arc<AtomicBool> {
        let mut g = self.active.lock().unwrap();
        if let Some((_, old)) = g.take() {
            old.store(true, Ordering::SeqCst);
        }
        let kick = Arc::new(AtomicBool::new(false));
        *g = Some((device.to_string(), kick.clone()));
        kick
    }

    /// Give the slot up, but only if it is still ours.
    pub fn release(&self, kick: &Arc<AtomicBool>) {
        let mut g = self.active.lock().unwrap();
        if matches!(&*g, Some((_, k)) if Arc::ptr_eq(k, kick)) {
            *g = None;
        }
    }

    pub fn connected_device(&self) -> Option<String> {
        self.active.lock().unwrap().as_ref().map(|(d, _)| d.clone())
    }

    /// Disconnect a device (used when it is revoked).
    pub fn kick_device(&self, device: &str) {
        if let Some((d, k)) = &*self.active.lock().unwrap() {
            if d == device {
                k.store(true, Ordering::SeqCst);
            }
        }
    }

    pub fn workspace(&self, d: Dir) {
        let cmd = match d {
            Dir::Next => "workspace next_on_output",
            Dir::Prev => "workspace prev_on_output",
        };
        // Failing to switch is not worth dropping the controller for.
        let _ = swayipc::run_command(cmd);
    }

    pub fn request_stop(&self) {
        self.stop.store(true, Ordering::SeqCst);
    }

    pub fn stopping(&self) -> bool {
        self.stop.load(Ordering::SeqCst)
    }
}

const GRAPHITE_DARK: &[(&str, &str)] = &[
    ("ground", "#0B0B0C"),
    ("surface", "#131314"),
    ("raised", "#1A1A1C"),
    ("hairline", "#FFFFFF14"),
    ("text", "#EDEDEE"),
    ("textSecondary", "#A3A3A8"),
    ("textTertiary", "#8F8F94"),
    ("accent", "#F4F4F5"),
    ("accentText", "#0B0B0C"),
    ("warning", "#D1A257"),
    ("danger", "#E0786F"),
    ("ok", "#6FBF8E"),
    ("focusRing", "#F4F4F5"),
    ("selection", "#FFFFFF1A"),
    ("track", "#FFFFFF14"),
];

fn valid_colour(s: &str) -> bool {
    let b = s.as_bytes();
    (b.len() == 7 || b.len() == 9) && b[0] == b'#' && b[1..].iter().all(|c| c.is_ascii_hexdigit())
}

fn kebab(s: &str) -> String {
    let mut o = String::new();
    for c in s.chars() {
        if c.is_ascii_uppercase() {
            o.push('-');
            o.push(c.to_ascii_lowercase());
        } else {
            o.push(c);
        }
    }
    o
}

/// CSS custom properties for the controller page, from the installed theme
/// (the same `theme.json` the native shell reads) or, without one, Graphite
/// dark. Only well formed colours are copied, so a theme file cannot inject
/// anything into the page.
pub fn tokens_css(theme: &Option<(PathBuf, String)>) -> String {
    let mut vals: Vec<(String, String)> = GRAPHITE_DARK
        .iter()
        .map(|(k, v)| (k.to_string(), v.to_string()))
        .collect();
    let mut radius = 1.0f64;
    if let Some((path, mode)) = theme {
        if let Ok(raw) = std::fs::read_to_string(path) {
            if let Ok(v) = serde_json::from_str::<serde_json::Value>(&raw) {
                if let Some(obj) = v
                    .get("colors")
                    .and_then(|c| c.get(mode.as_str()))
                    .and_then(|c| c.as_object())
                {
                    for (k, val) in vals.iter_mut() {
                        if let Some(s) = obj.get(k.as_str()).and_then(|s| s.as_str()) {
                            if valid_colour(s) {
                                *val = s.to_string();
                            }
                        }
                    }
                }
                if let Some(r) = v
                    .get("skin")
                    .and_then(|s| s.get("radiusScale"))
                    .and_then(|r| r.as_f64())
                {
                    if (0.0..=2.0).contains(&r) {
                        radius = r;
                    }
                }
            }
        }
    }
    let scheme = match theme {
        Some((_, m)) if m == "light" => "light",
        _ => "dark",
    };
    let mut css = format!(":root {{\n  color-scheme: {scheme};\n");
    for (k, v) in &vals {
        css.push_str(&format!("  --taos-{}: {v};\n", kebab(k)));
    }
    css.push_str(&format!("  --taos-radius-scale: {radius};\n}}\n"));
    css
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn default_tokens_are_graphite_dark() {
        let css = tokens_css(&None);
        assert!(css.contains("--taos-ground: #0B0B0C;"));
        assert!(css.contains("--taos-text-secondary: #A3A3A8;"));
        assert!(css.contains("color-scheme: dark"));
    }

    #[test]
    fn a_theme_file_overrides_and_bad_values_are_ignored() {
        let dir = std::env::temp_dir().join(format!("taos-input-theme-{}", std::process::id()));
        std::fs::create_dir_all(&dir).unwrap();
        let p = dir.join("theme.json");
        std::fs::write(
            &p,
            r##"{"colors":{"light":{"ground":"#C6C6C6","accent":"red;} body{display:none","text":"#101010"}},"skin":{"radiusScale":0.5}}"##,
        )
        .unwrap();
        let css = tokens_css(&Some((p, "light".into())));
        assert!(css.contains("--taos-ground: #C6C6C6;"));
        assert!(css.contains("--taos-text: #101010;"));
        assert!(
            css.contains("--taos-accent: #F4F4F5;"),
            "injection attempt falls back to the default"
        );
        assert!(!css.contains("display:none"));
        assert!(css.contains("--taos-radius-scale: 0.5;"));
        assert!(css.contains("color-scheme: light"));
        std::fs::remove_dir_all(&dir).ok();
    }

    #[test]
    fn a_missing_theme_file_falls_back() {
        let css = tokens_css(&Some((
            PathBuf::from("/nonexistent/theme.json"),
            "dark".into(),
        )));
        assert!(css.contains("--taos-ground: #0B0B0C;"));
    }
}
