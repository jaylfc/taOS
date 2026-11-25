// SPDX-License-Identifier: AGPL-3.0-or-later
//! Detection and the guarded flash flow, driven through the real CLI against fake devices and tools.
mod common;
use common::*;
use serde_json::Value;
use std::fs;

const V3: &str = "usb-10c4-ea60-SN1-1-1";
const RAK_SERIAL: &str = "usb-239a-8029-NRF1-1-2";

fn setup() -> (Server, Env) {
    let srv = Server::start();
    publish_all(&srv, None);
    let env = Env::new(&srv.base);
    env.accept_all();
    env.refresh_all();
    (srv, env)
}

fn flash(
    env: &Env,
    dev: &str,
    entry: &str,
    confirm: Option<&str>,
    extra: &[&str],
    envs: &[(&str, &str)],
) -> (i32, Value) {
    let mut a = vec!["flash", "run", "--device", dev, "--entry", entry];
    if let Some(c) = confirm {
        a.push("--confirm");
        a.push(c);
    }
    a.extend_from_slice(extra);
    // Unsigned images also need the acknowledgement; tests of that pass `--no-ack` to leave it out.
    if let Some(i) = a.iter().position(|x| *x == "--no-ack") {
        a.remove(i);
    } else {
        a.push("--ack-unsigned");
    }
    env.run_env(&a, envs)
}

fn writes(env: &Env) -> Vec<String> {
    env.tool_log()
        .into_iter()
        .filter(|l| l.contains("write_flash") || l.starts_with("nrfutil"))
        .collect()
}

#[test]
fn detects_boards_from_usb_ids_and_the_bootloader_probe() {
    let (_s, env) = setup();
    env.add_usb("1-1", "10c4", "ea60", "SN1", "ttyUSB0");
    env.add_usb("1-2", "239a", "8029", "NRF1", "ttyACM0");
    env.add_usb("1-3", "1234", "abcd", "OTHER", "ttyUSB1"); // not a board we know
    env.add_uf2("RAK4631", "WisBlock RAK4631", "WisBlock-RAK4631-Board");
    env.add_uf2("MYSTERY", "Some Other Board", "Other-Board");
    let (_, v) = env.run(&["devices", "--probe"]);
    let d = v["devices"].as_array().unwrap();
    assert_eq!(d.len(), 4, "{v}");
    let by = |id: &str| {
        d.iter()
            .find(|x| x["id"] == id)
            .unwrap_or_else(|| panic!("{id} in {v}"))
    };
    let cp = by(V3);
    assert_eq!(cp["probe"], "ok");
    assert_eq!(cp["chip"], "esp32s3");
    assert_eq!(
        cp["candidates"],
        serde_json::json!(["heltec-v3"]),
        "the probe narrows CP210x boards by chip"
    );
    assert_eq!(by(RAK_SERIAL)["candidates"], serde_json::json!(["rak4631"]));
    assert_eq!(by(RAK_SERIAL)["probe"], "not-applicable");
    assert_eq!(
        by("uf2-RAK4631")["candidates"],
        serde_json::json!(["rak4631"])
    );
    assert_eq!(by("uf2-MYSTERY")["candidates"], serde_json::json!([]));
    // The same USB id on an original ESP32 is a T-Beam.
    let (_, v) = env.run_env(
        &["devices", "--probe"],
        &[("FAKE_CHIP", "ESP32-D0WD-V3 (revision v3.1)")],
    );
    let cp = v["devices"]
        .as_array()
        .unwrap()
        .iter()
        .find(|x| x["id"] == V3)
        .unwrap()
        .clone();
    assert_eq!(cp["candidates"], serde_json::json!(["tbeam"]));
    // No probe: both are still possible and the probe is marked skipped.
    let before = env.tool_log().len();
    let (_, v) = env.run(&["devices"]);
    assert_eq!(
        env.tool_log().len(),
        before,
        "listing without --probe does not touch the boards"
    );
    let cp = v["devices"]
        .as_array()
        .unwrap()
        .iter()
        .find(|x| x["id"] == V3)
        .unwrap()
        .clone();
    assert_eq!(cp["probe"], "skipped");
    assert_eq!(cp["candidates"], serde_json::json!(["heltec-v3", "tbeam"]));
    // A probe that fails is reported as failed, not as an empty answer.
    let (_, v) = env.run_env(&["devices", "--probe"], &[("FAKE_PROBE_FAIL", "1")]);
    let cp = v["devices"]
        .as_array()
        .unwrap()
        .iter()
        .find(|x| x["id"] == V3)
        .unwrap()
        .clone();
    assert!(cp["probe"].as_str().unwrap().starts_with("failed"), "{cp}");
}

