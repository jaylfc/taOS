// SPDX-License-Identifier: AGPL-3.0-or-later
//! The guarded flash flow: check, back up, confirm, flash, verify, restore, and say how to recover.
use crate::boards::{self, Board, Network};
use crate::detect::{self, Device};
use crate::store::{self, Consent, Entry, Licences, Manifest, Paths};
use crate::timefmt;
use crate::tools::{self, RunResult, Runner, Tool};
use serde::Serialize;
use serde_json::{json, Value};
use std::fs;
use std::path::{Path, PathBuf};
use std::time::Duration;

pub struct Ctx<'a> {
    pub paths: &'a Paths,
    pub runner: &'a dyn Runner,
    pub cfg: &'a detect::Cfg,
    /// How long to wait for the board to come back after a flash.
    pub verify_wait: Duration,
}

#[derive(Debug, Clone, Default)]
pub struct Request {
    pub device_id: String,
    pub entry_id: String,
    /// The board name typed by the person.
    pub confirm: Option<String>,
    /// `person` or `agent:<id>`.
    pub actor: String,
    pub allow_no_backup: bool,
    /// Erase the whole flash first (clean switch between networks). ESP32 only.
    pub erase: bool,
    /// Backup id to restore after the flash (default: the backup taken now).
    pub restore_from: Option<String>,
}

#[derive(Debug, Clone, Serialize)]
pub struct Refusal {
    pub code: &'static str,
    pub message: String,
}

fn refuse<T>(code: &'static str, message: impl Into<String>) -> Result<T, Refusal> {
    Err(Refusal {
        code,
        message: message.into(),
    })
}

#[derive(Debug, Clone, Serialize)]
pub struct Step {
    pub name: String,
    /// The exact argument list that will be run, when the step runs a program.
    pub argv: Option<Vec<String>>,
    pub detail: String,
}

#[derive(Debug, Clone, Serialize)]
pub struct Plan {
    pub device: Device,
    pub entry_id: String,
    pub board: String,
    pub network: String,
    pub version: String,
    /// What the person must type to confirm.
    pub confirm_phrase: String,
    pub method: String,
    pub backup: String,
    pub steps: Vec<Step>,
    pub warnings: Vec<String>,
    pub recovery: String,
}

pub fn esptool_argv(tool: &str, chip: &str, port: &str, parts: &[(String, String)]) -> Vec<String> {
    let mut a: Vec<String> = [
        tool,
        "--chip",
        chip,
        "--port",
        port,
        "--baud",
        "460800",
        "write_flash",
        "-z",
    ]
    .iter()
    .map(|s| s.to_string())
    .collect();
    for (off, file) in parts {
        a.push(off.clone());
        a.push(file.clone());
    }
    a
}

pub fn nrfutil_argv(tool: &str, package: &str, port: &str, touch: bool) -> Vec<String> {
    let mut a: Vec<String> = [
        tool,
        "dfu",
        "serial",
        "--package",
        package,
        "-p",
        port,
        "-b",
        "115200",
        "--singlebank",
    ]
    .iter()
    .map(|s| s.to_string())
    .collect();
    if touch {
        a.push("--touch".into());
        a.push("1200".into());
    }
    a
}

pub fn meshtastic_export_argv(tool: &str, port: &str) -> Vec<String> {
    vec![
        tool.into(),
        "--port".into(),
        port.into(),
        "--export-config".into(),
    ]
}
pub fn meshtastic_configure_argv(tool: &str, port: &str, file: &str) -> Vec<String> {
    vec![
        tool.into(),
        "--port".into(),
        port.into(),
        "--configure".into(),
        file.into(),
    ]
}
pub fn meshtastic_info_argv(tool: &str, port: &str) -> Vec<String> {
    vec![tool.into(), "--port".into(), port.into(), "--info".into()]
}
pub fn meshcore_info_argv(tool: &str, port: &str) -> Vec<String> {
    vec![tool.into(), "-s".into(), port.into(), "infos".into()]
}
pub fn rnode_info_argv(tool: &str, port: &str) -> Vec<String> {
    vec![tool.into(), port.into(), "-i".into()]
}

fn norm(s: &str) -> String {
    s.split_whitespace()
        .collect::<Vec<_>>()
        .join(" ")
        .to_lowercase()
}

