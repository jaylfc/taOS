// SPDX-License-Identifier: AGPL-3.0-or-later
//! Test fixtures: a fake GitHub, fake USB/serial/UF2 trees and fake vendor tools. Nothing here
//! opens a real serial device, and every path the program reads is redirected by environment.
#![allow(dead_code)]
use serde_json::{json, Value};
use sha2::{Digest, Sha256};
use std::collections::HashMap;
use std::fs;
use std::io::{Read, Write};
use std::net::TcpListener;
use std::path::{Path, PathBuf};
use std::process::Command;
use std::sync::atomic::{AtomicU32, Ordering};
use std::sync::{Arc, Mutex};

static N: AtomicU32 = AtomicU32::new(0);

pub fn sha(b: &[u8]) -> String {
    Sha256::digest(b)
        .iter()
        .map(|x| format!("{x:02x}"))
        .collect()
}

pub fn zip_bytes(files: &[(&str, Vec<u8>)]) -> Vec<u8> {
    use std::io::Cursor;
    let mut w = zip::ZipWriter::new(Cursor::new(vec![]));
    let o =
        zip::write::SimpleFileOptions::default().compression_method(zip::CompressionMethod::Stored);
    for (n, d) in files {
        w.start_file(*n, o).unwrap();
        w.write_all(d).unwrap();
    }
    w.finish().unwrap().into_inner()
}

#[derive(Clone)]
pub struct Route {
    pub status: u16,
    pub body: Vec<u8>,
    pub headers: Vec<(String, String)>,
}

type Hits = Arc<Mutex<Vec<(String, Option<String>)>>>;

pub struct Server {
    pub base: String,
    pub routes: Arc<Mutex<HashMap<String, Route>>>,
    /// (path with query, If-None-Match)
    pub hits: Hits,
}

impl Server {
    pub fn start() -> Server {
        let l = TcpListener::bind("127.0.0.1:0").unwrap();
        let base = format!("http://127.0.0.1:{}", l.local_addr().unwrap().port());
        let routes: Arc<Mutex<HashMap<String, Route>>> = Arc::default();
        let hits: Hits = Arc::default();
        let (r2, h2) = (routes.clone(), hits.clone());
        std::thread::spawn(move || {
            for s in l.incoming().flatten() {
                let (r2, h2) = (r2.clone(), h2.clone());
                std::thread::spawn(move || {
                    let mut s = s;
                    let mut buf = vec![0u8; 8192];
                    let n = s.read(&mut buf).unwrap_or(0);
                    let req = String::from_utf8_lossy(&buf[..n]).into_owned();
                    let path = req.split_whitespace().nth(1).unwrap_or("").to_string();
                    let inm = req.lines().find_map(|l| {
                        l.to_ascii_lowercase()
                            .starts_with("if-none-match:")
                            .then(|| l.split_once(':').unwrap().1.trim().to_string())
                    });
                    h2.lock().unwrap().push((path.clone(), inm.clone()));
                    let route = r2.lock().unwrap().get(&path).cloned();
                    let (status, body, headers) = match route {
                        None => (404, b"{}".to_vec(), vec![]),
                        Some(r) => {
                            let etag = r
                                .headers
                                .iter()
                                .find(|(k, _)| k == "ETag")
                                .map(|(_, v)| v.clone());
                            if etag.is_some() && etag == inm {
                                (304, vec![], r.headers.clone())
                            } else {
                                (r.status, r.body.clone(), r.headers.clone())
                            }
                        }
                    };
                    let mut head = format!(
                        "HTTP/1.1 {status} X\r\nContent-Length: {}\r\nConnection: close\r\n",
                        body.len()
                    );
                    for (k, v) in headers {
                        head += &format!("{k}: {v}\r\n");
                    }
                    let _ = s.write_all(head.as_bytes());
                    let _ = s.write_all(b"\r\n");
                    let _ = s.write_all(&body);
                });
            }
        });
        Server { base, routes, hits }
    }
    pub fn set(&self, path: &str, status: u16, body: Vec<u8>, headers: Vec<(&str, &str)>) {
        self.routes.lock().unwrap().insert(
            path.into(),
            Route {
                status,
                body,
                headers: headers
                    .into_iter()
                    .map(|(a, b)| (a.into(), b.into()))
                    .collect(),
            },
        );
    }
    pub fn count(&self, prefix: &str) -> usize {
        self.hits
            .lock()
            .unwrap()
            .iter()
            .filter(|(p, _)| p.starts_with(prefix))
            .count()
    }
}

