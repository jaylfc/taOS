// SPDX-License-Identifier: AGPL-3.0-or-later
//! Shows the pairing code on the cast screen without putting it in any
//! process argument: the code lives in a 0600 file in the 0700 runtime
//! directory, and the helper window (`taos-cast _show-code <file>`) only
//! receives the file name. The window removes the file and exits when the code
//! expires, and the supervisor removes both as soon as a phone has paired.

use serde::{Deserialize, Serialize};
use std::fs;
use std::io::Write;
use std::os::unix::fs::OpenOptionsExt;
use std::path::{Path, PathBuf};
use std::time::{Duration, SystemTime, UNIX_EPOCH};

#[derive(Serialize, Deserialize)]
struct Shown {
    url: String,
    code: String,
    /// Unix seconds.
    expires: u64,
}

pub fn file_path(dir: &Path) -> PathBuf {
    dir.join("code.json")
}

fn now() -> u64 {
    SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .map(|d| d.as_secs())
        .unwrap_or(0)
}

pub fn write(dir: &Path, url: &str, code: &str, ttl: Duration) -> Result<PathBuf, String> {
    let p = file_path(dir);
    let body = serde_json::to_vec(&Shown {
        url: url.into(),
        code: code.into(),
        expires: now() + ttl.as_secs(),
    })
    .map_err(|e| e.to_string())?;
    let mut f = fs::OpenOptions::new()
        .write(true)
        .create(true)
        .truncate(true)
        .mode(0o600)
        .open(&p)
        .map_err(|e| format!("code file: {e}"))?;
    f.write_all(&body).map_err(|e| format!("code file: {e}"))?;
    Ok(p)
}

pub fn remove(dir: &Path) {
    let _ = fs::remove_file(file_path(dir));
}

/// The helper window's body: show the code until it expires or the file goes.
pub fn show(file: &Path) -> Result<(), String> {
    let mut last = String::new();
    loop {
        let Ok(raw) = fs::read(file) else {
            return Ok(());
        };
        let s: Shown = serde_json::from_slice(&raw).map_err(|e| e.to_string())?;
        let left = s.expires.saturating_sub(now());
        if left == 0 {
            let _ = fs::remove_file(file);
            return Ok(());
        }
        let spaced: Vec<String> = s.code.chars().map(String::from).collect();
        let text = format!(
            "\x1b[2J\x1b[H\n\n   Control this desktop from your phone\n\n   1. Open  {}\n   2. Enter the code\n\n        {}\n\n   The code works once and expires in {} min {:02} s.\n",
            s.url,
            spaced.join("  "),
            left / 60,
            left % 60
        );
        if text != last {
            print!("{text}");
            let _ = std::io::stdout().flush();
            last = text;
        }
        std::thread::sleep(Duration::from_millis(500));
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn the_file_is_private_and_removed_when_expired() {
        let d = std::env::temp_dir().join(format!("tcast-codeview-{}", std::process::id()));
        fs::create_dir_all(&d).unwrap();
        let p = write(&d, "http://x/", "123456", Duration::from_secs(60)).unwrap();
        use std::os::unix::fs::PermissionsExt;
        assert_eq!(
            fs::metadata(&p).unwrap().permissions().mode() & 0o777,
            0o600
        );
        let p0 = write(&d, "http://x/", "123456", Duration::from_secs(0)).unwrap();
        show(&p0).unwrap(); // expired at once: returns and deletes
        assert!(!p0.exists());
        fs::remove_dir_all(&d).ok();
    }
}