struct Eval {
    device: Device,
    entry: Entry,
    board: &'static Board,
    plan: Plan,
    /// Resolved flash argv, or the UF2 copy (source, destination).
    action: Action,
}

enum Action {
    Argv(Vec<String>),
    Uf2Copy { src: PathBuf, dest: PathBuf },
}

fn lib_path(p: &Paths, rel: &str) -> String {
    p.library().join(rel).display().to_string()
}

fn evaluate(ctx: &Ctx, device_id: &str, entry_id: &str) -> Result<Eval, Refusal> {
    let man = Manifest::load(ctx.paths).map_err(|m| Refusal {
        code: "manifest",
        message: m,
    })?;
    let Some(entry) = man.get(entry_id).cloned() else {
        return refuse(
            "no_such_firmware",
            format!("no firmware {entry_id} in the library"),
        );
    };
    let Some(net) = Network::parse(&entry.network) else {
        return refuse(
            "no_such_firmware",
            "the library entry names an unknown network",
        );
    };
    let Some(board) = boards::board(&entry.board) else {
        return refuse(
            "no_such_firmware",
            format!("the library entry names an unknown board {}", entry.board),
        );
    };
    // Find the device without touching any board, then probe only the one that was chosen.
    let devices = detect::detect(ctx.cfg, ctx.runner, false);
    let Some(mut device) = devices.into_iter().find(|d| d.id == device_id) else {
        return refuse(
            "no_such_device",
            format!("device {device_id} is not connected"),
        );
    };
    // Cheap refusal first, so a board that obviously does not fit is never reset by a probe.
    if !device.candidates.iter().any(|c| *c == board.id) {
        let seen = if device.candidates.is_empty() {
            "no supported board".to_string()
        } else {
            device.candidates.join(", ")
        };
        return refuse(
            "board_mismatch",
            format!(
                "this image is for {} ({}) but the connected device looks like: {seen}. Refusing.",
                board.name, board.chip
            ),
        );
    }
    if device.kind == "serial" {
        detect::probe_device(ctx.runner, &mut device);
    }
    match Licences::load(ctx.paths) {
        Ok(l) if l.get(net.id()).is_some() => {}
        Ok(_) => {
            return refuse(
                "licence_required",
                format!("the {} licence has not been accepted", net.display()),
            )
        }
        Err(m) => return refuse("manifest", m),
    }
    if let Err(m) = store::verify_entry(ctx.paths, &entry) {
        return refuse("hash_mismatch", m);
    }
    if !device.candidates.iter().any(|c| *c == board.id) {
        let seen = if device.candidates.is_empty() {
            "no supported board".to_string()
        } else {
            device.candidates.join(", ")
        };
        let chip = device.chip.as_deref().unwrap_or("unknown chip");
        return refuse(
            "board_mismatch",
            format!(
                "this image is for {} ({}) but the connected device looks like: {seen} (chip: {chip}). Refusing.",
                board.name, board.chip
            ),
        );
    }
    if let Some(c) = &device.chip {
        if c != board.chip {
            return refuse(
                "chip_mismatch",
                format!("device chip {c} is not {} for {}", board.chip, board.name),
            );
        }
    }
    let mut warnings = vec![];
    if entry.unsigned_by_publisher {
        warnings.push(format!(
            "unsigned by publisher: checked against the release host digest only (sha256 {})",
            entry.source_sha256
        ));
    }
    let mut steps = vec![];
    let (method, action) = match (entry.method.as_str(), device.kind.as_str()) {
        ("esptool", "serial") => {
            if device.probe != "ok" {
                return refuse(
                    "probe_required",
                    format!(
                        "the bootloader probe did not confirm the chip ({}); cannot flash safely",
                        device.probe
                    ),
                );
            }
            let tool = tools::resolve(Tool::Esptool).map_err(|m| Refusal {
                code: "tool_missing",
                message: m,
            })?;
            let port = device.port.clone().unwrap_or_default();
            let parts: Vec<(String, String)> = entry
                .files
                .iter()
                .filter_map(|f| f.offset.clone().map(|o| (o, lib_path(ctx.paths, &f.path))))
                .collect();
            if parts.is_empty() {
                return refuse("hash_mismatch", "the library entry has no flash map");
            }
            let argv = esptool_argv(&tool, board.chip, &port, &parts);
            steps.push(Step {
                name: "flash".into(),
                argv: Some(argv.clone()),
                detail: "write the release flash map".into(),
            });
            ("esptool", Action::Argv(argv))
        }
        ("uf2", "uf2") => {
            let f = entry
                .files
                .iter()
                .find(|f| f.role == "uf2")
                .ok_or(Refusal {
                    code: "no_such_firmware",
                    message: "the entry has no UF2 file".into(),
                })?;
            let mount = PathBuf::from(device.mount.clone().unwrap_or_default());
            let base = Path::new(&f.path)
                .file_name()
                .map(|n| n.to_owned())
                .unwrap_or_default();
            let src = ctx.paths.library().join(&f.path);
            let dest = mount.join(base);
            steps.push(Step {
                name: "flash".into(),
                argv: None,
                detail: format!("copy {} to the bootloader drive", src.display()),
            });
            ("uf2", Action::Uf2Copy { src, dest })
        }
        ("uf2", "serial") | ("nrfutil", "serial") => {
            let Some(f) = entry.files.iter().find(|f| f.role == "dfu-package") else {
                return refuse(
                    "not_in_bootloader",
                    format!(
                        "the board is running its firmware, not its bootloader drive. {}",
                        board.recovery
                    ),
                );
            };
            let tool = tools::resolve(Tool::Nrfutil).map_err(|m| Refusal {
                code: "tool_missing",
                message: m,
            })?;
            let port = device.port.clone().unwrap_or_default();
            let argv = nrfutil_argv(&tool, &lib_path(ctx.paths, &f.path), &port, true);
            steps.push(Step {
                name: "flash".into(),
                argv: Some(argv.clone()),
                detail: "serial DFU with a 1200 baud touch".into(),
            });
            ("nrfutil", Action::Argv(argv))
        }
        ("nrfutil", "uf2") => {
            return refuse(
                "not_in_bootloader",
                "this image is a DFU package; connect the board by its serial port",
            );
        }
        _ => {
            return refuse(
                "board_mismatch",
                format!(
                    "a {} image cannot be flashed to a {} device",
                    entry.method, device.kind
                ),
            );
        }
    };
    let backup = if device.kind == "serial" {
        "will try the Meshtastic config export, then save MeshCore companion info; says so if neither answers"
    } else {
        "not possible: the board is in its bootloader and runs no firmware"
    };
    steps.insert(
        0,
        Step {
            name: "backup".into(),
            argv: None,
            detail: backup.into(),
        },
    );
    steps.push(Step {
        name: "verify".into(),
        argv: None,
        detail: format!(
            "handshake with {} firmware {}",
            net.display(),
            entry.version
        ),
    });
    steps.push(Step {
        name: "restore".into(),
        argv: None,
        detail: "re-apply the saved configuration when the network is the same".into(),
    });
    let plan = Plan {
        device: device.clone(),
        entry_id: entry.id.clone(),
        board: board.id.into(),
        network: entry.network.clone(),
        version: entry.version.clone(),
        confirm_phrase: board.name.into(),
        method: method.into(),
        backup: backup.into(),
        steps,
        warnings,
        recovery: format!("{} {}", board.recovery, boards::GENERIC_RECOVERY),
    };
    Ok(Eval {
        device,
        entry,
        board,
        plan,
        action,
    })
}

