// SPDX-License-Identifier: AGPL-3.0-or-later
//! Pairing and per-device tokens.
//!
//! A phone pairs once with a short code that only the owner's own session can
//! mint (through the control socket, never over the network). Pairing yields a
//! long random token that the phone keeps; the engine keeps only its SHA-256
//! and the device list, in a 0600 file. Nothing is accepted without a token.

use serde::{Deserialize, Serialize};
use sha2::{Digest, Sha256};
use std::fs;
use std::io::{Read, Write};
use std::os::unix::fs::{OpenOptionsExt, PermissionsExt};
use std::path::{Path, PathBuf};
use std::sync::Mutex;
use std::time::{Duration, Instant, SystemTime, UNIX_EPOCH};

pub const CODE_TTL: Duration = Duration::from_secs(300);
pub const MAX_CODE_ATTEMPTS: u32 = 5;
pub const MAX_DEVICES: usize = 16;
pub const MAX_NAME: usize = 40;

/// Cryptographically random bytes from the kernel.
pub fn random_bytes(buf: &mut [u8]) -> std::io::Result<()> {
    fs::File::open("/dev/urandom")?.read_exact(buf)
}

pub fn hex(bytes: &[u8]) -> String {
    bytes.iter().map(|b| format!("{b:02x}")).collect()
}

fn token_hash(token: &str) -> String {
    hex(&Sha256::digest(token.as_bytes()))
}

/// Compare without leaking where the first difference is.
pub fn ct_eq(a: &[u8], b: &[u8]) -> bool {
    let mut diff = (a.len() != b.len()) as u32;
    for i in 0..a.len().max(b.len()) {
        let x = a.get(i).copied().unwrap_or(0);
        let y = b.get(i).copied().unwrap_or(0);
        diff |= (x ^ y) as u32;
    }
    diff == 0
}

#[derive(Clone, Debug, Serialize, Deserialize, PartialEq, Eq)]
pub struct Device {
    pub id: String,
    pub name: String,
    pub token_hash: String,
    pub created: u64,
    pub last_seen: u64,
}

#[derive(Debug, PartialEq, Eq)]
pub enum PairError {
    NoCodeActive,
    Expired,
    TooManyAttempts,
    WrongCode,
    TooManyDevices,
    Storage(String),
}

impl std::fmt::Display for PairError {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        match self {
            PairError::NoCodeActive => write!(f, "no pairing code is active"),
            PairError::Expired => write!(f, "the pairing code has expired"),
            PairError::TooManyAttempts => write!(f, "too many wrong codes; ask for a new one"),
            PairError::WrongCode => write!(f, "that code is not right"),
            PairError::TooManyDevices => write!(f, "too many paired devices; remove one first"),
            PairError::Storage(e) => write!(f, "could not save the device: {e}"),
        }
    }
}

struct Code {
    value: String,
    expires: Instant,
    attempts: u32,
}

struct Inner {
    devices: Vec<Device>,
    code: Option<Code>,
}

pub struct Auth {
    path: PathBuf,
    inner: Mutex<Inner>,
}

fn now_unix() -> u64 {
    SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .map(|d| d.as_secs())
        .unwrap_or(0)
}

impl Auth {
    /// Open (or create) the device store. A file that exists but cannot be
    /// read or parsed is an error: it is never silently replaced.
    pub fn open(path: &Path) -> Result<Auth, String> {
        let devices = match fs::read_to_string(path) {
            Ok(s) => serde_json::from_str::<Vec<Device>>(&s)
                .map_err(|e| format!("{} is not valid: {e}", path.display()))?,
            Err(e) if e.kind() == std::io::ErrorKind::NotFound => Vec::new(),
            Err(e) => return Err(format!("cannot read {}: {e}", path.display())),
        };
        Ok(Auth {
            path: path.to_path_buf(),
            inner: Mutex::new(Inner {
                devices,
                code: None,
            }),
        })
    }

    fn save(path: &Path, devices: &[Device]) -> Result<(), String> {
        let tmp = path.with_extension("tmp");
        let body = serde_json::to_vec_pretty(devices).map_err(|e| e.to_string())?;
        let mut f = fs::OpenOptions::new()
            .write(true)
            .create(true)
            .truncate(true)
            .mode(0o600)
            .open(&tmp)
            .map_err(|e| e.to_string())?;
        f.write_all(&body).map_err(|e| e.to_string())?;
        f.sync_all().map_err(|e| e.to_string())?;
        fs::set_permissions(&tmp, fs::Permissions::from_mode(0o600)).map_err(|e| e.to_string())?;
        fs::rename(&tmp, path).map_err(|e| e.to_string())
    }