#[test]
fn lists_only_firmware_that_fits_the_device() {
    let (_s, env) = setup();
    env.add_usb("1-1", "10c4", "ea60", "SN1", "ttyUSB0");
    let (code, v) = env.run(&["compatible", "--device", V3, "--probe"]);
    assert_eq!(code, 0);
    let fw = v["firmware"].as_array().unwrap();
    assert!(!fw.is_empty());
    assert!(fw
        .iter()
        .all(|e| e["board"] == "heltec-v3" && e["usable_now"] == true));
    let nets: std::collections::BTreeSet<_> =
        fw.iter().map(|e| e["network"].as_str().unwrap()).collect();
    assert_eq!(
        nets.into_iter().collect::<Vec<_>>(),
        vec!["meshcore", "meshtastic", "rnode"]
    );
}

#[test]
fn a_mismatched_image_is_refused_before_anything_runs() {
    let (_s, env) = setup();
    env.add_usb("1-1", "10c4", "ea60", "SN1", "ttyUSB0");
    // nRF52 image for a Heltec V3.
    let (code, v) = flash(
        &env,
        V3,
        "meshtastic:rak4631:2.7.26.54e0d8d",
        Some("RAK4631"),
        &[],
        &[],
    );
    assert_eq!(code, 1);
    assert_eq!(v["code"], "board_mismatch", "{v}");
    // T-Beam image (original ESP32) for a chip the probe says is an ESP32-S3.
    let (_, v) = flash(
        &env,
        V3,
        "meshtastic:tbeam:2.7.26.54e0d8d",
        Some("LilyGO T-Beam"),
        &[],
        &[],
    );
    assert_eq!(v["code"], "board_mismatch", "{v}");
    // Wrong board of the same chip family: a Heltec V4 image on a probed V3 (candidates narrowed by id).
    let (_, v) = flash(
        &env,
        V3,
        "meshcore:heltec-v4:1.18.0:ble",
        Some("Heltec V4"),
        &[],
        &[],
    );
    assert_eq!(v["code"], "board_mismatch", "{v}");
    assert!(
        writes(&env).is_empty(),
        "no flash tool ran: {:?}",
        env.tool_log()
    );
    // UF2 drive of one board, image of another.
    let d = env.add_uf2("RAK4631", "WisBlock RAK4631", "WisBlock-RAK4631-Board");
    let (_, v) = flash(
        &env,
        "uf2-RAK4631",
        "meshtastic:t1000e:2.7.26.54e0d8d",
        Some("Seeed T1000-E"),
        &[],
        &[],
    );
    assert_eq!(v["code"], "board_mismatch");
    assert_eq!(
        fs::read_dir(&d).unwrap().count(),
        1,
        "nothing was copied to the drive"
    );
    // Every refusal is in the log.
    let log = env.audit();
    assert_eq!(
        log.iter()
            .filter(|l| l["event"] == "refused" && l["code"] == "board_mismatch")
            .count(),
        4
    );
}

#[test]
fn flashing_needs_the_board_name_typed() {
    let (_s, env) = setup();
    env.add_usb("1-1", "10c4", "ea60", "SN1", "ttyUSB0");
    let e = "meshtastic:heltec-v3:2.7.26.54e0d8d";
    let (code, v) = flash(&env, V3, e, None, &[], &[]);
    assert_eq!(
        (code, v["code"].as_str()),
        (1, Some("confirmation_required")),
        "{v}"
    );
    assert!(
        !v["message"].as_str().unwrap().contains("Heltec"),
        "the refusal does not repeat the name"
    );
    let (_, v) = flash(&env, V3, e, Some("Heltec V4"), &[], &[]);
    assert_eq!(v["code"], "confirmation_required");
    let (_, v) = flash(&env, V3, e, Some("yes"), &[], &[]);
    assert_eq!(v["code"], "confirmation_required");
    assert!(writes(&env).is_empty());
    let (code, v) = flash(&env, V3, e, Some("  heltec   v3 "), &[], &[]);
    assert_eq!(code, 0, "{v}");
    assert_eq!(writes(&env).len(), 1);
}

#[test]
fn the_plan_shows_the_exact_command_without_running_it() {
    let (_s, env) = setup();
    env.add_usb("1-1", "10c4", "ea60", "SN1", "ttyUSB0");
    let (code, v) = env.run(&[
        "flash",
        "plan",
        "--device",
        V3,
        "--entry",
        "meshcore:heltec-v3:1.18.0:usb",
    ]);
    assert_eq!(code, 0, "{v}");
    assert!(v["plan"].get("confirm_phrase").is_none());
    assert_eq!(v["plan"]["chip_probe"], "after confirmation");
    assert!(
        env.tool_log().iter().all(|l| !l.contains("chip_id")),
        "planning does not reset the board"
    );
    let flash_step = v["plan"]["steps"]
        .as_array()
        .unwrap()
        .iter()
        .find(|s| s["name"] == "flash")
        .unwrap();
    assert_eq!(flash_step["argv"][0], env.tool("esptool"));
    assert!(writes(&env).is_empty());
    assert!(v["plan"]["warnings"][0]
        .as_str()
        .unwrap()
        .contains("Not verified:"));
}