pub fn plan(ctx: &Ctx, device_id: &str, entry_id: &str) -> Result<Plan, Refusal> {
    evaluate(ctx, device_id, entry_id).map(|e| e.plan)
}

fn log(ctx: &Ctx, v: Value) -> Result<(), String> {
    store::audit(ctx.paths, v)
}

fn refused(ctx: &Ctx, req: &Request, r: Refusal) -> Value {
    let logged = log(
        ctx,
        json!({"event":"refused","actor":req.actor,"device":req.device_id,"entry":req.entry_id,"code":r.code,"message":r.message}),
    );
    let mut v = json!({"ok":false,"stage":"refused","code":r.code,"message":r.message});
    if let Err(e) = logged {
        v["log_error"] = e.into();
    }
    v
}

pub fn run(ctx: &Ctx, req: &Request) -> Value {
    let ev = match evaluate(ctx, &req.device_id, &req.entry_id) {
        Ok(e) => e,
        Err(r) => return refused(ctx, req, r),
    };
    if let Some(id) = &req.restore_from {
        if !crate::library::safe_name(id) {
            return refused(
                ctx,
                req,
                Refusal {
                    code: "bad_request",
                    message: "restore_from is not a backup id".into(),
                },
            );
        }
    }
    let typed = req.confirm.as_deref().map(norm).unwrap_or_default();
    if typed.is_empty() || typed != norm(ev.board.name) {
        return refused(
            ctx,
            req,
            Refusal {
                code: "confirmation_required",
                message: format!("type the board name exactly to confirm: {}", ev.board.name),
            },
        );
    }
    if let Some(agent) = req.actor.strip_prefix("agent:") {
        match Consent::load(ctx.paths) {
            Ok(c) if c.valid_for(agent).is_some() => {}
            Ok(_) => {
                return refused(
                    ctx,
                    req,
                    Refusal {
                        code: "consent_required",
                        message: format!("the person has not given agent {agent} consent to flash"),
                    },
                )
            }
            Err(m) => {
                return refused(
                    ctx,
                    req,
                    Refusal {
                        code: "consent_required",
                        message: m,
                    },
                )
            }
        }
    } else if req.actor != "person" {
        return refused(
            ctx,
            req,
            Refusal {
                code: "consent_required",
                message: "actor must be person or agent:<id>".into(),
            },
        );
    }
    execute(ctx, req, ev)
}

