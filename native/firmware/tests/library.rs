// SPDX-License-Identifier: AGPL-3.0-or-later
//! Library: licence acceptance, manifest, verification, refresh behaviour (offline, ETag, rate limit).
mod common;
use common::*;
use serde_json::Value;
use std::fs;

fn setup(corrupt: Option<&str>) -> (Server, Env) {
    let srv = Server::start();
    publish_all(&srv, corrupt);
    let env = Env::new(&srv.base);
    (srv, env)
}

fn status_of(v: &Value, net: &str) -> String {
    v["reports"]
        .as_array()
        .unwrap()
        .iter()
        .find(|r| r["network"] == net)
        .unwrap()["status"]
        .as_str()
        .unwrap()
        .to_string()
}

#[test]
fn nothing_is_downloaded_before_the_licence_is_accepted() {
    let (srv, env) = setup(None);
    let (code, v) = env.run(&["refresh"]);
    assert_eq!(code, 0, "licence pending is a normal state, not a failure");
    for n in ["meshtastic", "meshcore", "rnode"] {
        assert_eq!(status_of(&v, n), "licence_required");
    }
    assert_eq!(srv.count("/dl/"), 0);
    assert_eq!(srv.count("/repos/"), 0);
    let (_, lib) = env.run(&["library"]);
    assert!(entry_ids(&lib).is_empty());
}

#[test]
fn licence_is_recorded_once_with_who_what_when() {
    let (_srv, env) = setup(None);
    let (_, a) = env.run(&["licence", "accept", "meshtastic", "--by", "Alex Owner"]);
    assert_eq!(a["newly_accepted"], true);
    assert_eq!(a["acceptance"]["licence_id"], "GPL-3.0-only");
    assert_eq!(a["acceptance"]["accepted_by"], "Alex Owner");
    let when = a["acceptance"]["accepted_at"].as_str().unwrap().to_string();
    // A second acceptance by someone else changes nothing.
    let (_, b) = env.run(&["licence", "accept", "meshtastic", "--by", "Someone Else"]);
    assert_eq!(b["newly_accepted"], false);
    assert_eq!(b["acceptance"]["accepted_by"], "Alex Owner");
    assert_eq!(b["acceptance"]["accepted_at"], when.as_str());
    let l: Value =
        serde_json::from_slice(&fs::read(env.data.join("licences.json")).unwrap()).unwrap();
    assert_eq!(l["accepted"].as_array().unwrap().len(), 1);
    // Naming nobody is refused.
    let (c, _) = env.run(&["licence", "accept", "meshcore"]);
    assert_eq!(c, 1);
    // The version is filled in by the first download.
    env.run(&["licence", "accept", "meshcore", "--by", "Alex Owner"]);
    env.run(&["licence", "accept", "rnode", "--by", "Alex Owner"]);
    env.refresh_all();
    let l: Value =
        serde_json::from_slice(&fs::read(env.data.join("licences.json")).unwrap()).unwrap();
    let mt = l["accepted"]
        .as_array()
        .unwrap()
        .iter()
        .find(|a| a["network"] == "meshtastic")
        .unwrap();
    assert_eq!(mt["version"], "2.7.26.54e0d8d");
}

#[test]
fn refresh_stores_latest_and_previous_stable_with_a_full_manifest() {
    let (_srv, env) = setup(None);
    env.accept_all();
    env.refresh_all();
    let (_, lib) = env.run(&["library"]);
    let ids = entry_ids(&lib);
    // Pre-releases are ignored; two stable versions per network.
    assert!(ids.contains(&"meshtastic:heltec-v3:2.7.26.54e0d8d".to_string()));
    // The older Meshtastic release has no flash map for ESP32 boards, so only nRF52 boards get it.
    assert!(!ids.contains(&"meshtastic:heltec-v3:2.7.25.104df5f".to_string()));
    assert!(ids.contains(&"meshtastic:rak4631:2.7.25.104df5f".to_string()));
    assert!(!ids.iter().any(|i| i.contains("2.8.2")));
    assert!(ids.contains(&"meshcore:rak4631:1.18.0:ble".to_string()));
    assert!(ids.contains(&"meshcore:heltec-v3:1.17.1:usb".to_string()));
    assert!(ids.contains(&"rnode:heltec-v3:1.86".to_string()));
    assert!(ids.contains(&"rnode:rak4631:1.85".to_string()));
    assert!(
        !ids.iter().any(|i| i.starts_with("rnode:t1000e")),
        "no RNode build for the T1000-E"
    );
    let e = lib["entries"]
        .as_array()
        .unwrap()
        .iter()
        .find(|e| e["id"] == "meshtastic:heltec-v3:2.7.26.54e0d8d")
        .unwrap();
    assert_eq!(e["licence_id"], "GPL-3.0-only");
    assert!(e["licence_url"].as_str().unwrap().starts_with("https://"));
    assert!(e["source"]
        .as_str()
        .unwrap()
        .contains("/firmware-esp32s3-2.7.26.54e0d8d.zip"));
    assert!(e["fetched_at"].as_str().unwrap().ends_with('Z'));
    assert_eq!(e["verification"], "release-host-digest");
    assert_eq!(e["unsigned_by_publisher"], true);
    let roles: Vec<(&str, &str)> = e["files"]
        .as_array()
        .unwrap()
        .iter()
        .map(|f| (f["role"].as_str().unwrap(), f["offset"].as_str().unwrap()))
        .collect();
    assert_eq!(
        roles,
        vec![
            ("factory", "0x0"),
            ("ota", "0x340000"),
            ("littlefs", "0x670000")
        ]
    );
    // Every stored file matches its recorded hash.
    for e in lib["entries"].as_array().unwrap() {
        for f in e["files"].as_array().unwrap() {
            let b = fs::read(env.data.join("library").join(f["path"].as_str().unwrap())).unwrap();
            assert_eq!(sha(&b), f["sha256"].as_str().unwrap());
        }
    }
    // RNode ships a publisher checksum list.
    let r = lib["entries"]
        .as_array()
        .unwrap()
        .iter()
        .find(|e| e["id"] == "rnode:heltec-v3:1.86")
        .unwrap();
    assert_eq!(r["verification"], "publisher-checksum");
    assert_eq!(r["unsigned_by_publisher"], false);
    // The release zips themselves are not kept: only the files that are used.
    assert!(!env
        .data
        .join("library/meshtastic/2.7.26.54e0d8d/firmware-esp32s3-2.7.26.54e0d8d.zip")
        .exists());
}