/// An asset served under /dl/<name>, with the digest GitHub would list for it.
fn asset(srv: &Server, name: &str, data: Vec<u8>, digest: bool) -> Value {
    asset_in(srv, "x", name, data, digest)
}

fn asset_in(srv: &Server, dir: &str, name: &str, data: Vec<u8>, digest: bool) -> Value {
    srv.set(&format!("/dl/{dir}/{name}"), 200, data.clone(), vec![]);
    let mut v = json!({"name":name,"browser_download_url":format!("{}/dl/{dir}/{name}",srv.base),"size":data.len()});
    if digest {
        v["digest"] = format!("sha256:{}", sha(&data)).into();
    }
    v
}

pub struct Mesh {
    pub versions: Vec<&'static str>,
}

fn blob(tag: &str) -> Vec<u8> {
    format!("firmware bytes for {tag}").into_bytes()
}

/// Publish fake Meshtastic, MeshCore and RNode releases on the fake server.
/// `corrupt` names an asset whose listed digest is made wrong.
pub fn publish_all(srv: &Server, corrupt: Option<&str>) {
    let mt_versions = ["2.7.26.54e0d8d", "2.7.25.104df5f"];
    let mut rels =
        vec![json!({"tag_name":"v2.8.2.d5f1619","draft":false,"prerelease":true,"assets":[]})];
    for v in mt_versions {
        let mt = |mcu: &str| {
            json!({"mcu":mcu,"part":[{"subtype":"ota_1","offset":"0x340000"},{"subtype":"spiffs","offset":"0x670000"}]})
                .to_string()
                .into_bytes()
        };
        // The older release has no .mt.json flash map, like the real v2.7.15.
        let legacy = v == mt_versions[1];
        let esp = |slug: &str, mcu: &str| -> Vec<(String, Vec<u8>)> {
            if legacy {
                return vec![
                    (
                        format!("firmware-{slug}-{v}.bin"),
                        blob(&format!("mt {slug} {v} full image")),
                    ),
                    (
                        format!("littlefs-{slug}-{v}.bin"),
                        blob(&format!("mt {slug} {v} littlefs")),
                    ),
                ];
            }
            vec![
                (format!("firmware-{slug}-{v}.mt.json"), mt(mcu)),
                (
                    format!("firmware-{slug}-{v}.factory.bin"),
                    blob(&format!("mt {slug} {v} factory")),
                ),
                (
                    format!("littlefs-{slug}-{v}.bin"),
                    blob(&format!("mt {slug} {v} littlefs")),
                ),
            ]
        };
        let mut s3 = vec![];
        for slug in ["heltec-v3", "heltec-v4", "tbeam-s3-core"] {
            s3.extend(esp(slug, "esp32s3"));
        }
        s3.push(("mt-esp32s3-ota.bin".to_string(), blob("mt ota s3")));
        let mut e32 = esp("tbeam", "esp32");
        e32.push(("mt-esp32-ota.bin".into(), blob("mt ota esp32")));
        let mut nrf = vec![];
        for slug in ["rak4631", "tracker-t1000-e", "seeed_xiao_nrf52840_kit"] {
            nrf.push((
                format!("firmware-{slug}-{v}.uf2"),
                blob(&format!("mt {slug} {v} uf2")),
            ));
            nrf.push((
                format!("firmware-{slug}-{v}-ota.zip"),
                blob(&format!("mt {slug} {v} ota")),
            ));
        }
        let mut assets = vec![];
        for (mcu, files) in [("esp32", e32), ("esp32s3", s3), ("nrf52840", nrf)] {
            let refs: Vec<(&str, Vec<u8>)> =
                files.iter().map(|(n, d)| (n.as_str(), d.clone())).collect();
            let name = format!("firmware-{mcu}-{v}.zip");
            assets.push(asset(srv, &name, zip_bytes(&refs), true));
        }
        rels.push(
            json!({"tag_name":format!("v{v}"),"draft":false,"prerelease":false,"assets":assets}),
        );
    }
    srv.set(
        "/repos/meshtastic/firmware/releases?per_page=30",
        200,
        Value::Array(rels).to_string().into_bytes(),
        vec![("ETag", "\"mt1\""), ("X-RateLimit-Remaining", "50")],
    );

    // MeshCore
    let mut rels =
        vec![json!({"tag_name":"repeater-v1.18.0","draft":false,"prerelease":false,"assets":[]})];
    for v in ["1.18.0", "1.17.1"] {
        let mut assets = vec![];
        for (prefix, esp) in [
            ("Heltec_v3", true),
            ("heltec_v4", true),
            ("Tbeam_SX1262", true),
            ("T_Beam_S3_Supreme_SX1262", true),
            ("RAK_4631", false),
            ("t1000e", false),
            ("Xiao_nrf52", false),
        ] {
            for var in ["ble", "usb"] {
                let stem = format!("{prefix}_companion_radio_{var}-v{v}-81dbae5");
                if esp {
                    assets.push(asset(
                        srv,
                        &format!("{stem}-merged.bin"),
                        blob(&format!("mc {stem} merged")),
                        true,
                    ));
                } else {
                    assets.push(asset(
                        srv,
                        &format!("{stem}.uf2"),
                        blob(&format!("mc {stem} uf2")),
                        true,
                    ));
                    assets.push(asset(
                        srv,
                        &format!("{stem}.zip"),
                        blob(&format!("mc {stem} zip")),
                        true,
                    ));
                }
            }
        }
        rels.push(json!({"tag_name":format!("companion-v{v}"),"draft":false,"prerelease":false,"assets":assets}));
    }
    srv.set(
        "/repos/meshcore-dev/MeshCore/releases?per_page=30",
        200,
        Value::Array(rels).to_string().into_bytes(),
        vec![],
    );

    publish_rnode(srv, &["1.86", "1.85"], corrupt);

    if let Some(name) = corrupt {
        // Make the host digest for a Meshtastic or MeshCore asset wrong.
        for p in [
            "/repos/meshtastic/firmware/releases?per_page=30",
            "/repos/meshcore-dev/MeshCore/releases?per_page=30",
        ] {
            let mut r = srv.routes.lock().unwrap().get(p).cloned().unwrap();
            let mut v: Value = serde_json::from_slice(&r.body).unwrap();
            for rel in v.as_array_mut().unwrap() {
                for a in rel["assets"].as_array_mut().unwrap() {
                    if a["name"] == name {
                        a["digest"] = format!("sha256:{}", "f".repeat(64)).into();
                    }
                }
            }
            r.body = v.to_string().into_bytes();
            srv.routes.lock().unwrap().insert(p.into(), r);
        }
    }
}

