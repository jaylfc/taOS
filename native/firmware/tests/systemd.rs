// SPDX-License-Identifier: AGPL-3.0-or-later
//! The shipped systemd user units: contents only. Nothing here enables or starts a unit.
use std::fs;
use std::path::PathBuf;

fn unit(name: &str) -> String {
    let p = PathBuf::from(env!("CARGO_MANIFEST_DIR"))
        .join("systemd")
        .join(name);
    fs::read_to_string(&p).unwrap_or_else(|e| panic!("{}: {e}", p.display()))
}

#[test]
fn refresh_runs_offline_tolerant_and_supervised() {
    let s = unit("taos-firmware-refresh.service");
    assert!(
        s.contains("ExecStart=/opt/taos/bin/taos-firmware refresh --quiet-offline"),
        "{s}"
    );
    assert!(s.contains("Type=oneshot"));
    assert!(
        s.contains("Restart=on-failure"),
        "supervised: a real failure is retried"
    );
    assert!(s.contains("RestartSec="));
    assert!(s.contains("Nice=") && s.contains("NoNewPrivileges=yes"));
    assert!(
        !s.contains("WantedBy"),
        "the service is started by its timer only"
    );
}

#[test]
fn the_timer_survives_being_offline_and_asleep() {
    let t = unit("taos-firmware-refresh.timer");
    assert!(
        t.contains("Persistent=true"),
        "a missed run happens at the next boot or wake"
    );
    assert!(t.contains("OnBootSec="));
    assert!(t.contains("OnUnitInactiveSec=") || t.contains("OnCalendar="));
    assert!(
        t.contains("RandomizedDelaySec="),
        "no stampede on the release host"
    );
    assert!(t.contains("WantedBy=timers.target"));
    assert!(t.contains("Unit=taos-firmware-refresh.service"));
}

#[test]
fn the_api_service_listens_on_loopback_only() {
    let s = unit("taos-firmware.service");
    assert!(s.contains("ExecStart=/opt/taos/bin/taos-firmware serve"));
    assert!(s.contains("Restart=on-failure"));
    assert!(!s.contains("0.0.0.0"));
    assert!(s.contains("WantedBy=default.target"));
}