#[test]
fn a_bad_hash_is_refused_and_nothing_is_stored() {
    // Host digest wrong for one Meshtastic zip.
    let (_srv, env) = setup(Some("firmware-nrf52840-2.7.26.54e0d8d.zip"));
    env.accept_all();
    let (code, v) = env.run(&["refresh", "--network", "meshtastic"]);
    assert_eq!(code, 1);
    let rep = &v["reports"][0];
    assert_eq!(rep["status"], "error");
    assert!(
        rep["errors"][0]
            .as_str()
            .unwrap()
            .contains("differs from the release host digest"),
        "{rep}"
    );
    let (_, lib) = env.run(&["library"]);
    let ids = entry_ids(&lib);
    assert!(
        !ids.iter()
            .any(|i| i.starts_with("meshtastic:rak4631:2.7.26")),
        "{ids:?}"
    );
    assert!(
        ids.contains(&"meshtastic:heltec-v3:2.7.26.54e0d8d".to_string()),
        "the other zips are still fine"
    );
    assert!(!env
        .data
        .join("library/meshtastic/2.7.26.54e0d8d/firmware-rak4631-2.7.26.54e0d8d.uf2")
        .exists());
    let leftovers: Vec<_> = fs::read_dir(env.data.join("tmp"))
        .unwrap()
        .flatten()
        .collect();
    assert!(leftovers.is_empty(), "a refused download is deleted");
}

#[test]
fn a_publisher_checksum_mismatch_is_refused() {
    let (_srv, env) = setup(Some("rnode_firmware_heltec32v3.zip"));
    env.accept_all();
    let (code, v) = env.run(&["refresh", "--network", "rnode"]);
    assert_eq!(code, 1);
    assert!(
        v["reports"][0]["errors"][0]
            .as_str()
            .unwrap()
            .contains("publisher checksum"),
        "{v}"
    );
    let (_, lib) = env.run(&["library"]);
    let ids = entry_ids(&lib);
    assert!(!ids.contains(&"rnode:heltec-v3:1.86".to_string()));
    assert!(
        ids.contains(&"rnode:heltec-v3:1.85".to_string()),
        "the previous release is still good"
    );
}

#[test]
fn an_edited_library_file_is_noticed() {
    let (_srv, env) = setup(None);
    env.accept_all();
    env.refresh_all();
    let f = env
        .data
        .join("library/meshcore/1.18.0/Heltec_v3_companion_radio_ble-v1.18.0-81dbae5-merged.bin");
    fs::write(&f, b"tampered").unwrap();
    // A refresh sees the damaged file and fetches it again.
    let v = env.refresh_all();
    let mc = v["reports"]
        .as_array()
        .unwrap()
        .iter()
        .find(|r| r["network"] == "meshcore")
        .unwrap();
    assert!(
        mc["fetched"]
            .as_array()
            .unwrap()
            .iter()
            .any(|x| x == "meshcore:heltec-v3:1.18.0:ble"),
        "{mc}"
    );
    assert_eq!(
        sha(&fs::read(&f).unwrap()),
        sha(b"firmware bytes for mc Heltec_v3_companion_radio_ble-v1.18.0-81dbae5 merged")
    );
}

#[test]
fn offline_is_tolerated_for_the_timer_but_reported() {
    let (_srv, env) = setup(None);
    env.accept_all();
    env.refresh_all();
    let mut dead = Env::new("http://127.0.0.1:9");
    dead.data = env.data.clone();
    let (code, v) = dead.run(&["refresh", "--quiet-offline"]);
    assert_eq!(code, 0, "{v}");
    assert_eq!(status_of(&v, "meshtastic"), "offline");
    let (code, _) = dead.run(&["refresh"]);
    assert_eq!(code, 1, "without the timer flag, offline is a failure");
    // What was fetched before is still there.
    let (_, lib) = dead.run(&["library"]);
    assert!(entry_ids(&lib).contains(&"meshtastic:heltec-v3:2.7.26.54e0d8d".to_string()));
}