    /// Mint a fresh six digit pairing code, replacing any earlier one.
    pub fn new_code(&self) -> Result<(String, Duration), String> {
        let value = loop {
            let mut b = [0u8; 4];
            random_bytes(&mut b).map_err(|e| e.to_string())?;
            let n = u32::from_le_bytes(b);
            // Reject the biased tail so every code is equally likely.
            if n < 4_294_000_000 {
                break format!("{:06}", n % 1_000_000);
            }
        };
        let mut g = self.inner.lock().unwrap();
        g.code = Some(Code {
            value: value.clone(),
            expires: Instant::now() + CODE_TTL,
            attempts: 0,
        });
        Ok((value, CODE_TTL))
    }

    /// Pair a device. On success the code is spent and the token is returned
    /// (the only time it exists in the clear on the engine side).
    pub fn pair(&self, given: &str, name: &str) -> Result<(String, String), PairError> {
        let mut g = self.inner.lock().unwrap();
        let ok = {
            let code = g.code.as_mut().ok_or(PairError::NoCodeActive)?;
            if Instant::now() >= code.expires {
                g.code = None;
                return Err(PairError::Expired);
            }
            if code.attempts >= MAX_CODE_ATTEMPTS {
                g.code = None;
                return Err(PairError::TooManyAttempts);
            }
            code.attempts += 1;
            ct_eq(code.value.as_bytes(), given.trim().as_bytes())
        };
        if !ok {
            let spent = g
                .code
                .as_ref()
                .map(|c| c.attempts >= MAX_CODE_ATTEMPTS)
                .unwrap_or(false);
            if spent {
                g.code = None;
                return Err(PairError::TooManyAttempts);
            }
            return Err(PairError::WrongCode);
        }
        if g.devices.len() >= MAX_DEVICES {
            return Err(PairError::TooManyDevices);
        }
        let mut raw = [0u8; 32];
        let mut idb = [0u8; 6];
        random_bytes(&mut raw).map_err(|e| PairError::Storage(e.to_string()))?;
        random_bytes(&mut idb).map_err(|e| PairError::Storage(e.to_string()))?;
        let token = hex(&raw);
        let name: String = name
            .chars()
            .filter(|c| !c.is_control())
            .take(MAX_NAME)
            .collect();
        let dev = Device {
            id: hex(&idb),
            name: if name.trim().is_empty() {
                "Phone".into()
            } else {
                name
            },
            token_hash: token_hash(&token),
            created: now_unix(),
            last_seen: 0,
        };
        let mut next = g.devices.clone();
        next.push(dev.clone());
        Auth::save(&self.path, &next).map_err(PairError::Storage)?;
        g.devices = next;
        g.code = None;
        Ok((dev.id, token))
    }

    /// The device a token belongs to, if any. Checks every entry so timing
    /// does not reveal which one matched.
    pub fn verify(&self, token: &str) -> Option<Device> {
        if token.len() != 64 || !token.bytes().all(|b| b.is_ascii_hexdigit()) {
            return None;
        }
        let want = token_hash(token);
        let g = self.inner.lock().unwrap();
        let mut found = None;
        for d in &g.devices {
            if ct_eq(d.token_hash.as_bytes(), want.as_bytes()) {
                found = Some(d.clone());
            }
        }
        found
    }

    pub fn exists(&self, id: &str) -> bool {
        self.inner
            .lock()
            .unwrap()
            .devices
            .iter()
            .any(|d| d.id == id)
    }

    pub fn touch(&self, id: &str) {
        let mut g = self.inner.lock().unwrap();
        if let Some(d) = g.devices.iter_mut().find(|d| d.id == id) {
            d.last_seen = now_unix();
        }
    }

    pub fn devices(&self) -> Vec<Device> {
        self.inner.lock().unwrap().devices.clone()
    }

