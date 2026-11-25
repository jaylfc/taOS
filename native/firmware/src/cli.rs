// SPDX-License-Identifier: AGPL-3.0-or-later
//! Command line: hand written, JSON out. Exit code 0 on success, 1 on failure.
use crate::api;
use crate::boards::{Network, BOARDS};
use crate::detect;
use crate::flash::{self, Ctx, Request};
use crate::library::{self, RefreshOpts};
use crate::net::Curl;
use crate::store::{self, Consent, Licences, Manifest, Paths};
use crate::tools::System;
use serde_json::{json, Value};

pub const USAGE: &str = "usage: taos-firmware [--json] <command>
  boards                                  supported boards and networks
  library [--board B] [--network N]       what is stored
  refresh [--network N] [--board B] [--quiet-offline] [--force]
  licence show [NETWORK] | licence accept NETWORK --by NAME
  devices [--probe]                       detect connected boards (--probe asks ESP32 boards for their chip, which resets them)
  compatible --device ID [--probe]       firmware that fits a device
  flash plan --device ID --entry ID
  flash run --device ID --entry ID --confirm \"BOARD NAME\" [--actor person|agent:ID] [--allow-no-backup] [--erase] [--restore-from BACKUP]
  consent grant --agent ID --by NAME [--minutes N] | consent revoke --agent ID | consent list
  backups | log [--limit N]
  serve [--port N]                        local JSON API on 127.0.0.1 (default 7421)";

struct Args {
    rest: Vec<String>,
    json: bool,
}

fn flag(a: &[String], name: &str) -> Option<String> {
    a.iter()
        .position(|x| x == name)
        .and_then(|i| a.get(i + 1))
        .cloned()
}
fn has(a: &[String], name: &str) -> bool {
    a.iter().any(|x| x == name)
}

fn out(json_mode: bool, ok: bool, v: Value, human: Option<String>) -> i32 {
    if json_mode || human.is_none() {
        println!(
            "{}",
            if json_mode {
                v.to_string()
            } else {
                serde_json::to_string_pretty(&v).unwrap_or_default()
            }
        );
    } else if let Some(h) = human {
        println!("{h}");
    }
    i32::from(!ok)
}

fn fail(json_mode: bool, msg: &str) -> i32 {
    out(
        json_mode,
        false,
        json!({"ok":false,"error":msg}),
        Some(format!("error: {msg}")),
    )
}

