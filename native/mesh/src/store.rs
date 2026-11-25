// SPDX-License-Identifier: AGPL-3.0-or-later
//! Interim append-only record store (JSONL), designed to be replayed into
//! taOSmd when its record API is available. See docs/STORAGE.md.
//!
//! Rules: one record per line; a line is written with a single `write_all`
//! then `sync_data` before `append` returns; a failure is returned to the
//! caller (never swallowed); nothing is ever rewritten or trimmed. A torn
//! last line (crash mid-write) is moved to a side file on open, not deleted.

use crate::model::Record;
use std::fs::{File, OpenOptions};
use std::io::{self, BufRead, BufReader, Write};
use std::path::{Path, PathBuf};

#[derive(Debug)]
pub struct StoreError(pub String);
impl std::fmt::Display for StoreError {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        write!(f, "store error: {}", self.0)
    }
}
impl std::error::Error for StoreError {}
impl From<io::Error> for StoreError {
    fn from(e: io::Error) -> Self {
        StoreError(e.to_string())
    }
}

pub struct Store {
    path: PathBuf,
    file: File,
    pub torn_preserved: Option<PathBuf>,
}

/// An append-only line log shared by the record store, the identity registry
/// and the grant list.
pub struct LineLog {
    path: PathBuf,
    file: File,
    pub torn_preserved: Option<PathBuf>,
}

impl LineLog {
    pub fn open(path: &Path) -> Result<Self, StoreError> {
        if let Some(dir) = path.parent() {
            std::fs::create_dir_all(dir)?;
        }
        let torn = repair_tail(path)?;
        let file = OpenOptions::new().create(true).append(true).open(path)?;
        if let Some(dir) = path.parent() {
            // make the directory entry durable
            if let Ok(d) = File::open(dir) {
                let _ = d.sync_all();
            }
        }
        Ok(LineLog { path: path.to_path_buf(), file, torn_preserved: torn })
    }

    pub fn path(&self) -> &Path {
        &self.path
    }

    /// Append one line durably. `line` must not contain a newline.
    pub fn append_line(&mut self, line: &str) -> Result<(), StoreError> {
        if line.contains('\n') {
            return Err(StoreError("line contains a newline".into()));
        }
        let mut buf = String::with_capacity(line.len() + 1);
        buf.push_str(line);
        buf.push('\n');
        self.file.write_all(buf.as_bytes())?;
        self.file.sync_data()?;
        Ok(())
    }

    pub fn read_lines(path: &Path) -> Result<Vec<String>, StoreError> {
        let f = match File::open(path) {
            Ok(f) => f,
            Err(e) if e.kind() == io::ErrorKind::NotFound => return Ok(vec![]),
            Err(e) => return Err(e.into()),
        };
        let mut out = Vec::new();
        for l in BufReader::new(f).lines() {
            let l = l?;
            if !l.is_empty() {
                out.push(l);
            }
        }
        Ok(out)
    }
}

/// If the file does not end in a newline, the last line was torn by a crash.
/// Preserve those bytes in `<name>.torn-<n>` and cut the file back to the
/// last complete line.
fn repair_tail(path: &Path) -> Result<Option<PathBuf>, StoreError> {
    let data = match std::fs::read(path) {
        Ok(d) => d,
        Err(e) if e.kind() == io::ErrorKind::NotFound => return Ok(None),
        Err(e) => return Err(e.into()),
    };
    if data.is_empty() || data.ends_with(b"\n") {
        return Ok(None);
    }
    let cut = data.iter().rposition(|b| *b == b'\n').map(|i| i + 1).unwrap_or(0);
    let mut n = 0;
    let side = loop {
        let name = format!("{}.torn-{n}", path.file_name().and_then(|s| s.to_str()).unwrap_or("log"));
        let p = path.with_file_name(name);
        if !p.exists() {
            break p;
        }
        n += 1;
    };
    {
        let mut f = File::create(&side)?;
        f.write_all(&data[cut..])?;
        f.sync_all()?;
    }
    let f = OpenOptions::new().write(true).open(path)?;
    f.set_len(cut as u64)?;
    f.sync_all()?;
    Ok(Some(side))
}

impl Store {
    pub fn open(dir: &Path) -> Result<Self, StoreError> {
        let log = LineLog::open(&dir.join("records.jsonl"))?;
        Ok(Store { path: log.path.clone(), file: log.file, torn_preserved: log.torn_preserved })
    }

    pub fn path(&self) -> &Path {
        &self.path
    }

    pub fn append(&mut self, rec: &Record) -> Result<(), StoreError> {
        let line = serde_json::to_string(rec).map_err(|e| StoreError(e.to_string()))?;
        let mut buf = line.into_bytes();
        buf.push(b'\n');
        self.file.write_all(&buf)?;
        self.file.sync_data()?;
        Ok(())
    }

    /// Read every record. A line that does not parse is an error, not a skip:
    /// it is reported with its line number so nothing is silently lost.
    pub fn read_all(dir: &Path) -> Result<Vec<Record>, StoreError> {
        let mut out = Vec::new();
        for (i, l) in LineLog::read_lines(&dir.join("records.jsonl"))?.into_iter().enumerate() {
            out.push(serde_json::from_str::<Record>(&l).map_err(|e| StoreError(format!("records.jsonl line {}: {e}", i + 1)))?);
        }
        Ok(out)
    }
}

impl Store {
    /// Open a specific file (tests put the store on a device that fails
    /// writes; nothing else uses this).
    pub fn open_file(path: &Path) -> Result<Self, StoreError> {
        let file = OpenOptions::new().append(true).open(path)?;
        Ok(Store { path: path.to_path_buf(), file, torn_preserved: None })
    }
}