    /// Remove one device (or all with `None`). Returns how many were removed.
    pub fn revoke(&self, id: Option<&str>) -> Result<usize, String> {
        let mut g = self.inner.lock().unwrap();
        let next: Vec<Device> = match id {
            Some(id) => g.devices.iter().filter(|d| d.id != id).cloned().collect(),
            None => Vec::new(),
        };
        let removed = g.devices.len() - next.len();
        if removed > 0 {
            Auth::save(&self.path, &next)?;
            g.devices = next;
        }
        Ok(removed)
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn store() -> (tempdir::Dir, Auth) {
        let d = tempdir::Dir::new();
        let a = Auth::open(&d.path().join("devices.json")).unwrap();
        (d, a)
    }

    pub mod tempdir {
        use std::path::{Path, PathBuf};
        pub struct Dir(PathBuf);
        impl Dir {
            pub fn new() -> Dir {
                let mut b = [0u8; 6];
                super::super::random_bytes(&mut b).unwrap();
                let p =
                    std::env::temp_dir().join(format!("taos-input-test-{}", super::super::hex(&b)));
                std::fs::create_dir_all(&p).unwrap();
                Dir(p)
            }
            pub fn path(&self) -> &Path {
                &self.0
            }
        }
        impl Drop for Dir {
            fn drop(&mut self) {
                let _ = std::fs::remove_dir_all(&self.0);
            }
        }
    }

    #[test]
    fn pair_then_verify_and_persist() {
        let (d, a) = store();
        let (code, _) = a.new_code().unwrap();
        assert_eq!(code.len(), 6);
        let (id, token) = a.pair(&code, "My phone").unwrap();
        assert_eq!(a.verify(&token).unwrap().id, id);
        // Survives a restart, and only the hash is on disk.
        let raw = std::fs::read_to_string(d.path().join("devices.json")).unwrap();
        assert!(!raw.contains(&token));
        let again = Auth::open(&d.path().join("devices.json")).unwrap();
        assert_eq!(again.verify(&token).unwrap().name, "My phone");
        let mode = std::fs::metadata(d.path().join("devices.json"))
            .unwrap()
            .permissions()
            .mode();
        assert_eq!(mode & 0o777, 0o600);
    }

    #[test]
    fn wrong_and_malformed_tokens_are_refused() {
        let (_d, a) = store();
        let (code, _) = a.new_code().unwrap();
        let (_, token) = a.pair(&code, "p").unwrap();
        let mut bad = token.clone();
        bad.replace_range(0..1, if token.starts_with('0') { "1" } else { "0" });
        assert!(a.verify(&bad).is_none());
        assert!(a.verify("").is_none());
        assert!(a.verify("not-hex").is_none());
        assert!(a.verify(&token[..63]).is_none());
    }

    #[test]
    fn code_is_single_use() {
        let (_d, a) = store();
        let (code, _) = a.new_code().unwrap();
        a.pair(&code, "p").unwrap();
        assert_eq!(a.pair(&code, "q").unwrap_err(), PairError::NoCodeActive);
    }

    #[test]
    fn code_dies_after_five_wrong_guesses_even_if_the_sixth_is_right() {
        let (_d, a) = store();
        let (code, _) = a.new_code().unwrap();
        let wrong = if code == "000000" { "000001" } else { "000000" };
        for _ in 0..4 {
            assert_eq!(a.pair(wrong, "p").unwrap_err(), PairError::WrongCode);
        }
        assert_eq!(a.pair(wrong, "p").unwrap_err(), PairError::TooManyAttempts);
        assert_eq!(a.pair(&code, "p").unwrap_err(), PairError::NoCodeActive);
        assert!(a.devices().is_empty());
    }

    #[test]
    fn no_code_means_no_pairing() {
        let (_d, a) = store();
        assert_eq!(a.pair("123456", "p").unwrap_err(), PairError::NoCodeActive);
    }

    #[test]
    fn revoke_cuts_a_device_off() {
        let (d, a) = store();
        let (code, _) = a.new_code().unwrap();
        let (id, token) = a.pair(&code, "p").unwrap();
        assert_eq!(a.revoke(Some(&id)).unwrap(), 1);
        assert!(a.verify(&token).is_none());
        assert!(!a.exists(&id));
        let again = Auth::open(&d.path().join("devices.json")).unwrap();
        assert!(again.verify(&token).is_none());
    }

    #[test]
    fn a_corrupt_store_is_an_error_not_a_reset() {
        let d = tempdir::Dir::new();
        let p = d.path().join("devices.json");
        std::fs::write(&p, "{ nope").unwrap();
        assert!(Auth::open(&p).is_err());
        assert_eq!(std::fs::read_to_string(&p).unwrap(), "{ nope");
    }

    #[test]
    fn ct_eq_basics() {
        assert!(ct_eq(b"abc", b"abc"));
        assert!(!ct_eq(b"abc", b"abd"));
        assert!(!ct_eq(b"abc", b"abcd"));
        assert!(!ct_eq(b"", b"a"));
    }
}