fn copy_synced(src: &Path, dest: &Path) -> std::io::Result<()> {
    let mut i = fs::File::open(src)?;
    let mut o = fs::File::create(dest)?;
    std::io::copy(&mut i, &mut o)?;
    o.sync_all()
}

fn tool_or_none(t: Tool) -> Option<String> {
    tools::resolve(t).ok()
}

struct Backup {
    status: &'static str,
    dir: Option<PathBuf>,
    detail: String,
}

fn take_backup(ctx: &Ctx, ev: &Eval) -> Backup {
    let none = |d: &str| Backup {
        status: "not_possible",
        dir: None,
        detail: d.into(),
    };
    let Some(port) = ev.device.port.clone() else {
        return none("the board is in its bootloader and runs no firmware, so there is no configuration to save");
    };
    let id = format!("{}-{}", timefmt::now_secs(), ev.board.id);
    let dir = ctx.paths.backups().join(&id);
    let t = Duration::from_secs(45);
    let tries: Vec<(Network, Option<String>, &str, &str)> = vec![
        (
            Network::Meshtastic,
            tool_or_none(Tool::Meshtastic),
            "meshtastic-config.yaml",
            "export",
        ),
        (
            Network::Meshcore,
            tool_or_none(Tool::MeshcoreCli),
            "meshcore-info.txt",
            "infos",
        ),
    ];
    let mut notes = vec![];
    for (net, tool, fname, _) in tries {
        let Some(tool) = tool else {
            notes.push(format!("{} tool not installed", net.display()));
            continue;
        };
        let argv = if net == Network::Meshtastic {
            meshtastic_export_argv(&tool, &port)
        } else {
            meshcore_info_argv(&tool, &port)
        };
        match ctx.runner.run(&argv, t) {
            Ok(r) if r.ok() && !r.stdout.trim().is_empty() => {
                use std::os::unix::fs::{DirBuilderExt, PermissionsExt};
                if let Err(e) = fs::DirBuilder::new()
                    .recursive(true)
                    .mode(0o700)
                    .create(&dir)
                {
                    return Backup {
                        status: "failed",
                        dir: None,
                        detail: format!("create {}: {e}", dir.display()),
                    };
                }
                let file = dir.join(fname);
                if let Err(e) = store::write_atomic(&file, r.stdout.as_bytes()).and_then(|_| {
                    fs::set_permissions(&file, fs::Permissions::from_mode(0o600))
                        .map_err(|e| e.to_string())
                }) {
                    return Backup {
                        status: "failed",
                        dir: None,
                        detail: e,
                    };
                }
                let meta = json!({"id":id,"board":ev.board.id,"network":net.id(),"file":fname,
                    "device":ev.device.id,"taken_at":timefmt::now_rfc3339(),"replaced_by":ev.entry.id});
                if let Err(e) = serde_json::to_vec_pretty(&meta)
                    .map_err(|e| e.to_string())
                    .and_then(|b| store::write_atomic(&dir.join("backup.json"), &b))
                {
                    return Backup {
                        status: "failed",
                        dir: None,
                        detail: e,
                    };
                }
                return Backup {
                    status: "saved",
                    dir: Some(dir),
                    detail: if net == Network::Meshtastic {
                        "Meshtastic configuration saved".to_string()
                    } else {
                        format!("{} companion info saved (identity and radio details; not a restorable configuration export)", net.display())
                    },
                };
            }
            Ok(r) => notes.push(format!(
                "{}: no usable answer ({})",
                net.display(),
                r.tail()
            )),
            Err(e) => notes.push(format!("{}: {e}", net.display())),
        }
    }
    none(&format!(
        "no backup possible: no supported firmware answered on the port ({})",
        notes.join("; ")
    ))
}