fn argv_line(env: &Env, tool: &str, rest: &[String]) -> String {
    let _ = env;
    format!("{tool} {}", rest.join(" "))
}

#[test]
fn esptool_command_lines_for_each_network() {
    let (_s, env) = setup();
    env.add_usb("1-1", "10c4", "ea60", "SN1", "ttyUSB0");
    let port = env.port("ttyUSB0");
    let p = |s: &str| s.to_string();
    // Meshtastic: factory image, OTA slot and file system at the offsets of the release's flash map.
    let (code, v) = flash(
        &env,
        V3,
        "meshtastic:heltec-v3:2.7.26.54e0d8d",
        Some("Heltec V3"),
        &[],
        &[],
    );
    assert_eq!(code, 0, "{v}");
    let want = argv_line(
        &env,
        "esptool",
        &[
            p("--chip"),
            p("esp32s3"),
            p("--port"),
            port.clone(),
            p("--baud"),
            p("460800"),
            p("write_flash"),
            p("-z"),
            p("0x0"),
            env.lib("meshtastic/2.7.26.54e0d8d/firmware-heltec-v3-2.7.26.54e0d8d.factory.bin"),
            p("0x340000"),
            env.lib("meshtastic/2.7.26.54e0d8d/mt-esp32s3-ota.bin"),
            p("0x670000"),
            env.lib("meshtastic/2.7.26.54e0d8d/littlefs-heltec-v3-2.7.26.54e0d8d.bin"),
        ],
    );
    assert!(
        env.tool_log().contains(&want),
        "want {want}\nhave {:#?}",
        env.tool_log()
    );
    // MeshCore: the merged image at 0x0.
    let (code, v) = flash(
        &env,
        V3,
        "meshcore:heltec-v3:1.18.0:ble",
        Some("Heltec V3"),
        &[],
        &[],
    );
    assert_eq!(code, 0, "{v}");
    let want = argv_line(
        &env,
        "esptool",
        &[
            p("--chip"),
            p("esp32s3"),
            p("--port"),
            port.clone(),
            p("--baud"),
            p("460800"),
            p("write_flash"),
            p("-z"),
            p("0x0"),
            env.lib("meshcore/1.18.0/Heltec_v3_companion_radio_ble-v1.18.0-81dbae5-merged.bin"),
        ],
    );
    assert!(env.tool_log().contains(&want), "want {want}");
    // RNode on an ESP32-S3: bootloader at 0x0.
    let (code, v) = flash(
        &env,
        V3,
        "rnode:heltec-v3:1.86",
        Some("Heltec V3"),
        &[],
        &[],
    );
    assert_eq!(code, 0, "{v}");
    let r = |n: &str| env.lib(&format!("rnode/1.86/{n}"));
    let want = argv_line(
        &env,
        "esptool",
        &[
            p("--chip"),
            p("esp32s3"),
            p("--port"),
            port.clone(),
            p("--baud"),
            p("460800"),
            p("write_flash"),
            p("-z"),
            p("0xe000"),
            r("rnode_firmware_heltec32v3.boot_app0"),
            p("0x0"),
            r("rnode_firmware_heltec32v3.bootloader"),
            p("0x8000"),
            r("rnode_firmware_heltec32v3.partitions"),
            p("0x10000"),
            r("rnode_firmware_heltec32v3.bin"),
            p("0x210000"),
            r("rnode_heltec32v3_console_image.bin"),
        ],
    );
    assert!(
        env.tool_log().contains(&want),
        "want {want}\nhave {:#?}",
        env.tool_log()
    );
    // RNode on the original ESP32 (T-Beam): bootloader at 0x1000.
    let (code, v) = flash(
        &env,
        V3,
        "rnode:tbeam:1.86",
        Some("LilyGO T-Beam"),
        &[],
        &[("FAKE_CHIP", "ESP32-D0WD-V3 (revision v3.1)")],
    );
    assert_eq!(code, 0, "{v}");
    let r = |n: &str| env.lib(&format!("rnode/1.86/{n}"));
    let want = argv_line(
        &env,
        "esptool",
        &[
            p("--chip"),
            p("esp32"),
            p("--port"),
            port,
            p("--baud"),
            p("460800"),
            p("write_flash"),
            p("-z"),
            p("0xe000"),
            r("rnode_firmware_tbeam_sx1262.boot_app0"),
            p("0x1000"),
            r("rnode_firmware_tbeam_sx1262.bootloader"),
            p("0x8000"),
            r("rnode_firmware_tbeam_sx1262.partitions"),
            p("0x10000"),
            r("rnode_firmware_tbeam_sx1262.bin"),
            p("0x210000"),
            r("rnode_tbeam_sx1262_console_image.bin"),
        ],
    );
    assert!(
        env.tool_log().contains(&want),
        "want {want}\nhave {:#?}",
        env.tool_log()
    );
}

