// SPDX-License-Identifier: AGPL-3.0-or-later
//! On-disk state: the firmware manifest, licence acceptances, agent consent grants and the flash log.
use crate::timefmt;
use serde::{Deserialize, Serialize};
use sha2::{Digest, Sha256};
use std::fs::{self, File, OpenOptions};
use std::io::{Read, Write};
use std::path::{Path, PathBuf};

#[derive(Clone, Debug)]
pub struct Paths {
    pub root: PathBuf,
}

impl Paths {
    pub fn new(root: impl Into<PathBuf>) -> Paths {
        Paths { root: root.into() }
    }
    /// `TAOS_FIRMWARE_DIR`, else `$XDG_DATA_HOME/taos/firmware`, else `~/.local/share/taos/firmware`.
    pub fn from_env() -> Result<Paths, String> {
        if let Some(d) = std::env::var_os("TAOS_FIRMWARE_DIR").filter(|v| !v.is_empty()) {
            return Ok(Paths::new(d));
        }
        if let Some(d) = std::env::var_os("XDG_DATA_HOME").filter(|v| !v.is_empty()) {
            return Ok(Paths::new(PathBuf::from(d).join("taos/firmware")));
        }
        match std::env::var_os("HOME").filter(|v| !v.is_empty()) {
            Some(h) => Ok(Paths::new(
                PathBuf::from(h).join(".local/share/taos/firmware"),
            )),
            None => Err("no data directory: set TAOS_FIRMWARE_DIR".into()),
        }
    }
    pub fn library(&self) -> PathBuf {
        self.root.join("library")
    }
    pub fn manifest(&self) -> PathBuf {
        self.root.join("manifest.json")
    }
    pub fn licences(&self) -> PathBuf {
        self.root.join("licences.json")
    }
    pub fn consent(&self) -> PathBuf {
        self.root.join("consent.json")
    }
    pub fn audit(&self) -> PathBuf {
        self.root.join("flash-log.jsonl")
    }
    pub fn backups(&self) -> PathBuf {
        self.root.join("backups")
    }
    pub fn state(&self) -> PathBuf {
        self.root.join("refresh-state.json")
    }
    pub fn http_cache(&self) -> PathBuf {
        self.root.join("http-cache")
    }
    pub fn token(&self) -> PathBuf {
        self.root.join("api-token")
    }
    pub fn tmp(&self) -> PathBuf {
        self.root.join("tmp")
    }
    pub fn ensure(&self) -> Result<(), String> {
        use std::os::unix::fs::{DirBuilderExt, PermissionsExt};
        for d in [
            self.root.clone(),
            self.library(),
            self.backups(),
            self.http_cache(),
            self.tmp(),
        ] {
            fs::DirBuilder::new()
                .recursive(true)
                .mode(0o700)
                .create(&d)
                .map_err(|e| format!("create {}: {e}", d.display()))?;
        }
        // The state may hold configuration backups with channel keys: owner only.
        fs::set_permissions(&self.root, fs::Permissions::from_mode(0o700))
            .map_err(|e| format!("protect {}: {e}", self.root.display()))?;
        Ok(())
    }
}

pub fn write_atomic(path: &Path, data: &[u8]) -> Result<(), String> {
    let tmp = path.with_extension(format!("tmp{}", std::process::id()));
    {
        let mut f = File::create(&tmp).map_err(|e| format!("write {}: {e}", tmp.display()))?;
        f.write_all(data)
            .and_then(|_| f.sync_all())
            .map_err(|e| format!("write {}: {e}", tmp.display()))?;
    }
    fs::rename(&tmp, path).map_err(|e| format!("rename to {}: {e}", path.display()))
}

pub fn sha256_file(p: &Path) -> Result<(String, u64), String> {
    let mut f = File::open(p).map_err(|e| format!("open {}: {e}", p.display()))?;
    let mut h = Sha256::new();
    let mut buf = vec![0u8; 1 << 16];
    let mut n = 0u64;
    loop {
        let k = f
            .read(&mut buf)
            .map_err(|e| format!("read {}: {e}", p.display()))?;
        if k == 0 {
            break;
        }
        h.update(&buf[..k]);
        n += k as u64;
    }
    Ok((hex(&h.finalize()), n))
}

pub fn sha256_bytes(b: &[u8]) -> String {
    hex(&Sha256::digest(b))
}

fn hex(b: &[u8]) -> String {
    b.iter().map(|x| format!("{x:02x}")).collect()
}

// ---- manifest -------------------------------------------------------------------------------

