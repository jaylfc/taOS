// SPDX-License-Identifier: AGPL-3.0-or-later
//! Thin wrapper over swaymsg (set SWAYMSG to use another binary).

use serde_json::Value;
use std::process::Command;

#[derive(Debug, Clone, PartialEq)]
pub struct Output {
    pub name: String,
    pub active: bool,
    pub width: u64,
    pub height: u64,
}

fn bin() -> String {
    std::env::var("SWAYMSG").unwrap_or_else(|_| "swaymsg".into())
}

fn run(args: &[&str]) -> Result<String, String> {
    let o = Command::new(bin())
        .args(args)
        .output()
        .map_err(|e| format!("swaymsg: {e}"))?;
    let out = String::from_utf8_lossy(&o.stdout).to_string();
    if !o.status.success() {
        return Err(format!(
            "swaymsg {} failed: {} {}",
            args.join(" "),
            out.trim(),
            String::from_utf8_lossy(&o.stderr).trim()
        ));
    }
    Ok(out)
}

/// Run one sway command and check every reply entry for success.
pub fn command(c: &str) -> Result<(), String> {
    let out = run(&["-r", c])?;
    let v: Value = serde_json::from_str(&out).map_err(|e| format!("swaymsg reply: {e}"))?;
    for r in v.as_array().into_iter().flatten() {
        if r.get("success").and_then(Value::as_bool) != Some(true) {
            return Err(format!("sway refused `{c}`: {r}"));
        }
    }
    Ok(())
}

pub fn outputs() -> Result<Vec<Output>, String> {
    let out = run(&["-t", "get_outputs", "-r"])?;
    let v: Value = serde_json::from_str(&out).map_err(|e| format!("get_outputs: {e}"))?;
    Ok(v.as_array()
        .ok_or("get_outputs: not a list")?
        .iter()
        .filter_map(|o| {
            Some(Output {
                name: o.get("name")?.as_str()?.to_string(),
                active: o.get("active").and_then(Value::as_bool).unwrap_or(false),
                width: o
                    .pointer("/current_mode/width")
                    .and_then(Value::as_u64)
                    .unwrap_or(0),
                height: o
                    .pointer("/current_mode/height")
                    .and_then(Value::as_u64)
                    .unwrap_or(0),
            })
        })
        .collect())
}

/// Create a headless output and return its name (found by diffing the list).
pub fn create_output() -> Result<String, String> {
    let before: Vec<String> = outputs()?.into_iter().map(|o| o.name).collect();
    command("create_output")?;
    outputs()?
        .into_iter()
        .map(|o| o.name)
        .find(|n| !before.contains(n))
        .ok_or_else(|| "create_output made no new output".to_string())
}

pub fn set_mode(name: &str, size: &str, fps: u32) -> Result<(), String> {
    command(&format!("output {name} mode {size}@{fps}Hz"))
        .or_else(|_| command(&format!("output {name} mode {size}")))
}

pub fn enable(name: &str) -> Result<(), String> {
    command(&format!("output {name} enable"))
}
pub fn disable(name: &str) -> Result<(), String> {
    command(&format!("output {name} disable"))
}
pub fn unplug(name: &str) -> Result<(), String> {
    command(&format!("output {name} unplug"))
}

pub fn set_wallpaper(name: &str, path: Option<&str>) {
    let c = match path {
        Some(p) if !p.contains('"') => format!("output {name} bg \"{p}\" fill"),
        _ => format!("output {name} bg #0b0d14 solid_color"),
    };
    let _ = command(&c); // a missing wallpaper never stops a cast
}