#[test]
fn uf2_images_are_copied_to_the_bootloader_drive() {
    let (_s, env) = setup();
    let d = env.add_uf2("RAK4631", "WisBlock RAK4631", "WisBlock-RAK4631-Board");
    let (code, v) = flash(
        &env,
        "uf2-RAK4631",
        "meshtastic:rak4631:2.7.26.54e0d8d",
        Some("RAK4631"),
        &[],
        &[],
    );
    assert_eq!(code, 0, "{v}");
    let copied = fs::read(d.join("firmware-rak4631-2.7.26.54e0d8d.uf2")).unwrap();
    assert_eq!(copied, b"firmware bytes for mt rak4631 2.7.26.54e0d8d uf2");
    assert_eq!(
        v["backup"]["status"], "not_possible",
        "a board in its bootloader has no configuration to save"
    );
    assert_eq!(
        v["verify"]["status"], "unconfirmed",
        "no serial port came back in the fake"
    );
    assert!(writes(&env).is_empty(), "UF2 needs no flash tool");
    // MeshCore on a T1000-E drive.
    let d = env.add_uf2("T1000E", "Seeed SenseCAP T1000-E", "SenseCap-T1000-E");
    let (code, v) = flash(
        &env,
        "uf2-T1000E",
        "meshcore:t1000e:1.18.0:ble",
        Some("seeed t1000-e"),
        &[],
        &[],
    );
    assert_eq!(code, 0, "{v}");
    assert!(d
        .join("t1000e_companion_radio_ble-v1.18.0-81dbae5.uf2")
        .exists());
}

#[test]
fn nrf52_serial_dfu_uses_the_official_tool_with_a_touch() {
    let (_s, env) = setup();
    env.add_usb("1-2", "239a", "8029", "NRF1", "ttyACM0");
    let port = env.port("ttyACM0");
    // A DFU package exists for RNode on the RAK4631.
    let (code, v) = flash(
        &env,
        RAK_SERIAL,
        "rnode:rak4631:1.86",
        Some("RAK4631"),
        &[],
        &[],
    );
    assert_eq!(code, 0, "{v}");
    let want = format!(
        "nrfutil dfu serial --package {} -p {port} -b 115200 --singlebank --touch 1200",
        env.lib("rnode/1.86/rnode_firmware_rak4631.zip")
    );
    assert!(
        env.tool_log().contains(&want),
        "want {want}\nhave {:#?}",
        env.tool_log()
    );
    // Meshtastic carries an OTA package too, so a running board can be updated the same way.
    let (code, _) = flash(
        &env,
        RAK_SERIAL,
        "meshtastic:rak4631:2.7.26.54e0d8d",
        Some("RAK4631"),
        &[],
        &[],
    );
    assert_eq!(code, 0);
    let want = format!(
        "nrfutil dfu serial --package {} -p {port} -b 115200 --singlebank --touch 1200",
        env.lib("meshtastic/2.7.26.54e0d8d/firmware-rak4631-2.7.26.54e0d8d-ota.zip")
    );
    assert!(env.tool_log().contains(&want));
}

#[test]
fn a_failed_flash_says_how_to_recover() {
    let (_s, env) = setup();
    env.add_usb("1-1", "10c4", "ea60", "SN1", "ttyUSB0");
    let (code, v) = flash(
        &env,
        V3,
        "meshtastic:heltec-v3:2.7.26.54e0d8d",
        Some("Heltec V3"),
        &[],
        &[("FAKE_FLASH_EXIT", "2")],
    );
    assert_eq!(code, 1);
    assert_eq!(v["ok"], false);
    assert_eq!(v["stage"], "flash");
    let rec = v["recovery"].as_str().unwrap();
    assert!(rec.contains("PRG") && rec.contains("bootloader"), "{rec}");
    let log = env.audit();
    assert_eq!(log.last().unwrap()["event"], "flash-end");
    assert_eq!(log.last().unwrap()["ok"], false);
    // The nRF52 board gets its own steps.
    let d = env.add_uf2("RAK4631", "WisBlock RAK4631", "WisBlock-RAK4631-Board");
    fs::remove_dir_all(&d).unwrap();
    env.add_usb("1-2", "239a", "8029", "NRF1", "ttyACM0");
    let (_, v) = flash(
        &env,
        RAK_SERIAL,
        "rnode:rak4631:1.86",
        Some("RAK4631"),
        &[],
        &[("FAKE_FLASH_EXIT", "1")],
    );
    assert!(
        v["recovery"].as_str().unwrap().contains("Double-tap"),
        "{v}"
    );
}