fn execute(ctx: &Ctx, req: &Request, ev: Eval) -> Value {
    let mut steps: Vec<Value> = vec![];
    let start = json!({"event":"flash-start","actor":req.actor,"device":ev.device.id,"board":ev.board.id,
        "entry":ev.entry.id,"network":ev.entry.network,"version":ev.entry.version,"sha256":ev.entry.source_sha256,
        "confirmed_by_typing_board_name":true});
    if let Err(e) = log(ctx, start) {
        return json!({"ok":false,"stage":"refused","code":"log_unavailable","message":format!("the flash log cannot be written, so nothing was flashed: {e}")});
    }
    let fail = |stage: &str, msg: String, steps: &Vec<Value>, backup: &Backup| -> Value {
        let _ = log(
            ctx,
            json!({"event":"flash-end","actor":req.actor,"device":ev.device.id,"entry":ev.entry.id,"ok":false,"stage":stage,"message":msg}),
        );
        json!({"ok":false,"stage":stage,"message":msg,"steps":steps,
            "backup":{"status":backup.status,"detail":backup.detail,"dir":backup.dir.as_ref().map(|d| d.display().to_string())},
            "recovery": format!("{} {}", ev.board.recovery, boards::GENERIC_RECOVERY)})
    };

    // 1. back up
    let backup = take_backup(ctx, &ev);
    steps.push(json!({"name":"backup","ok":backup.status != "failed","status":backup.status,"detail":backup.detail}));
    if backup.status == "failed"
        || (backup.status == "not_possible" && ev.device.kind == "serial" && !req.allow_no_backup)
    {
        let msg = if backup.status == "failed" {
            format!(
                "the backup failed, so nothing was flashed: {}",
                backup.detail
            )
        } else {
            format!("{}. Nothing was flashed. Repeat with allow_no_backup to continue without a backup.", backup.detail)
        };
        return fail("backup", msg, &steps, &backup);
    }

    // 2. flash (optionally after a full erase, for a clean switch between networks)
    if req.erase {
        if ev.entry.method != "esptool" {
            steps.push(json!({"name":"erase","ok":true,"detail":"skipped: only ESP32 boards are erased; a UF2 or DFU update keeps its own settings area"}));
        } else {
            let tool = tools::resolve(Tool::Esptool).unwrap_or_default();
            let eargv: Vec<String> = [
                tool.as_str(),
                "--chip",
                ev.board.chip,
                "--port",
                ev.device.port.as_deref().unwrap_or(""),
                "erase_flash",
            ]
            .iter()
            .map(|s| s.to_string())
            .collect();
            match ctx.runner.run(&eargv, Duration::from_secs(300)) {
                Ok(r) if r.ok() => steps.push(json!({"name":"erase","ok":true,"argv":eargv})),
                Ok(r) => {
                    steps.push(json!({"name":"erase","ok":false,"argv":eargv,"output":r.tail()}));
                    return fail(
                        "erase",
                        format!("the erase failed, nothing was written: {}", r.tail()),
                        &steps,
                        &backup,
                    );
                }
                Err(e) => return fail("erase", e, &steps, &backup),
            }
        }
    }
    match &ev.action {
        Action::Argv(argv) => {
            match ctx.runner.run(argv, Duration::from_secs(900)) {
                Ok(r) if r.ok() => steps.push(json!({"name":"flash","ok":true,"argv":argv})),
                Ok(r) => {
                    steps.push(json!({"name":"flash","ok":false,"argv":argv,"exit":r.code,"output":r.tail()}));
                    return fail(
                        "flash",
                        format!("the flash tool failed: {}", r.tail()),
                        &steps,
                        &backup,
                    );
                }
                Err(e) => {
                    steps.push(json!({"name":"flash","ok":false,"argv":argv,"error":e}));
                    return fail("flash", e, &steps, &backup);
                }
            }
        }
        Action::Uf2Copy { src, dest } => match copy_synced(src, dest) {
            Ok(_) => {
                steps.push(json!({"name":"flash","ok":true,"copied_to":dest.display().to_string()}))
            }
            Err(e) => {
                // A UF2 bootloader reboots as soon as the last block lands, which can cut the copy off.
                let gone = dest.parent().is_some_and(|m| !m.exists());
                if gone {
                    steps.push(json!({"name":"flash","ok":true,"note":"the drive disappeared at the end of the copy, which is normal"}));
                } else {
                    steps.push(json!({"name":"flash","ok":false,"error":e.to_string()}));
                    return fail(
                        "flash",
                        format!("copy to the bootloader drive failed: {e}"),
                        &steps,
                        &backup,
                    );
                }
            }
        },
    }

    // 3. verify: the board must come back and answer
    let (vstatus, vdetail, port) = verify(ctx, &ev);
    steps.push(
        json!({"name":"verify","ok":vstatus == "confirmed","status":vstatus,"detail":vdetail}),
    );

    // 4. restore the settings that carry across
    let mut restored = json!({"status":"not_applicable","detail":"nothing to restore"});
    let from_dir = match &req.restore_from {
        Some(id) => Some(ctx.paths.backups().join(id)),
        None => backup.dir.clone(),
    };
    if let (Some(dir), Some(port)) = (from_dir, port.clone()) {
        restored = restore(ctx, &ev, &dir, &port, vstatus == "confirmed");
    } else if backup.status == "saved" {
        restored = json!({"status":"skipped","detail":"the board did not come back, so nothing was restored; the backup is kept"});
    }
    steps.push(json!({"name":"restore","detail":restored}));

    let ok = vstatus != "failed";
    let _ = log(
        ctx,
        json!({"event":"flash-end","actor":req.actor,"device":ev.device.id,"entry":ev.entry.id,"ok":ok,
        "verify":vstatus,"backup":backup.status}),
    );
    let mut out = json!({"ok":ok,"stage":"done","steps":steps,
        "backup":{"status":backup.status,"detail":backup.detail,"dir":backup.dir.as_ref().map(|d| d.display().to_string())},
        "verify":{"status":vstatus,"detail":vdetail},"restore":restored});
    if !ok {
        out["recovery"] = format!("{} {}", ev.board.recovery, boards::GENERIC_RECOVERY).into();
        out["message"] = "the image was written but the board did not answer afterwards".into();
    }
    out
}