pub fn run(argv: Vec<String>) -> i32 {
    let mut a = Args {
        rest: vec![],
        json: false,
    };
    for x in argv {
        if x == "--json" {
            a.json = true;
        } else {
            a.rest.push(x);
        }
    }
    let j = a.json;
    let Some(cmd) = a.rest.first().cloned() else {
        println!("{USAGE}");
        return 1;
    };
    if cmd == "help" || cmd == "--help" || cmd == "-h" {
        println!("{USAGE}");
        return 0;
    }
    let paths = match Paths::from_env() {
        Ok(p) => p,
        Err(e) => return fail(j, &e),
    };
    let r = &a.rest[1..];
    let sys = System;
    let cfg = detect::Cfg::from_env();
    let ctx = Ctx {
        paths: &paths,
        runner: &sys,
        cfg: &cfg,
        verify_wait: api::verify_wait_from_env(),
    };
    match cmd.as_str() {
        "boards" => {
            let b: Vec<Value> = BOARDS
                .iter()
                .map(|b| {
                    json!({"id":b.id,"name":b.name,"family":b.family,"chip":b.chip,
                    "networks":b.networks().iter().map(|n| n.id()).collect::<Vec<_>>()})
                })
                .collect();
            let human = BOARDS
                .iter()
                .map(|b| format!("{:16} {}", b.id, b.name))
                .collect::<Vec<_>>()
                .join("\n");
            out(j, true, json!({"ok":true,"boards":b}), Some(human))
        }
        "library" => match Manifest::load(&paths) {
            Ok(m) => {
                let e: Vec<_> = m
                    .entries
                    .iter()
                    .filter(|e| {
                        flag(r, "--board").is_none_or(|b| b == e.board)
                            && flag(r, "--network").is_none_or(|n| n == e.network)
                    })
                    .collect();
                let human = e
                    .iter()
                    .map(|e| {
                        format!(
                            "{:44} {:22} {}",
                            e.id,
                            e.verification,
                            e.files.first().map(|f| f.path.as_str()).unwrap_or("")
                        )
                    })
                    .collect::<Vec<_>>()
                    .join("\n");
                out(j, true, json!({"ok":true,"entries":e}), Some(human))
            }
            Err(e) => fail(j, &e),
        },
        "refresh" => {
            let f = match Curl::from_env(paths.tmp()) {
                Ok(f) => f,
                Err(e) => return fail(j, &e),
            };
            let net = flag(r, "--network");
            if net.as_deref().is_some_and(|n| Network::parse(n).is_none()) {
                return fail(j, "network must be meshtastic, meshcore or rnode");
            }
            let o = RefreshOpts {
                force: has(r, "--force"),
                network: net.as_deref().and_then(Network::parse),
                board: flag(r, "--board"),
            };
            let reps = library::refresh(&paths, &f, &o);
            let quiet = has(r, "--quiet-offline");
            // Offline, rate limited and licence-pending are normal states for the timer, not failures.
            let ok = reps.iter().all(|x| {
                x.status == "ok"
                    || x.status == "licence_required"
                    || (quiet && (x.status == "offline" || x.status == "rate_limited"))
            });
            let human = reps
                .iter()
                .map(|x| {
                    format!(
                        "{:11} {:16} fetched {} kept {} {}",
                        x.network,
                        x.status,
                        x.fetched.len(),
                        x.skipped,
                        x.detail
                    )
                })
                .collect::<Vec<_>>()
                .join("\n");
            out(j, ok, json!({"ok":ok,"reports":reps}), Some(human))
        }
        "licence" => match r.first().map(String::as_str) {
            Some("show") => {
                let l = match Licences::load(&paths) {
                    Ok(l) => l,
                    Err(e) => return fail(j, &e),
                };
                let nets: Vec<Network> = match r.get(1) {
                    Some(n) => match Network::parse(n) {
                        Some(n) => vec![n],
                        None => return fail(j, "unknown network"),
                    },
                    None => crate::boards::NETWORKS.to_vec(),
                };
                let v: Vec<Value> = nets
                    .iter()
                    .map(|n| json!({"network":n.id(),"licence_id":n.licence_id(),"licence_url":n.licence_url(),"accepted":l.get(n.id())}))
                    .collect();
                out(j, true, json!({"ok":true,"licences":v}), None)
            }
            Some("accept") => {
                let Some(n) = r.get(1).and_then(|n| Network::parse(n)) else {
                    return fail(j, "name the network: meshtastic, meshcore or rnode");
                };
                match Licences::accept(&paths, n, &flag(r, "--by").unwrap_or_default(), "") {
                    Ok((acc, new)) => out(
                        j,
                        true,
                        json!({"ok":true,"newly_accepted":new,"acceptance":acc}),
                        None,
                    ),
                    Err(e) => fail(j, &e),
                }
            }
            _ => fail(
                j,
                "licence show [NETWORK] | licence accept NETWORK --by NAME",
            ),
        },
        "devices" => {
            let d = detect::detect(&cfg, &sys, has(r, "--probe"));
            let human = if d.is_empty() {
                "no boards found".to_string()
            } else {
                d.iter()
                    .map(|d| {
                        format!(
                            "{:34} {:7} {} probe={} candidates={}",
                            d.id,
                            d.kind,
                            d.port.as_deref().or(d.mount.as_deref()).unwrap_or(""),
                            d.probe,
                            d.candidates.join(",")
                        )
                    })
                    .collect::<Vec<_>>()
                    .join("\n")
            };
            out(j, true, json!({"ok":true,"devices":d}), Some(human))
        }
        "compatible" => {
            let Some(id) = flag(r, "--device") else {
                return fail(j, "--device ID is required");
            };
            let devs = detect::detect(&cfg, &sys, has(r, "--probe"));
            let Some(d) = devs.iter().find(|d| d.id == id) else {
                return fail(j, "no such device");
            };
            match flash::compatible(&paths, d) {
                Ok(v) => out(j, true, json!({"ok":true,"device":d,"firmware":v}), None),
                Err(e) => fail(j, &e),
            }
        }
        "flash" => {
            let (sub, rr) = match r.first().map(String::as_str) {
                Some("plan") => ("plan", &r[1..]),
                Some("run") => ("run", &r[1..]),
                _ => return fail(j, "flash plan|run --device ID --entry ID ..."),
            };
            let (Some(device), Some(entry)) = (flag(rr, "--device"), flag(rr, "--entry")) else {
                return fail(j, "--device and --entry are required");
            };
            if sub == "plan" {
                return match flash::plan(&ctx, &device, &entry) {
                    Ok(p) => out(j, true, json!({"ok":true,"plan":p}), None),
                    Err(e) => out(
                        j,
                        false,
                        json!({"ok":false,"code":e.code,"message":e.message}),
                        Some(format!("refused ({}): {}", e.code, e.message)),
                    ),
                };
            }
            let req = Request {
                device_id: device,
                entry_id: entry,
                confirm: flag(rr, "--confirm"),
                actor: flag(rr, "--actor").unwrap_or_else(|| "person".into()),
                allow_no_backup: has(rr, "--allow-no-backup"),
                erase: has(rr, "--erase"),
                restore_from: flag(rr, "--restore-from"),
            };
            let v = flash::run(&ctx, &req);
            let ok = v["ok"].as_bool().unwrap_or(false);
            out(j, ok, v, None)
        }
        "consent" => match r.first().map(String::as_str) {
            Some("grant") => match Consent::grant(
                &paths,
                &flag(r, "--agent").unwrap_or_default(),
                &flag(r, "--by").unwrap_or_default(),
                flag(r, "--minutes")
                    .and_then(|m| m.parse().ok())
                    .unwrap_or(30),
            ) {
                Ok(g) => out(j, true, json!({"ok":true,"grant":g}), None),
                Err(e) => fail(j, &e),
            },
            Some("revoke") => {
                match Consent::revoke(&paths, &flag(r, "--agent").unwrap_or_default()) {
                    Ok(c) => out(j, true, json!({"ok":true,"revoked":c}), None),
                    Err(e) => fail(j, &e),
                }
            }
            Some("list") => match Consent::load(&paths) {
                Ok(c) => out(j, true, json!({"ok":true,"grants":c.grants}), None),
                Err(e) => fail(j, &e),
            },
            _ => fail(j, "consent grant|revoke|list"),
        },
        "backups" => out(
            j,
            true,
            json!({"ok":true,"backups":flash::list_backups(&paths)}),
            None,
        ),
        "log" => {
            let n = flag(r, "--limit")
                .and_then(|v| v.parse().ok())
                .unwrap_or(50);
            out(
                j,
                true,
                json!({"ok":true,"log":store::read_audit(&paths, n)}),
                None,
            )
        }
        "serve" => {
            let port = flag(r, "--port")
                .and_then(|p| p.parse().ok())
                .unwrap_or(7421);
            match api::serve(paths, port) {
                Ok(()) => 0,
                Err(e) => fail(j, &e),
            }
        }
        _ => {
            println!("{USAGE}");
            1
        }
    }
}