#[derive(Clone, Debug, Serialize, Deserialize, PartialEq)]
pub struct FileRec {
    /// `factory`, `ota`, `littlefs`, `uf2`, `dfu-package`, `firmware`, `bootloader`, ...
    pub role: String,
    /// Relative to the library directory.
    pub path: String,
    pub sha256: String,
    pub size: u64,
    /// Flash offset for esptool writes.
    pub offset: Option<String>,
}

#[derive(Clone, Debug, Serialize, Deserialize, PartialEq)]
pub struct Entry {
    pub id: String,
    pub board: String,
    pub network: String,
    pub version: String,
    /// `ble` / `usb` for MeshCore, empty otherwise.
    pub variant: String,
    /// `esptool`, `uf2` or `nrfutil`.
    pub method: String,
    pub chip: String,
    pub files: Vec<FileRec>,
    pub licence_id: String,
    pub licence_url: String,
    /// URL the verified asset was downloaded from.
    pub source: String,
    /// sha256 of the downloaded release asset (the archive for zip releases).
    pub source_sha256: String,
    pub fetched_at: String,
    /// `publisher-checksum`, `release-host-digest` or `unsigned-by-publisher`.
    pub verification: String,
    pub unsigned_by_publisher: bool,
}

#[derive(Default, Serialize, Deserialize)]
pub struct Manifest {
    pub version: u32,
    pub entries: Vec<Entry>,
}

impl Manifest {
    pub fn load(p: &Paths) -> Result<Manifest, String> {
        match fs::read(p.manifest()) {
            Ok(b) => {
                serde_json::from_slice(&b).map_err(|e| format!("manifest.json is damaged: {e}"))
            }
            Err(e) if e.kind() == std::io::ErrorKind::NotFound => Ok(Manifest {
                version: 1,
                entries: vec![],
            }),
            Err(e) => Err(format!("read manifest: {e}")),
        }
    }
    pub fn save(&self, p: &Paths) -> Result<(), String> {
        p.ensure()?;
        let b = serde_json::to_vec_pretty(self).map_err(|e| e.to_string())?;
        write_atomic(&p.manifest(), &b)
    }
    pub fn get(&self, id: &str) -> Option<&Entry> {
        self.entries.iter().find(|e| e.id == id)
    }
}

/// Re-hash every file of an entry against the manifest. Refuses on any difference.
pub fn verify_entry(p: &Paths, e: &Entry) -> Result<(), String> {
    if e.files.is_empty() {
        return Err(format!("{}: manifest entry lists no files", e.id));
    }
    for f in &e.files {
        let rel = Path::new(&f.path);
        if rel.is_absolute() || rel.components().any(|c| c.as_os_str() == "..") {
            return Err(format!("{}: unsafe path in manifest: {}", e.id, f.path));
        }
        let full = p.library().join(rel);
        let (h, n) = sha256_file(&full).map_err(|m| format!("{}: {m}", e.id))?;
        if h != f.sha256 || n != f.size {
            return Err(format!(
                "{}: {} does not match the manifest (sha256 {h}); refusing to use it",
                e.id, f.path
            ));
        }
    }
    Ok(())
}

// ---- licences -------------------------------------------------------------------------------

#[derive(Clone, Debug, Serialize, Deserialize)]
pub struct Acceptance {
    pub network: String,
    pub licence_id: String,
    pub licence_url: String,
    /// First firmware version this acceptance covered.
    pub version: String,
    pub accepted_by: String,
    pub accepted_at: String,
}

#[derive(Default, Serialize, Deserialize)]
pub struct Licences {
    pub accepted: Vec<Acceptance>,
}

impl Licences {
    pub fn load(p: &Paths) -> Result<Licences, String> {
        match fs::read(p.licences()) {
            Ok(b) => {
                serde_json::from_slice(&b).map_err(|e| format!("licences.json is damaged: {e}"))
            }
            Err(e) if e.kind() == std::io::ErrorKind::NotFound => Ok(Licences::default()),
            Err(e) => Err(format!("read licences: {e}")),
        }
    }
    pub fn get(&self, network: &str) -> Option<&Acceptance> {
        self.accepted.iter().find(|a| a.network == network)
    }
    /// Fill in the version an acceptance first covered, once the first download has happened.
    pub fn note_version(p: &Paths, network: &str, version: &str) -> Result<(), String> {
        let mut l = Licences::load(p)?;
        let Some(a) = l.accepted.iter_mut().find(|a| a.network == network) else {
            return Ok(());
        };
        if !a.version.is_empty() {
            return Ok(());
        }
        a.version = version.into();
        write_atomic(
            &p.licences(),
            &serde_json::to_vec_pretty(&l).map_err(|e| e.to_string())?,
        )
    }
    /// Record an acceptance once; a second call for the same network keeps the first record.
    pub fn accept(
        p: &Paths,
        network: crate::boards::Network,
        by: &str,
        version: &str,
    ) -> Result<(Acceptance, bool), String> {
        let by = by.trim();
        if by.is_empty() {
            return Err("who accepts the licence must be named".into());
        }
        p.ensure()?;
        let mut l = Licences::load(p)?;
        if let Some(a) = l.get(network.id()) {
            return Ok((a.clone(), false));
        }
        let a = Acceptance {
            network: network.id().into(),
            licence_id: network.licence_id().into(),
            licence_url: network.licence_url().into(),
            version: version.into(),
            accepted_by: by.into(),
            accepted_at: timefmt::now_rfc3339(),
        };
        l.accepted.push(a.clone());
        let b = serde_json::to_vec_pretty(&l).map_err(|e| e.to_string())?;
        write_atomic(&p.licences(), &b)?;
        Ok((a, true))
    }
}

