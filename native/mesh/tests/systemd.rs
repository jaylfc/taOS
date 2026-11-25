// SPDX-License-Identifier: AGPL-3.0-or-later
//! The shipped user unit and its installer. Nothing here starts or enables a unit: the installer runs with
//! --no-systemctl in a private home.
mod common;
use common::TmpDir;
use std::fs;
use std::path::PathBuf;
use std::process::Command;

fn root() -> PathBuf {
    PathBuf::from(env!("CARGO_MANIFEST_DIR"))
}

#[test]
fn the_unit_is_receive_only_by_default_opens_no_radio_and_lets_the_owner_override() {
    let s = fs::read_to_string(root().join("systemd/taos-mesh.service")).unwrap();
    let exec = s.lines().find(|l| l.starts_with("ExecStart=")).unwrap();
    assert_eq!(exec, "ExecStart=%h/.local/bin/taos-mesh daemon $TAOS_MESH_ARGS");
    assert!(s.contains("Environment=TAOS_MESH_ARGS=--receive-only"));
    assert!(!exec.contains("--device"), "no radio is opened unless the owner names one");
    // the owner's file is read after the default, so it wins
    let env_default = s.find("Environment=TAOS_MESH_ARGS").unwrap();
    assert!(s.find("EnvironmentFile=-%h/.config/taos-mesh/mesh.env").unwrap() > env_default);
    assert!(s.contains("Restart=on-failure") && s.contains("NoNewPrivileges=yes"));
    assert!(!s.contains("/home/"), "no home path in a shipped unit");
}

#[test]
fn the_installer_places_files_and_enables_nothing() {
    let t = TmpDir::new("units");
    let home = t.path().join("home");
    fs::create_dir_all(&home).unwrap();
    let fake = t.path().join("fake-bin");
    fs::write(&fake, "#!/bin/sh\nexit 0\n").unwrap();
    Command::new("chmod").arg("755").arg(&fake).status().unwrap();
    let fw_fake = t.path().join("fake-fw");
    fs::copy(&fake, &fw_fake).unwrap();
    let out = Command::new("sh")
        .arg(root().join("systemd/install-user-units.sh"))
        .args(["--mesh-bin"]).arg(&fake)
        .args(["--with-firmware", "--firmware-bin"]).arg(&fw_fake)
        .arg("--no-systemctl")
        .env_clear()
        .env("PATH", "/usr/bin:/bin")
        .env("HOME", &home)
        .output()
        .unwrap();
    assert!(out.status.success(), "{}", String::from_utf8_lossy(&out.stderr));
    let units = home.join(".config/systemd/user");
    assert!(home.join(".local/bin/taos-mesh").exists());
    let mesh = fs::read_to_string(units.join("taos-mesh.service")).unwrap();
    assert!(mesh.contains("--receive-only"));
    // the firmware units point at the installed binary, and nothing is enabled (no wants links)
    let api = fs::read_to_string(units.join("taos-firmware.service")).unwrap();
    assert!(api.contains(&format!("ExecStart={}/.local/bin/taos-firmware serve", home.display())), "{api}");
    let refresh = fs::read_to_string(units.join("taos-firmware-refresh.service")).unwrap();
    assert!(refresh.contains(&format!("{}/.local/bin/taos-firmware refresh", home.display())), "{refresh}");
    assert!(units.join("taos-firmware-refresh.timer").exists());
    assert!(!units.join("default.target.wants").exists() && !units.join("timers.target.wants").exists());
    // a missing binary is refused
    let bad = Command::new("sh")
        .arg(root().join("systemd/install-user-units.sh"))
        .args(["--mesh-bin", "/nonexistent/taos-mesh", "--no-systemctl"])
        .env_clear().env("PATH", "/usr/bin:/bin").env("HOME", &home)
        .output().unwrap();
    assert!(!bad.status.success());
}
