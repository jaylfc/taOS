// SPDX-License-Identifier: AGPL-3.0-or-later
//! Finding boards: USB ids on serial ports, a bootloader probe, and UF2 bootloader drives.
use crate::boards::{board, Family, BOARDS};
use crate::tools::{resolve, Runner, Tool};
use serde::Serialize;
use std::fs;
use std::path::{Path, PathBuf};
use std::time::Duration;

#[derive(Clone, Debug, Serialize, PartialEq)]
pub struct Device {
    pub id: String,
    /// `serial` or `uf2`
    pub kind: String,
    pub port: Option<String>,
    pub mount: Option<String>,
    pub vid: Option<String>,
    pub pid: Option<String>,
    pub product: Option<String>,
    pub serial_number: Option<String>,
    pub family: Option<Family>,
    /// Chip reported by the bootloader probe (esptool chip id) when it ran.
    pub chip: Option<String>,
    /// `ok`, `failed: ...`, `skipped`, `not-applicable`
    pub probe: String,
    /// Boards this device could be, by board id.
    pub candidates: Vec<String>,
    pub uf2_model: Option<String>,
    pub uf2_board_id: Option<String>,
}

pub struct Cfg {
    pub usb_root: PathBuf,
    pub dev_root: PathBuf,
    pub uf2_roots: Vec<PathBuf>,
}

impl Cfg {
    pub fn from_env() -> Cfg {
        let usb_root = std::env::var_os("TAOS_FW_USB_ROOT")
            .map(PathBuf::from)
            .unwrap_or_else(|| "/sys/bus/usb/devices".into());
        let dev_root = std::env::var_os("TAOS_FW_DEV_ROOT")
            .map(PathBuf::from)
            .unwrap_or_else(|| "/dev".into());
        let uf2_roots = match std::env::var_os("TAOS_FW_UF2_ROOTS") {
            Some(v) => std::env::split_paths(&v).collect(),
            None => {
                let user = std::env::var("USER").unwrap_or_default();
                vec![
                    PathBuf::from(format!("/run/media/{user}")),
                    PathBuf::from(format!("/media/{user}")),
                    PathBuf::from("/mnt"),
                ]
            }
        };
        Cfg {
            usb_root,
            dev_root,
            uf2_roots,
        }
    }
}

fn read_trim(p: &Path) -> Option<String> {
    fs::read_to_string(p)
        .ok()
        .map(|s| s.trim().to_string())
        .filter(|s| !s.is_empty())
}

fn find_tty(dir: &Path, depth: u8) -> Option<String> {
    for e in fs::read_dir(dir).ok()?.flatten() {
        let name = e.file_name().to_string_lossy().into_owned();
        let Ok(ft) = e.file_type() else { continue };
        if ft.is_symlink() {
            continue;
        }
        if name.starts_with("ttyUSB") || name.starts_with("ttyACM") {
            return Some(name);
        }
        if ft.is_dir() && depth > 0 {
            if let Some(f) = find_tty(&e.path(), depth - 1) {
                return Some(f);
            }
        }
    }
    None
}

fn parse_chip(out: &str) -> Option<String> {
    let l = out.to_ascii_uppercase();
    for (needle, chip) in [
        ("ESP32-S3", "esp32s3"),
        ("ESP32-S2", "esp32s2"),
        ("ESP32-C3", "esp32c3"),
        ("ESP32-C6", "esp32c6"),
        ("ESP32-D", "esp32"),
        ("ESP32 ", "esp32"),
        ("ESP32\n", "esp32"),
    ] {
        if l.contains(needle) {
            return Some(chip.into());
        }
    }
    None
}

pub fn probe_argv(esptool: &str, port: &str) -> Vec<String> {
    vec![
        esptool.into(),
        "--port".into(),
        port.into(),
        "chip_id".into(),
    ]
}

pub fn detect(cfg: &Cfg, runner: &dyn Runner, probe: bool) -> Vec<Device> {
    let mut out = detect_serial(cfg, runner, probe);
    out.extend(detect_uf2(cfg));
    out.sort_by(|a, b| a.id.cmp(&b.id));
    out
}

/// Ask one ESP32 board for its chip (`esptool chip_id`, which resets it) and narrow its candidates.
/// Never called on a device that is not the one the person chose, except for an explicit probe listing.
pub fn probe_device(runner: &dyn Runner, d: &mut Device) {
    let Some(port) = d.port.clone() else { return };
    if d.probe != "skipped" && !d.probe.starts_with("failed") {
        return;
    }
    let tool = match resolve(Tool::Esptool) {
        Ok(t) => t,
        Err(e) => {
            d.probe = format!("failed: {e}");
            return;
        }
    };
    d.probe = match runner.run(&probe_argv(&tool, &port), Duration::from_secs(20)) {
        Ok(r) if r.ok() => match parse_chip(&format!("{}\n{}", r.stdout, r.stderr)) {
            Some(c) => {
                d.candidates
                    .retain(|id| crate::boards::board(id).is_some_and(|b| b.chip == c));
                d.chip = Some(c);
                "ok".into()
            }
            None => "failed: esptool did not report a chip".into(),
        },
        Ok(r) => format!("failed: {}", r.tail()),
        Err(e) => format!("failed: {e}"),
    };
}