fn wait_for_port(ctx: &Ctx, ev: &Eval) -> Option<String> {
    let deadline = std::time::Instant::now() + ctx.verify_wait;
    loop {
        let devs = detect::detect(ctx.cfg, ctx.runner, false);
        let found = devs
            .iter()
            .filter(|d| d.kind == "serial" && d.port.is_some())
            .find(|d| d.id == ev.device.id);
        if let Some(d) = found {
            return d.port.clone();
        }
        // A UF2 board comes back as a serial port with a new id: accept it only if it is the one such board.
        let same_board: Vec<_> = devs
            .iter()
            .filter(|d| d.kind == "serial" && d.candidates.contains(&ev.board.id.to_string()))
            .collect();
        if ev.device.kind == "uf2" && same_board.len() == 1 {
            return same_board[0].port.clone();
        }
        if std::time::Instant::now() >= deadline {
            return None;
        }
        std::thread::sleep(Duration::from_millis(200));
    }
}

fn verify(ctx: &Ctx, ev: &Eval) -> (&'static str, String, Option<String>) {
    let Some(port) = wait_for_port(ctx, ev) else {
        return (
            "unconfirmed",
            "the board did not show up as a serial port afterwards".into(),
            None,
        );
    };
    let net = Network::parse(&ev.entry.network);
    type ArgvFn = fn(&str, &str) -> Vec<String>;
    let (tool, argv_of): (Tool, ArgvFn) = match net {
        Some(Network::Meshtastic) => (Tool::Meshtastic, meshtastic_info_argv),
        Some(Network::Meshcore) => (Tool::MeshcoreCli, meshcore_info_argv),
        _ => (Tool::Rnodeconf, rnode_info_argv),
    };
    let Ok(t) = tools::resolve(tool) else {
        return (
            "unconfirmed",
            format!(
                "the board is back on {port}, but {} is not installed to ask it",
                tool.install_hint()
            ),
            Some(port),
        );
    };
    match ctx.runner.run(&argv_of(&t, &port), Duration::from_secs(60)) {
        Ok(r) if r.ok() && handshake_matches(net, &r, &ev.entry.version) => (
            "confirmed",
            format!("{} answered on {port}", ev.entry.network),
            Some(port),
        ),
        Ok(r) if r.ok() => (
            "failed",
            format!(
                "the board answered but not as {} {}: {}",
                ev.entry.network,
                ev.entry.version,
                r.tail()
            ),
            Some(port),
        ),
        Ok(r) => ("failed", format!("no handshake: {}", r.tail()), Some(port)),
        Err(e) => ("failed", e, Some(port)),
    }
}