#[test]
fn second_refresh_asks_with_etag_and_downloads_nothing() {
    let (srv, env) = setup(None);
    env.accept_all();
    env.refresh_all();
    let dl = srv.count("/dl/");
    assert!(dl > 0);
    env.refresh_all();
    assert_eq!(srv.count("/dl/"), dl, "nothing new to download");
    let hits = srv.hits.lock().unwrap();
    let again: Vec<_> = hits
        .iter()
        .filter(|(p, e)| p.starts_with("/repos/meshtastic") && e.is_some())
        .collect();
    assert_eq!(
        again.len(),
        1,
        "the second listing request carries If-None-Match"
    );
}

#[test]
fn rate_limit_is_respected_and_not_hammered() {
    let (srv, env) = setup(None);
    env.accept_all();
    let reset = (std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)
        .unwrap()
        .as_secs()
        + 3600)
        .to_string();
    srv.set(
        "/repos/meshtastic/firmware/releases?per_page=30",
        403,
        b"{\"message\":\"API rate limit exceeded\"}".to_vec(),
        vec![
            ("X-RateLimit-Remaining", "0"),
            ("X-RateLimit-Reset", &reset),
        ],
    );
    let (code, v) = env.run(&["refresh", "--network", "meshtastic", "--quiet-offline"]);
    assert_eq!(code, 0);
    assert_eq!(status_of(&v, "meshtastic"), "rate_limited");
    assert_eq!(
        v["reports"][0]["retry_after_epoch"]
            .as_u64()
            .unwrap()
            .to_string(),
        reset
    );
    assert_eq!(srv.count("/repos/meshtastic"), 1);
    // The next run does not call the API again before the reset.
    let (_, v) = env.run(&["refresh", "--network", "meshtastic", "--quiet-offline"]);
    assert_eq!(status_of(&v, "meshtastic"), "rate_limited");
    assert_eq!(srv.count("/repos/meshtastic"), 1);
}

#[test]
fn only_the_two_newest_stable_versions_are_kept() {
    let (srv, env) = setup(None);
    env.accept_all();
    env.refresh_all();
    let old = env
        .data
        .join("library/rnode/1.85/rnode_firmware_heltec32v3.bin");
    assert!(old.exists());
    publish_rnode(&srv, &["1.87", "1.86"], None);
    let (code, out) = env.run(&["refresh", "--network", "rnode"]);
    assert_eq!(code, 0, "{out}");
    let (_, lib) = env.run(&["library", "--network", "rnode", "--board", "heltec-v3"]);
    let mut ids = entry_ids(&lib);
    ids.sort();
    assert_eq!(ids, vec!["rnode:heltec-v3:1.86", "rnode:heltec-v3:1.87"]);
    assert!(!old.exists(), "the oldest version's files are removed");
    assert!(env
        .data
        .join("library/rnode/1.86/rnode_firmware_heltec32v3.bin")
        .exists());
}

#[test]
fn a_release_without_a_flash_map_is_noted_and_not_fetched_again() {
    let (srv, env) = setup(None);
    env.accept_all();
    let (code, v) = env.run(&["refresh", "--network", "meshtastic"]);
    assert_eq!(code, 0, "an unusable old release is not an error: {v}");
    let rep = &v["reports"][0];
    assert_eq!(rep["status"], "ok");
    let notes = rep["notes"].as_array().unwrap();
    assert!(
        notes.iter().any(|n| n
            .as_str()
            .unwrap()
            .contains("predates the release flash map")),
        "{rep}"
    );
    let zip = "/firmware-esp32s3-2.7.25.104df5f.zip";
    let fetched = || {
        srv.hits
            .lock()
            .unwrap()
            .iter()
            .filter(|(p, _)| p.ends_with(zip))
            .count()
    };
    assert_eq!(fetched(), 1);
    // The next run leaves it alone instead of downloading the zip again; --force tries again.
    env.run(&["refresh", "--network", "meshtastic"]);
    assert_eq!(fetched(), 1);
    env.run(&["refresh", "--network", "meshtastic", "--force"]);
    assert_eq!(fetched(), 2);
}

#[test]
fn a_failed_download_is_retried_later_not_on_every_tick() {
    let (srv, env) = setup(Some("firmware-nrf52840-2.7.26.54e0d8d.zip"));
    env.accept_all();
    let zip = "/firmware-nrf52840-2.7.26.54e0d8d.zip";
    let fetched = || {
        srv.hits
            .lock()
            .unwrap()
            .iter()
            .filter(|(p, _)| p.ends_with(zip))
            .count()
    };
    env.run(&["refresh", "--network", "meshtastic"]);
    assert_eq!(fetched(), 1);
    env.run(&["refresh", "--network", "meshtastic"]);
    assert_eq!(fetched(), 1, "backed off");
}