#[test]
fn a_board_that_does_not_answer_afterwards_is_reported_with_recovery() {
    let (_s, env) = setup();
    env.add_usb("1-1", "10c4", "ea60", "SN1", "ttyUSB0");
    let (code, v) = flash(
        &env,
        V3,
        "meshtastic:heltec-v3:2.7.26.54e0d8d",
        Some("Heltec V3"),
        &[],
        &[("FAKE_MT_VERSION", "1.0.0")],
    );
    assert_eq!(code, 1, "{v}");
    assert_eq!(v["verify"]["status"], "failed");
    assert!(v["recovery"].as_str().unwrap().contains("PRG"));
    // And the happy path confirms the radio came up on the new network.
    let (code, v) = flash(
        &env,
        V3,
        "meshtastic:heltec-v3:2.7.26.54e0d8d",
        Some("Heltec V3"),
        &[],
        &[],
    );
    assert_eq!(code, 0);
    assert_eq!(v["verify"]["status"], "confirmed");
}

#[test]
fn the_configuration_is_backed_up_first_and_restored_on_the_same_network() {
    let (_s, env) = setup();
    env.add_usb("1-1", "10c4", "ea60", "SN1", "ttyUSB0");
    let (code, v) = flash(
        &env,
        V3,
        "meshtastic:heltec-v3:2.7.26.54e0d8d",
        Some("Heltec V3"),
        &[],
        &[],
    );
    assert_eq!(code, 0, "{v}");
    assert_eq!(v["backup"]["status"], "saved");
    let dir = std::path::PathBuf::from(v["backup"]["dir"].as_str().unwrap());
    assert!(fs::read_to_string(dir.join("meshtastic-config.yaml"))
        .unwrap()
        .contains("region: EU_868"));
    let log = env.tool_log();
    let pos = |needle: &str| {
        log.iter()
            .position(|l| l.contains(needle))
            .unwrap_or_else(|| panic!("{needle} in {log:#?}"))
    };
    assert!(
        pos("--export-config") < pos("write_flash"),
        "backup comes before the flash"
    );
    assert!(
        pos("write_flash") < pos("--configure"),
        "restore comes after"
    );
    assert!(log.iter().any(|l| l.contains(&format!(
        "--configure {}",
        dir.join("meshtastic-config.yaml").display()
    ))));
    assert_eq!(v["restore"]["status"], "restored");
}

#[test]
fn settings_that_do_not_carry_are_kept_for_switching_back() {
    let (_s, env) = setup();
    env.add_usb("1-1", "10c4", "ea60", "SN1", "ttyUSB0");
    // A Meshtastic board is switched to MeshCore (the fake MeshCore answers --info style calls too).
    let (code, v) = flash(
        &env,
        V3,
        "meshcore:heltec-v3:1.18.0:ble",
        Some("Heltec V3"),
        &[],
        &[],
    );
    assert_eq!(code, 0, "{v}");
    assert_eq!(v["backup"]["status"], "saved");
    assert_eq!(v["restore"]["status"], "kept_for_switching_back", "{v}");
    let (_, b) = env.run(&["backups"]);
    assert_eq!(b["backups"].as_array().unwrap().len(), 1);
    assert_eq!(b["backups"][0]["network"], "meshtastic");
}

#[test]
fn a_failed_backup_stops_the_flash_unless_the_person_allows_it() {
    let (_s, env) = setup();
    env.add_usb("1-1", "10c4", "ea60", "SN1", "ttyUSB0");
    let none = [("FAKE_MT_NO_BACKUP", "1"), ("FAKE_MC_NO_BACKUP", "1")];
    let (code, v) = flash(
        &env,
        V3,
        "meshtastic:heltec-v3:2.7.26.54e0d8d",
        Some("Heltec V3"),
        &[],
        &none,
    );
    assert_eq!(code, 1);
    assert_eq!(v["stage"], "backup");
    assert!(
        v["message"]
            .as_str()
            .unwrap()
            .contains("no backup possible"),
        "{v}"
    );
    assert!(writes(&env).is_empty(), "nothing was flashed");
    let (code, v) = flash(
        &env,
        V3,
        "meshtastic:heltec-v3:2.7.26.54e0d8d",
        Some("Heltec V3"),
        &["--allow-no-backup"],
        &none,
    );
    assert_eq!(code, 0, "{v}");
    assert_eq!(v["backup"]["status"], "not_possible");
    assert_eq!(writes(&env).len(), 1);
}