// ---- agent consent --------------------------------------------------------------------------

#[derive(Clone, Debug, Serialize, Deserialize)]
pub struct Grant {
    pub agent: String,
    pub granted_by: String,
    pub created_at: String,
    pub expires_at: String,
}

#[derive(Default, Serialize, Deserialize)]
pub struct Consent {
    pub grants: Vec<Grant>,
}

impl Consent {
    pub fn load(p: &Paths) -> Result<Consent, String> {
        match fs::read(p.consent()) {
            Ok(b) => {
                serde_json::from_slice(&b).map_err(|e| format!("consent.json is damaged: {e}"))
            }
            Err(e) if e.kind() == std::io::ErrorKind::NotFound => Ok(Consent::default()),
            Err(e) => Err(format!("read consent: {e}")),
        }
    }
    fn save(&self, p: &Paths) -> Result<(), String> {
        p.ensure()?;
        let b = serde_json::to_vec_pretty(self).map_err(|e| e.to_string())?;
        write_atomic(&p.consent(), &b)
    }
    pub fn grant(p: &Paths, agent: &str, by: &str, minutes: u64) -> Result<Grant, String> {
        if agent.trim().is_empty() || by.trim().is_empty() {
            return Err("an agent and the person granting consent must be named".into());
        }
        if minutes == 0 || minutes > 24 * 60 {
            return Err("a grant lasts 1 to 1440 minutes".into());
        }
        let mut c = Consent::load(p)?;
        let now = timefmt::now_secs();
        c.grants.retain(|g| g.agent != agent);
        let g = Grant {
            agent: agent.into(),
            granted_by: by.trim().into(),
            created_at: timefmt::rfc3339(now),
            expires_at: timefmt::rfc3339(now + minutes * 60),
        };
        c.grants.push(g.clone());
        c.save(p)?;
        Ok(g)
    }
    pub fn revoke(p: &Paths, agent: &str) -> Result<bool, String> {
        let mut c = Consent::load(p)?;
        let before = c.grants.len();
        c.grants.retain(|g| g.agent != agent);
        let changed = c.grants.len() != before;
        if changed {
            c.save(p)?;
        }
        Ok(changed)
    }
    pub fn valid_for(&self, agent: &str) -> Option<&Grant> {
        let now = timefmt::now_secs();
        self.grants.iter().find(|g| {
            g.agent == agent && timefmt::parse_rfc3339(&g.expires_at).is_some_and(|t| t > now)
        })
    }
}

// ---- flash log ------------------------------------------------------------------------------

/// Append one JSON line. Errors are returned: a flash must not proceed without its log line.
pub fn audit(p: &Paths, mut rec: serde_json::Value) -> Result<(), String> {
    p.ensure()?;
    if let Some(o) = rec.as_object_mut() {
        o.insert("ts".into(), timefmt::now_rfc3339().into());
    }
    let mut line = serde_json::to_string(&rec).map_err(|e| e.to_string())?;
    line.push('\n');
    let mut f = OpenOptions::new()
        .create(true)
        .append(true)
        .open(p.audit())
        .map_err(|e| format!("flash log: {e}"))?;
    f.write_all(line.as_bytes())
        .and_then(|_| f.sync_all())
        .map_err(|e| format!("flash log: {e}"))
}

pub fn read_audit(p: &Paths, limit: usize) -> Vec<serde_json::Value> {
    let Ok(s) = fs::read_to_string(p.audit()) else {
        return vec![];
    };
    let v: Vec<serde_json::Value> = s
        .lines()
        .filter_map(|l| serde_json::from_str(l).ok())
        .collect();
    let skip = v.len().saturating_sub(limit);
    v.into_iter().skip(skip).collect()
}