pub fn publish_rnode(srv: &Server, versions: &[&str], corrupt: Option<&str>) {
    let mut rels = vec![];
    for &v in versions {
        let mut assets = vec![];
        let mut hashes = serde_json::Map::new();
        for (slug, esp) in [
            ("heltec32v3", true),
            ("heltec32v4pa", true),
            ("tbeam_sx1262", true),
            ("tbeam_supreme", true),
            ("rak4631", false),
        ] {
            let name = format!("rnode_firmware_{slug}.zip");
            let data = if esp {
                zip_bytes(&[
                    ("esptool.py", b"ignored".to_vec()),
                    ("console_image.bin", blob("rn console")),
                    (
                        &format!("rnode_firmware_{slug}.boot_app0"),
                        blob(&format!("rn {slug} {v} boot_app0")),
                    ),
                    (
                        &format!("rnode_firmware_{slug}.bin"),
                        blob(&format!("rn {slug} {v} app")),
                    ),
                    (
                        &format!("rnode_firmware_{slug}.bootloader"),
                        blob(&format!("rn {slug} {v} bl")),
                    ),
                    (
                        &format!("rnode_firmware_{slug}.partitions"),
                        blob(&format!("rn {slug} {v} parts")),
                    ),
                ])
            } else {
                zip_bytes(&[
                    ("manifest.json", b"{}".to_vec()),
                    (&format!("rnode_firmware_{slug}.bin"), blob("rn nrf")),
                ])
            };
            let mut h = sha(&data);
            if corrupt == Some(&name) && v == versions[0] {
                h = "0".repeat(64);
            }
            hashes.insert(name.clone(), json!({"hash":h,"version":v}));
            // The host digest is left out so the publisher checksum is what is checked.
            assets.push(asset_in(srv, v, &name, data, false));
        }
        let body = Value::Object(hashes).to_string().into_bytes();
        srv.set(&format!("/dl/rj/{v}"), 200, body.clone(), vec![]);
        assets.push(json!({"name":"release.json","browser_download_url":format!("{}/dl/rj/{v}",srv.base),"size":body.len()}));
        rels.push(json!({"tag_name":v,"draft":false,"prerelease":false,"assets":assets}));
    }
    srv.set(
        "/repos/markqvist/RNode_Firmware/releases?per_page=30",
        200,
        Value::Array(rels).to_string().into_bytes(),
        vec![],
    );
}

