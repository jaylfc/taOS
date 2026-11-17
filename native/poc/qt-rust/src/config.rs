//! PocConfig: env-driven configuration + bar clock/battery text, exposed to QML as a singleton.
#[cxx_qt::bridge]
pub mod qobject {
    unsafe extern "C++" {
        include!("cxx-qt-lib/qstring.h");
        type QString = cxx_qt_lib::QString;
        include!("cxx-qt-lib/qurl.h");
        type QUrl = cxx_qt_lib::QUrl;
    }

    extern "RustQt" {
        #[qobject]
        #[qml_element]
        #[qml_singleton]
        #[qproperty(QUrl, wallpaper)]
        #[qproperty(bool, no_blur, cxx_name = "noBlur")]
        #[qproperty(i32, blur_radius, cxx_name = "blurRadius")]
        #[qproperty(i32, auto_card, cxx_name = "autoCard")]
        #[qproperty(i32, auto_scroll, cxx_name = "autoScroll")]
        #[qproperty(QString, battery)]
        #[qproperty(QString, clock)]
        type PocConfig = super::PocConfigRust;

        /// Re-read clock and battery (called from a QML Timer).
        #[qinvokable]
        fn refresh(self: Pin<&mut PocConfig>);
    }
}

use core::pin::Pin;
use cxx_qt_lib::{QDateTime, QString, QUrl};
use std::{env, fs, path::PathBuf};

pub struct PocConfigRust {
    wallpaper: QUrl,
    no_blur: bool,
    blur_radius: i32,
    auto_card: i32,
    auto_scroll: i32,
    battery: QString,
    clock: QString,
}

fn env_int(name: &str) -> Option<i32> {
    env::var(name).ok()?.trim().parse().ok()
}

fn read_battery() -> String {
    if let Ok(dirs) = fs::read_dir("/sys/class/power_supply") {
        for d in dirs.flatten() {
            let p = d.path();
            let ty = fs::read_to_string(p.join("type")).unwrap_or_default();
            if ty.trim() == "Battery" {
                if let Ok(cap) = fs::read_to_string(p.join("capacity")) {
                    return format!("{}%", cap.trim());
                }
            }
        }
    }
    "--%".to_string()
}

fn read_clock() -> String {
    let t = QDateTime::current_date_time().time();
    format!("{:02}:{:02}", t.hour(), t.minute())
}

fn wallpaper_path() -> PathBuf {
    if let Ok(p) = env::var("POC_WALLPAPER") {
        if !p.is_empty() {
            return PathBuf::from(p);
        }
    }
    let dir = env::current_exe()
        .ok()
        .and_then(|e| e.parent().map(|p| p.to_path_buf()))
        .unwrap_or_default();
    let cands = [
        dir.join("../../../common/wallpaper.png"),
        dir.join("../common/wallpaper.png"),
        dir.join("wallpaper.png"),
    ];
    cands
        .iter()
        .find(|c| c.exists())
        .unwrap_or(&cands[0])
        .clone()
}

impl Default for PocConfigRust {
    fn default() -> Self {
        let wp = wallpaper_path();
        let wp = fs::canonicalize(&wp).unwrap_or(wp);
        let url = format!("file://{}", wp.display());
        Self {
            wallpaper: QUrl::from(&url),
            no_blur: env::var("POC_NO_BLUR").map(|v| v == "1").unwrap_or(false),
            blur_radius: env_int("POC_BLUR_RADIUS").filter(|r| *r >= 0).unwrap_or(24),
            auto_card: env_int("POC_AUTOCARD").unwrap_or(0).max(0),
            auto_scroll: env_int("POC_AUTOSCROLL").unwrap_or(0).max(0),
            battery: QString::from(read_battery()),
            clock: QString::from(read_clock()),
        }
    }
}

impl qobject::PocConfig {
    pub fn refresh(mut self: Pin<&mut Self>) {
        self.as_mut().set_clock(QString::from(read_clock()));
        self.as_mut().set_battery(QString::from(read_battery()));
    }
}