fn handshake_matches(net: Option<Network>, r: &RunResult, version: &str) -> bool {
    match net {
        // The Meshtastic version looks like 2.7.26.54e0d8d; the device reports 2.7.26.54e0d8d too.
        Some(Network::Meshtastic) => {
            let core = version.split('.').take(3).collect::<Vec<_>>().join(".");
            r.stdout.contains(&core) || r.stderr.contains(&core)
        }
        _ => !r.stdout.trim().is_empty(),
    }
}

fn restore(ctx: &Ctx, ev: &Eval, dir: &Path, port: &str, up: bool) -> Value {
    let meta: Value = fs::read(dir.join("backup.json"))
        .ok()
        .and_then(|b| serde_json::from_slice(&b).ok())
        .unwrap_or(Value::Null);
    let bnet = meta["network"].as_str().unwrap_or("");
    if bnet != ev.entry.network {
        return json!({"status":"kept_for_switching_back","detail":format!(
            "the saved configuration is for {bnet}; settings do not carry from {bnet} to {} automatically. It is kept at {} for switching back.",
            ev.entry.network, dir.display())});
    }
    if !up {
        return json!({"status":"skipped","detail":"the board is not confirmed up, so nothing was restored; the backup is kept"});
    }
    if bnet != "meshtastic" {
        return json!({"status":"kept_for_switching_back","detail":"restore for this network is not implemented; the backup is kept"});
    }
    let Some(file) = meta["file"].as_str() else {
        return json!({"status":"failed","detail":"the backup record has no file"});
    };
    let Ok(tool) = tools::resolve(Tool::Meshtastic) else {
        return json!({"status":"failed","detail":"the Meshtastic CLI is not installed"});
    };
    let argv = meshtastic_configure_argv(&tool, port, &dir.join(file).display().to_string());
    match ctx.runner.run(&argv, Duration::from_secs(180)) {
        Ok(r) if r.ok() => json!({"status":"restored","argv":argv}),
        Ok(r) => json!({"status":"failed","detail":r.tail(),"argv":argv}),
        Err(e) => json!({"status":"failed","detail":e}),
    }
}

pub fn list_backups(p: &Paths) -> Vec<Value> {
    let mut out = vec![];
    if let Ok(rd) = fs::read_dir(p.backups()) {
        for e in rd.flatten() {
            if let Some(v) = fs::read(e.path().join("backup.json"))
                .ok()
                .and_then(|b| serde_json::from_slice::<Value>(&b).ok())
            {
                out.push(v);
            }
        }
    }
    out.sort_by(|a, b| a["id"].as_str().cmp(&b["id"].as_str()));
    out
}

/// Library entries whose board matches the device, with whether they can be flashed right now.
pub fn compatible(p: &Paths, d: &Device) -> Result<Vec<Value>, String> {
    let man = Manifest::load(p)?;
    Ok(man
        .entries
        .iter()
        .filter(|e| d.candidates.contains(&e.board))
        .map(|e| {
            let has_dfu = e.files.iter().any(|f| f.role == "dfu-package");
            let usable = match (e.method.as_str(), d.kind.as_str()) {
                ("esptool", "serial") => d.probe == "ok",
                ("uf2", "uf2") => true,
                ("uf2" | "nrfutil", "serial") => has_dfu,
                _ => false,
            };
            let mut v = serde_json::to_value(e).unwrap_or(Value::Null);
            v["usable_now"] = usable.into();
            v
        })
        .collect())
}