pub struct Env {
    pub root: PathBuf,
    pub data: PathBuf,
    pub usb: PathBuf,
    pub dev: PathBuf,
    pub uf2: PathBuf,
    pub log: PathBuf,
    pub tools: PathBuf,
    pub api: String,
}

impl Env {
    pub fn new(api: &str) -> Env {
        let root = PathBuf::from(env!("CARGO_TARGET_TMPDIR")).join(format!(
            "fw-{}-{}",
            std::process::id(),
            N.fetch_add(1, Ordering::SeqCst)
        ));
        let _ = fs::remove_dir_all(&root);
        let e = Env {
            data: root.join("data"),
            usb: root.join("usb"),
            dev: root.join("dev"),
            uf2: root.join("uf2"),
            log: root.join("tools.log"),
            tools: root.join("tools"),
            root,
            api: api.into(),
        };
        for d in [&e.data, &e.usb, &e.dev, &e.uf2, &e.tools] {
            fs::create_dir_all(d).unwrap();
        }
        fs::write(&e.log, "").unwrap();
        e.install_tools();
        e
    }
    fn script(&self, name: &str, body: &str) {
        use std::os::unix::fs::PermissionsExt;
        let p = self.tools.join(name);
        fs::write(
            &p,
            format!("#!/bin/bash\nprintf '%s\\n' \"{name} $*\" >> \"$FAKE_LOG\"\n{body}\n"),
        )
        .unwrap();
        fs::set_permissions(&p, fs::Permissions::from_mode(0o755)).unwrap();
    }
    fn install_tools(&self) {
        // esptool: chip_id answers with FAKE_CHIP; write_flash exits with FAKE_FLASH_EXIT.
        self.script("esptool", r#"case "$*" in
 *chip_id*) echo "Chip is ${FAKE_CHIP:-ESP32-S3 (QFN56) (revision v0.2)}"; [ -n "$FAKE_PROBE_FAIL" ] && exit 2; exit 0;;
 *erase_flash*) echo "Erasing..."; exit ${FAKE_ERASE_EXIT:-0};;
 *write_flash*) echo "Writing..."; sleep ${FAKE_FLASH_SLEEP:-0}; exit ${FAKE_FLASH_EXIT:-0};;
esac; exit 3"#);
        self.script("meshtastic", r#"case "$*" in
 *--export-config*) [ -n "$FAKE_MT_NO_BACKUP" ] && { echo "no answer" >&2; exit 1; }; printf 'owner: Fake\nlora:\n  region: EU_868\n'; exit 0;;
 *--info*) echo "Firmware version: ${FAKE_MT_VERSION:-2.7.26.54e0d8d}"; exit 0;;
 *--configure*) echo configured; exit 0;;
