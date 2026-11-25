// SPDX-License-Identifier: AGPL-3.0-or-later
//! Running the official vendor tools as subprocesses. Always an argument list, never a shell string.
use std::io::Read;
use std::path::{Path, PathBuf};
use std::process::{Command, Stdio};
use std::time::{Duration, Instant};

#[derive(Debug, Clone, Default)]
pub struct RunResult {
    pub code: Option<i32>,
    pub stdout: String,
    pub stderr: String,
}

impl RunResult {
    pub fn ok(&self) -> bool {
        self.code == Some(0)
    }
    pub fn tail(&self) -> String {
        let s = format!("{}\n{}", self.stdout.trim(), self.stderr.trim());
        let t = s.trim();
        let start = t.len().saturating_sub(600);
        let mut i = start;
        while !t.is_char_boundary(i) {
            i += 1;
        }
        t[i..].to_string()
    }
}

pub trait Runner {
    fn run(&self, argv: &[String], timeout: Duration) -> Result<RunResult, String>;
}

pub struct System;

fn drain<R: Read + Send + 'static>(mut r: R) -> std::thread::JoinHandle<String> {
    std::thread::spawn(move || {
        let mut b = vec![];
        let _ = r.read_to_end(&mut b);
        String::from_utf8_lossy(&b).into_owned()
    })
}

impl Runner for System {
    fn run(&self, argv: &[String], timeout: Duration) -> Result<RunResult, String> {
        let (prog, args) = argv.split_first().ok_or("empty command")?;
        let mut c = Command::new(prog)
            .args(args)
            .stdin(Stdio::null())
            .stdout(Stdio::piped())
            .stderr(Stdio::piped())
            .spawn()
            .map_err(|e| format!("cannot start {prog}: {e}"))?;
        let (o, e) = (
            drain(c.stdout.take().ok_or("no stdout")?),
            drain(c.stderr.take().ok_or("no stderr")?),
        );
        let start = Instant::now();
        let status = loop {
            if let Some(s) = c.try_wait().map_err(|e| e.to_string())? {
                break s;
            }
            if start.elapsed() > timeout {
                let _ = c.kill();
                let _ = c.wait();
                return Err(format!(
                    "{prog} did not finish within {}s and was stopped",
                    timeout.as_secs()
                ));
            }
            std::thread::sleep(Duration::from_millis(30));
        };
        Ok(RunResult {
            code: status.code(),
            stdout: o.join().unwrap_or_default(),
            stderr: e.join().unwrap_or_default(),
        })
    }
}

#[derive(Clone, Copy, PartialEq, Eq, Debug)]
pub enum Tool {
    Esptool,
    Meshtastic,
    Nrfutil,
    MeshcoreCli,
    Rnodeconf,
}

impl Tool {
    pub fn env(self) -> &'static str {
        match self {
            Tool::Esptool => "TAOS_FW_ESPTOOL",
            Tool::Meshtastic => "TAOS_FW_MESHTASTIC",
            Tool::Nrfutil => "TAOS_FW_NRFUTIL",
            Tool::MeshcoreCli => "TAOS_FW_MESHCORE_CLI",
            Tool::Rnodeconf => "TAOS_FW_RNODECONF",
        }
    }
    fn names(self) -> &'static [&'static str] {
        match self {
            Tool::Esptool => &["esptool", "esptool.py"],
            Tool::Meshtastic => &["meshtastic"],
            Tool::Nrfutil => &["adafruit-nrfutil"],
            Tool::MeshcoreCli => &["meshcore-cli"],
            Tool::Rnodeconf => &["rnodeconf"],
        }
    }
    pub fn install_hint(self) -> &'static str {
        match self {
            Tool::Esptool => "install the official esptool (pip install esptool)",
            Tool::Meshtastic => "install the Meshtastic Python CLI (pip install meshtastic)",
            Tool::Nrfutil => "install adafruit-nrfutil (pip install adafruit-nrfutil)",
            Tool::MeshcoreCli => "install meshcore-cli (pip install meshcore-cli)",
            Tool::Rnodeconf => "install rnodeconf (pip install rns)",
        }
    }
}

fn is_exec(p: &Path) -> bool {
    use std::os::unix::fs::PermissionsExt;
    p.metadata()
        .is_ok_and(|m| m.is_file() && m.permissions().mode() & 0o111 != 0)
}

/// Find the tool: the `TAOS_FW_*` override first, then the PATH.
pub fn resolve(t: Tool) -> Result<String, String> {
    if let Some(v) = std::env::var_os(t.env()).filter(|v| !v.is_empty()) {
        let p = PathBuf::from(&v);
        return if is_exec(&p) {
            Ok(p.display().to_string())
        } else {
            Err(format!(
                "{} points at {} which is not an executable file",
                t.env(),
                p.display()
            ))
        };
    }
    let path = std::env::var_os("PATH").unwrap_or_default();
    for name in t.names() {
        for d in std::env::split_paths(&path) {
            let c = d.join(name);
            if is_exec(&c) {
                return Ok(c.display().to_string());
            }
        }
    }
    Err(format!(
        "{} is not installed: {}",
        t.names()[0],
        t.install_hint()
    ))
}