fn detect_serial(cfg: &Cfg, runner: &dyn Runner, probe: bool) -> Vec<Device> {
    let mut out = vec![];
    let Ok(rd) = fs::read_dir(&cfg.usb_root) else {
        return out;
    };
    for e in rd.flatten() {
        let name = e.file_name().to_string_lossy().into_owned();
        if name.contains(':') || name.starts_with("usb") {
            continue;
        }
        let dir = e.path();
        let (Some(v), Some(p)) = (
            read_trim(&dir.join("idVendor")),
            read_trim(&dir.join("idProduct")),
        ) else {
            continue;
        };
        let (Ok(vn), Ok(pn)) = (u16::from_str_radix(&v, 16), u16::from_str_radix(&p, 16)) else {
            continue;
        };
        let cands: Vec<&crate::boards::Board> = BOARDS
            .iter()
            .filter(|b| b.usb.contains(&(vn, pn)))
            .collect();
        if cands.is_empty() {
            continue;
        }
        let Some(tty) = find_tty(&dir, 3) else {
            continue;
        };
        let port = cfg.dev_root.join(&tty).display().to_string();
        let serial = read_trim(&dir.join("serial"));
        let esp = cands.iter().any(|b| b.family == Family::Esp32);
        let mut d = Device {
            // The hub port is part of the id: identical bridges often share one serial number.
            id: format!(
                "usb-{v}-{p}-{}-{name}",
                serial.clone().unwrap_or_else(|| tty.clone())
            ),
            kind: "serial".into(),
            port: Some(port),
            mount: None,
            vid: Some(v),
            pid: Some(p),
            product: read_trim(&dir.join("product")),
            serial_number: serial,
            family: Some(cands[0].family),
            chip: None,
            probe: if esp { "skipped" } else { "not-applicable" }.into(),
            candidates: cands.iter().map(|b| b.id.to_string()).collect(),
            uf2_model: None,
            uf2_board_id: None,
        };
        if probe && esp {
            probe_device(runner, &mut d);
        }
        out.push(d);
    }
    out
}

fn uf2_info(mount: &Path) -> Option<(String, String)> {
    let t = fs::read_to_string(mount.join("INFO_UF2.TXT")).ok()?;
    let get = |k: &str| {
        t.lines()
            .find_map(|l| l.strip_prefix(k).map(|v| v.trim().to_string()))
            .unwrap_or_default()
    };
    Some((get("Model:"), get("Board-ID:")))
}

fn detect_uf2(cfg: &Cfg) -> Vec<Device> {
    let mut out = vec![];
    let mut seen = vec![];
    for root in &cfg.uf2_roots {
        let mut dirs = vec![root.clone()];
        if let Ok(rd) = fs::read_dir(root) {
            dirs.extend(
                rd.flatten()
                    .filter(|e| e.file_type().is_ok_and(|t| t.is_dir()))
                    .map(|e| e.path()),
            );
        }
        for d in dirs {
            let Some((model, bid)) = uf2_info(&d) else {
                continue;
            };
            if seen.contains(&d) {
                continue;
            }
            seen.push(d.clone());
            let hay = format!("{model} {bid}").to_ascii_lowercase();
            let cands: Vec<String> = BOARDS
                .iter()
                .filter(|b| b.uf2.iter().any(|s| hay.contains(s)))
                .map(|b| b.id.to_string())
                .collect();
            let base = d
                .file_name()
                .map_or("uf2".into(), |n| n.to_string_lossy().into_owned());
            out.push(Device {
                id: format!("uf2-{base}"),
                kind: "uf2".into(),
                port: None,
                mount: Some(d.display().to_string()),
                vid: None,
                pid: None,
                product: None,
                serial_number: None,
                family: cands.first().and_then(|c| board(c)).map(|b| b.family),
                chip: None,
                probe: "not-applicable".into(),
                candidates: cands,
                uf2_model: Some(model),
                uf2_board_id: Some(bid),
            });
        }
    }
    out
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn chip_parsing() {
        assert_eq!(
            parse_chip("Chip is ESP32-S3 (QFN56) (revision v0.2)").as_deref(),
            Some("esp32s3")
        );
        assert_eq!(
            parse_chip("Chip type:          ESP32-D0WD-V3 (revision v3.1)").as_deref(),
            Some("esp32")
        );
        assert_eq!(parse_chip("Chip is ESP32\n").as_deref(), Some("esp32"));
        assert_eq!(parse_chip("nothing"), None);
    }
}