#[test]
fn agents_need_the_persons_consent_and_every_flash_is_logged() {
    let (_s, env) = setup();
    env.add_usb("1-1", "10c4", "ea60", "SN1", "ttyUSB0");
    let e = "meshtastic:heltec-v3:2.7.26.54e0d8d";
    let (code, v) = flash(
        &env,
        V3,
        e,
        Some("Heltec V3"),
        &["--actor", "agent:scout"],
        &[],
    );
    assert_eq!(
        (code, v["code"].as_str()),
        (1, Some("consent_required")),
        "{v}"
    );
    assert!(writes(&env).is_empty());
    let (c, _) = env.run(&[
        "consent",
        "grant",
        "--agent",
        "scout",
        "--by",
        "Test Owner",
        "--minutes",
        "10",
    ]);
    assert_eq!(c, 0);
    // Consent is not the confirmation: the typed name is still needed.
    let (_, v) = flash(&env, V3, e, None, &["--actor", "agent:scout"], &[]);
    assert_eq!(v["code"], "confirmation_required");
    let (code, v) = flash(
        &env,
        V3,
        e,
        Some("Heltec V3"),
        &["--actor", "agent:scout"],
        &[],
    );
    assert_eq!(code, 0, "{v}");
    // Another agent has no grant; revoking removes it.
    let (_, v) = flash(
        &env,
        V3,
        e,
        Some("Heltec V3"),
        &["--actor", "agent:other"],
        &[],
    );
    assert_eq!(v["code"], "consent_required");
    env.run(&["consent", "revoke", "--agent", "scout"]);
    let (_, v) = flash(
        &env,
        V3,
        e,
        Some("Heltec V3"),
        &["--actor", "agent:scout"],
        &[],
    );
    assert_eq!(v["code"], "consent_required");
    let (_, log) = env.run(&["log"]);
    let log = log["log"].as_array().unwrap();
    let start = log.iter().find(|l| l["event"] == "flash-start").unwrap();
    assert_eq!(start["actor"], "agent:scout");
    assert_eq!(start["entry"], e);
    assert!(log
        .iter()
        .any(|l| l["event"] == "flash-end" && l["ok"] == true));
    assert!(log.iter().filter(|l| l["event"] == "refused").count() >= 4);
}

#[test]
fn a_tampered_library_file_is_never_flashed() {
    let (_s, env) = setup();
    env.add_usb("1-1", "10c4", "ea60", "SN1", "ttyUSB0");
    fs::write(
        env.data.join(
            "library/meshtastic/2.7.26.54e0d8d/firmware-heltec-v3-2.7.26.54e0d8d.factory.bin",
        ),
        b"evil",
    )
    .unwrap();
    let (code, v) = flash(
        &env,
        V3,
        "meshtastic:heltec-v3:2.7.26.54e0d8d",
        Some("Heltec V3"),
        &[],
        &[],
    );
    assert_eq!(
        (code, v["code"].as_str()),
        (1, Some("hash_mismatch")),
        "{v}"
    );
    assert!(writes(&env).is_empty());
}

#[test]
fn an_unconfirmed_chip_or_missing_tool_blocks_the_flash() {
    let (_s, env) = setup();
    env.add_usb("1-1", "10c4", "ea60", "SN1", "ttyUSB0");
    let (code, v) = flash(
        &env,
        V3,
        "meshtastic:heltec-v3:2.7.26.54e0d8d",
        Some("Heltec V3"),
        &[],
        &[("FAKE_PROBE_FAIL", "1")],
    );
    assert_eq!(
        (code, v["code"].as_str()),
        (1, Some("probe_required")),
        "{v}"
    );
    let (code, v) = flash(
        &env,
        V3,
        "meshtastic:heltec-v3:2.7.26.54e0d8d",
        Some("Heltec V3"),
        &[],
        &[("TAOS_FW_ESPTOOL", "/nonexistent/esptool")],
    );
    assert_eq!(code, 1);
    assert_eq!(v["code"], "tool_missing");
    assert!(
        v["message"].as_str().unwrap().contains("not an executable"),
        "{v}"
    );
    assert!(writes(&env).is_empty());
}

#[test]
fn no_device_and_no_licence_are_refused() {
    let (_s, env) = setup();
    let (_, v) = flash(
        &env,
        V3,
        "meshtastic:heltec-v3:2.7.26.54e0d8d",
        Some("Heltec V3"),
        &[],
        &[],
    );
    assert_eq!(v["code"], "no_such_device");
    env.add_usb("1-1", "10c4", "ea60", "SN1", "ttyUSB0");
    let (_, v) = flash(
        &env,
        V3,
        "meshtastic:heltec-v3:9.9.9",
        Some("Heltec V3"),
        &[],
        &[],
    );
    assert_eq!(v["code"], "no_such_firmware");
    fs::remove_file(env.data.join("licences.json")).unwrap();
    let (_, v) = flash(
        &env,
        V3,
        "meshtastic:heltec-v3:2.7.26.54e0d8d",
        Some("Heltec V3"),
        &[],
        &[],
    );
    assert_eq!(v["code"], "licence_required");
}