esac; exit 3"#);
        self.script("nrfutil", r#"exit ${FAKE_FLASH_EXIT:-0}"#);
        self.script(
            "meshcore-cli",
            r#"[ -n "$FAKE_MC_NO_BACKUP" ] && exit 1; echo "name: fake companion"; exit 0"#,
        );
        self.script("rnodeconf", r#"echo "RNode firmware"; exit 0"#);
    }
    pub fn tool(&self, name: &str) -> String {
        self.tools.join(name).display().to_string()
    }
    pub fn lib(&self, rel: &str) -> String {
        self.data.join("library").join(rel).display().to_string()
    }
    pub fn port(&self, tty: &str) -> String {
        self.dev.join(tty).display().to_string()
    }
    /// Add a fake USB serial device with a tty, like a board on a hub port.
    pub fn add_usb(&self, port: &str, vid: &str, pid: &str, serial: &str, tty: &str) {
        let d = self.usb.join(port);
        fs::create_dir_all(d.join(format!("{port}:1.0")).join(tty)).unwrap();
        fs::write(d.join("idVendor"), format!("{vid}\n")).unwrap();
        fs::write(d.join("idProduct"), format!("{pid}\n")).unwrap();
        fs::write(d.join("serial"), format!("{serial}\n")).unwrap();
        fs::write(d.join("product"), "Fake board\n").unwrap();
        fs::write(self.dev.join(tty), "").unwrap();
    }
    pub fn remove_usb(&self, port: &str) {
        fs::remove_dir_all(self.usb.join(port)).unwrap();
    }
    pub fn add_uf2(&self, name: &str, model: &str, board_id: &str) -> PathBuf {
        let d = self.uf2.join(name);
        fs::create_dir_all(&d).unwrap();
        fs::write(
            d.join("INFO_UF2.TXT"),
            format!("UF2 Bootloader v0.6.2\nModel: {model}\nBoard-ID: {board_id}\n"),
        )
        .unwrap();
        d
    }
    pub fn cmd(&self) -> Command {
        let mut c = Command::new(env!("CARGO_BIN_EXE_taos-firmware"));
        c.env_clear()
            .env("PATH", "/usr/bin:/bin")
            .env("HOME", &self.root)
            .env("TAOS_FIRMWARE_DIR", &self.data)
            .env("TAOS_FW_USB_ROOT", &self.usb)
            .env("TAOS_FW_DEV_ROOT", &self.dev)
            .env("TAOS_FW_UF2_ROOTS", &self.uf2)
            .env("TAOS_FW_API_BASE", &self.api)
            .env("TAOS_FW_VERIFY_WAIT_MS", "400")
            .env("TAOS_FW_ESPTOOL", self.tool("esptool"))
            .env("TAOS_FW_MESHTASTIC", self.tool("meshtastic"))
            .env("TAOS_FW_NRFUTIL", self.tool("nrfutil"))
            .env("TAOS_FW_MESHCORE_CLI", self.tool("meshcore-cli"))
            .env("TAOS_FW_RNODECONF", self.tool("rnodeconf"))
            .env("FAKE_LOG", &self.log);
        c
    }
    pub fn run(&self, args: &[&str]) -> (i32, Value) {
        self.run_env(args, &[])
    }
    pub fn run_env(&self, args: &[&str], envs: &[(&str, &str)]) -> (i32, Value) {
        let mut c = self.cmd();
        c.arg("--json").args(args);
        for (k, v) in envs {
            c.env(k, v);
        }
        let o = c.output().unwrap();
        let s = String::from_utf8_lossy(&o.stdout).into_owned();
        let v = s
            .lines()
            .rfind(|l| l.starts_with('{'))
            .and_then(|l| serde_json::from_str(l).ok())
            .unwrap_or_else(|| {
                panic!(
                    "no JSON from {args:?}: stdout={s} stderr={}",
                    String::from_utf8_lossy(&o.stderr)
                )
            });
        (o.status.code().unwrap_or(-1), v)
    }
    pub fn tool_log(&self) -> Vec<String> {
        fs::read_to_string(&self.log)
            .unwrap()
            .lines()
            .map(String::from)
            .collect()
    }
    pub fn accept_all(&self) {
        for n in ["meshtastic", "meshcore", "rnode"] {
            let (c, _) = self.run(&["licence", "accept", n, "--by", "Test Owner"]);
            assert_eq!(c, 0);
        }
    }
    pub fn refresh_all(&self) -> Value {
        let (c, v) = self.run(&["refresh"]);
        assert_eq!(c, 0, "{v}");
        v
    }
    pub fn audit(&self) -> Vec<Value> {
        fs::read_to_string(self.data.join("flash-log.jsonl"))
            .unwrap_or_default()
            .lines()
            .map(|l| serde_json::from_str(l).unwrap())
            .collect()
    }
}

pub fn entry_ids(v: &Value) -> Vec<String> {
    v["entries"]
        .as_array()
        .unwrap()
        .iter()
        .map(|e| e["id"].as_str().unwrap().to_string())
        .collect()
}

pub fn exists(p: &Path) -> bool {
    p.exists()
}