#[test]
fn uf2_drives_match_on_their_own_name_not_a_generic_chip_name() {
    let (_s, env) = setup();
    env.add_uf2("GENERIC", "Generic board", "nRF52840-DK-v1");
    env.add_uf2("XIAO", "Seeed XIAO nRF52840", "Seeed-XIAO-nRF52840");
    let (_, v) = env.run(&["devices"]);
    let d = v["devices"].as_array().unwrap();
    let c = |id: &str| d.iter().find(|x| x["id"] == id).unwrap()["candidates"].clone();
    assert_eq!(
        c("uf2-GENERIC"),
        serde_json::json!([]),
        "an nRF52840 name alone is not a XIAO"
    );
    assert_eq!(c("uf2-XIAO"), serde_json::json!(["xiao-nrf52840"]));
    let (_, v) = flash(
        &env,
        "uf2-GENERIC",
        "meshtastic:xiao-nrf52840:2.7.26.54e0d8d",
        Some("Seeed XIAO nRF52840"),
        &[],
        &[],
    );
    assert_eq!(v["code"], "board_mismatch", "{v}");
}

#[test]
fn erase_first_is_an_explicit_step_before_the_write() {
    let (_s, env) = setup();
    env.add_usb("1-1", "10c4", "ea60", "SN1", "ttyUSB0");
    let port = env.port("ttyUSB0");
    let (code, v) = flash(
        &env,
        V3,
        "meshcore:heltec-v3:1.18.0:ble",
        Some("Heltec V3"),
        &["--erase"],
        &[],
    );
    assert_eq!(code, 0, "{v}");
    let log = env.tool_log();
    let e = log
        .iter()
        .position(|l| *l == format!("esptool --chip esp32s3 --port {port} erase_flash"))
        .unwrap_or_else(|| panic!("{log:#?}"));
    let w = log.iter().position(|l| l.contains("write_flash")).unwrap();
    assert!(e < w);
    // Without the flag nothing is erased.
    let (_, _) = flash(
        &env,
        V3,
        "meshcore:heltec-v3:1.18.0:usb",
        Some("Heltec V3"),
        &[],
        &[],
    );
    assert_eq!(
        env.tool_log()
            .iter()
            .filter(|l| l.contains("erase_flash"))
            .count(),
        1
    );
}

#[test]
fn backups_are_private_and_the_restore_id_is_checked() {
    use std::os::unix::fs::PermissionsExt;
    let (_s, env) = setup();
    env.add_usb("1-1", "10c4", "ea60", "SN1", "ttyUSB0");
    let (code, v) = flash(
        &env,
        V3,
        "meshtastic:heltec-v3:2.7.26.54e0d8d",
        Some("Heltec V3"),
        &[],
        &[],
    );
    assert_eq!(code, 0);
    let dir = std::path::PathBuf::from(v["backup"]["dir"].as_str().unwrap());
    assert_eq!(
        fs::metadata(&dir).unwrap().permissions().mode() & 0o777,
        0o700
    );
    assert_eq!(
        fs::metadata(dir.join("meshtastic-config.yaml"))
            .unwrap()
            .permissions()
            .mode()
            & 0o777,
        0o600
    );
    assert_eq!(
        fs::metadata(&env.data).unwrap().permissions().mode() & 0o777,
        0o700
    );
    let before = writes(&env).len();
    let (code, v) = flash(
        &env,
        V3,
        "meshtastic:heltec-v3:2.7.26.54e0d8d",
        Some("Heltec V3"),
        &["--restore-from", "../../etc"],
        &[],
    );
    assert_eq!((code, v["code"].as_str()), (1, Some("bad_request")), "{v}");
    assert_eq!(writes(&env).len(), before);
}

#[test]
fn identical_boards_get_distinct_ids_and_only_the_chosen_one_is_touched() {
    let (_s, env) = setup();
    // Two bridges with the same default serial number on different hub ports.
    env.add_usb("1-1", "10c4", "ea60", "SN1", "ttyUSB0");
    env.add_usb("1-2", "10c4", "ea60", "SN1", "ttyUSB1");
    let (_, v) = env.run(&["devices"]);
    let ids: Vec<&str> = v["devices"]
        .as_array()
        .unwrap()
        .iter()
        .map(|d| d["id"].as_str().unwrap())
        .collect();
    assert_eq!(ids, vec!["usb-10c4-ea60-SN1-1-1", "usb-10c4-ea60-SN1-1-2"]);
    let before = env.tool_log().len();
    let second = "usb-10c4-ea60-SN1-1-2";
    let (code, v) = flash(
        &env,
        second,
        "meshtastic:heltec-v3:2.7.26.54e0d8d",
        Some("Heltec V3"),
        &[],
        &[],
    );
    assert_eq!(code, 0, "{v}");
    let log = env.tool_log();
    let other = env.port("ttyUSB0");
    let chosen = env.port("ttyUSB1");
    let probes: Vec<_> = log[before..]
        .iter()
        .filter(|l| l.contains("chip_id"))
        .collect();
    assert_eq!(probes.len(), 1, "{log:#?}");
    assert!(
        probes[0].contains(&chosen) && !log[before..].iter().any(|l| l.contains(&other)),
        "the other board was not touched: {log:#?}"
    );
    assert!(log
        .iter()
        .any(|l| l.contains("write_flash") && l.contains(&format!("--port {chosen} "))));
}

#[test]
fn an_image_that_cannot_fit_never_resets_the_board() {
    let (_s, env) = setup();
    env.add_usb("1-1", "10c4", "ea60", "SN1", "ttyUSB0");
    let (_, v) = flash(
        &env,
        V3,
        "meshtastic:rak4631:2.7.26.54e0d8d",
        Some("RAK4631"),
        &[],
        &[],
    );
    assert_eq!(v["code"], "board_mismatch");
    assert!(
        env.tool_log().is_empty(),
        "no probe, no flash: {:?}",
        env.tool_log()
    );
}

#[test]
fn an_image_nobody_signs_needs_a_separate_acknowledgement() {
    let (_s, env) = setup();
    env.add_usb("1-1", "10c4", "ea60", "SN1", "ttyUSB0");
    let e = "meshtastic:heltec-v3:2.7.26.54e0d8d";
    let (code, v) = flash(&env, V3, e, Some("Heltec V3"), &["--no-ack"], &[]);
    assert_eq!(
        (code, v["code"].as_str()),
        (1, Some("unsigned_ack_required")),
        "{v}"
    );
    let m = v["message"].as_str().unwrap();
    assert!(
        m.contains("Verified: the file matches sha256")
            && m.contains("Not verified:")
            && m.contains("does not sign"),
        "{m}"
    );
    assert!(writes(&env).is_empty());
    assert!(
        env.tool_log().iter().all(|l| !l.contains("chip_id")),
        "refused before any probe"
    );
    // RNode ships a publisher checksum: no acknowledgement needed.
    let (code, v) = flash(
        &env,
        V3,
        "rnode:heltec-v3:1.86",
        Some("Heltec V3"),
        &["--no-ack"],
        &[],
    );
    assert_eq!(code, 0, "{v}");
    // An entry with no checksum from anyone is never flashed, acknowledged or not.
    let p = env.data.join("manifest.json");
    let mut m: Value = serde_json::from_slice(&fs::read(&p).unwrap()).unwrap();
    for e in m["entries"].as_array_mut().unwrap() {
        if e["id"] == "meshcore:heltec-v3:1.18.0:ble" {
            e["verification"] = "unsigned-by-publisher".into();
        }
    }
    fs::write(&p, m.to_string()).unwrap();
    let (_, v) = flash(
        &env,
        V3,
        "meshcore:heltec-v3:1.18.0:ble",
        Some("Heltec V3"),
        &[],
        &[],
    );
    assert_eq!(v["code"], "unverified", "{v}");
}

#[test]
fn the_board_is_not_reset_until_the_person_has_confirmed() {
    let (_s, env) = setup();
    env.add_usb("1-1", "10c4", "ea60", "SN1", "ttyUSB0");
    let e = "meshtastic:heltec-v3:2.7.26.54e0d8d";
    let probes = |env: &Env| {
        env.tool_log()
            .iter()
            .filter(|l| l.contains("chip_id"))
            .count()
    };
    let (_, v) = flash(&env, V3, e, None, &[], &[]);
    assert_eq!(v["code"], "confirmation_required");
    let (_, v) = flash(&env, V3, e, Some("wrong"), &[], &[]);
    assert_eq!(v["code"], "confirmation_required");
    let (_, v) = flash(
        &env,
        V3,
        e,
        Some("Heltec V3"),
        &["--actor", "agent:nobody"],
        &[],
    );
    assert_eq!(v["code"], "consent_required");
    let (_, v) = flash(&env, V3, e, Some("Heltec V3"), &["--no-ack"], &[]);
    assert_eq!(v["code"], "unsigned_ack_required");
    assert_eq!(
        probes(&env),
        0,
        "no refusal before the confirmation touched the board"
    );
    let (code, v) = flash(&env, V3, e, Some("Heltec V3"), &[], &[]);
    assert_eq!(code, 0, "{v}");
    assert_eq!(probes(&env), 1);
}
