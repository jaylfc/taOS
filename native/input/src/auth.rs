// SPDX-License-Identifier: AGPL-3.0-or-later
//! Pairing and the paired device list.
//!
//! A phone pairs once with a short code that only the owner's own session can
//! mint (through the control socket, never over the network). The code is
//! never sent: pairing is a password authenticated key exchange (`pake.rs`),
//! at the end of which the phone's public key is stored here, together with
//! the phone's name. The engine holds no device secret at all. Every PAKE
//! exchange is one guess at the code, so each is counted: per source address
//! (so one noisy device on the network cannot burn the owner's code) and in
//! total. The owner can always ask for a fresh code.

use serde::{Deserialize, Serialize};
use std::collections::HashMap;
use std::fs;
use std::io::{Read, Write};
use std::net::IpAddr;
use std::os::unix::fs::{OpenOptionsExt, PermissionsExt};
use std::path::{Path, PathBuf};
use std::sync::Mutex;
use std::time::{Duration, Instant, SystemTime, UNIX_EPOCH};

pub const CODE_TTL: Duration = Duration::from_secs(300);
/// Guesses allowed per code in total, and per source address.
pub const MAX_CODE_ATTEMPTS: u32 = 12;
pub const MAX_SOURCE_ATTEMPTS: u32 = 3;
/// Pairing exchanges one source may start per minute.
pub const MAX_STARTS_PER_MIN: usize = 20;
const PENDING_TTL: Duration = Duration::from_secs(30);
pub const MAX_DEVICES: usize = 16;
pub const MAX_NAME: usize = 40;

/// Cryptographically random bytes from the kernel.
pub fn random_bytes(buf: &mut [u8]) -> std::io::Result<()> {
    fs::File::open("/dev/urandom")?.read_exact(buf)
}

pub fn hex(bytes: &[u8]) -> String {
    bytes.iter().map(|b| format!("{b:02x}")).collect()
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
    /// The phone's P-256 public key, uncompressed SEC1, hex.
    pub pubkey: String,
    pub created: u64,
    pub last_seen: u64,
}

#[derive(Debug, PartialEq, Eq)]
pub enum PairError {
    NoCodeActive,
    Expired,
    /// This source (or the code as a whole) has used up its guesses.
    TooManyAttempts,
    RateLimited,
    WrongCode,
    BadRequest,
    UnknownSession,
    TooManyDevices,
    Storage(String),
}

impl std::fmt::Display for PairError {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        match self {
            PairError::NoCodeActive => {
                write!(f, "no pairing code is active; ask the desktop for one")
            }
            PairError::Expired => write!(
                f,
                "the pairing code has expired; ask the desktop for a new one"
            ),
            PairError::TooManyAttempts => {
                write!(f, "too many tries; ask the desktop for a new code")
            }
            PairError::RateLimited => write!(f, "slow down"),
            PairError::WrongCode => write!(f, "that code is not right"),
            PairError::BadRequest => write!(f, "bad request"),
            PairError::UnknownSession => {
                write!(f, "that pairing attempt is not known; start again")
            }
            PairError::TooManyDevices => write!(f, "too many paired devices; remove one first"),
            PairError::Storage(e) => write!(f, "could not save the device: {e}"),
        }
    }
}

struct Code {
    value: String,
    expires: Instant,
    attempts: u32,
    per_source: HashMap<IpAddr, u32>,
}

struct Pending {
    sid: [u8; 16],
    src: IpAddr,
    created: Instant,
    keys: Option<crate::pake::Keys>,
}

struct Inner {
    devices: Vec<Device>,
    code: Option<Code>,
    pending: HashMap<String, Pending>,
    starts: HashMap<IpAddr, Vec<Instant>>,
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

fn seal_once(key: &[u8; 32], aad: &[u8], pt: &[u8]) -> Result<Vec<u8>, PairError> {
    use aes_gcm::aead::{Aead, KeyInit, Payload};
    let c = aes_gcm::Aes256Gcm::new_from_slice(key).map_err(|_| PairError::BadRequest)?;
    c.encrypt(
        aes_gcm::Nonce::from_slice(&[0u8; 12]),
        Payload { msg: pt, aad },
    )
    .map_err(|_| PairError::BadRequest)
}

/// Each pairing key is used for exactly one message, so a zero nonce is safe.
pub fn open_once(key: &[u8; 32], aad: &[u8], ct: &[u8]) -> Result<Vec<u8>, PairError> {
    use aes_gcm::aead::{Aead, KeyInit, Payload};
    let c = aes_gcm::Aes256Gcm::new_from_slice(key).map_err(|_| PairError::BadRequest)?;
    c.decrypt(
        aes_gcm::Nonce::from_slice(&[0u8; 12]),
        Payload { msg: ct, aad },
    )
    .map_err(|_| PairError::BadRequest)
}

pub const AAD_C2S: &[u8] = b"taos-input/pair/device";
pub const AAD_S2C: &[u8] = b"taos-input/pair/engine";

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
                pending: HashMap::new(),
                starts: HashMap::new(),
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

    /// Mint a fresh six digit pairing code, replacing any earlier one (and
    /// forgetting the guesses spent on it).
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
            per_source: HashMap::new(),
        });
        g.pending.clear();
        Ok((value, CODE_TTL))
    }

    fn live_code(g: &mut Inner) -> Result<&mut Code, PairError> {
        let expired = matches!(&g.code, Some(c) if Instant::now() >= c.expires);
        if expired {
            g.code = None;
            return Err(PairError::Expired);
        }
        g.code.as_mut().ok_or(PairError::NoCodeActive)
    }

    /// Step 1: a phone asks to pair. Returns the exchange id and the session
    /// id the generator is bound to.
    pub fn pair_begin(&self, src: IpAddr) -> Result<(String, [u8; 16]), PairError> {
        let mut g = self.inner.lock().unwrap();
        let now = Instant::now();
        g.pending
            .retain(|_, p| now.duration_since(p.created) < PENDING_TTL);
        Auth::live_code(&mut g)?;
        let starts = g.starts.entry(src).or_default();
        starts.retain(|t| now.duration_since(*t) < Duration::from_secs(60));
        if starts.len() >= MAX_STARTS_PER_MIN {
            return Err(PairError::RateLimited);
        }
        starts.push(now);
        if g.pending.values().filter(|p| p.src == src).count() >= 4 || g.pending.len() >= 16 {
            return Err(PairError::RateLimited);
        }
        let mut sid = [0u8; 16];
        let mut pid = [0u8; 12];
        random_bytes(&mut sid).map_err(|e| PairError::Storage(e.to_string()))?;
        random_bytes(&mut pid).map_err(|e| PairError::Storage(e.to_string()))?;
        let pid = hex(&pid);
        g.pending.insert(
            pid.clone(),
            Pending {
                sid,
                src,
                created: now,
                keys: None,
            },
        );
        Ok((pid, sid))
    }

    /// Step 2: the phone's PAKE point. This is one guess at the code and is
    /// counted before anything is computed.
    pub fn pair_init(
        &self,
        src: IpAddr,
        pid: &str,
        ya: &[u8; 32],
    ) -> Result<([u8; 32], [u8; 32]), PairError> {
        let mut g = self.inner.lock().unwrap();
        let sid = match g.pending.get(pid) {
            Some(p) if p.src == src && p.keys.is_none() => p.sid,
            _ => return Err(PairError::UnknownSession),
        };
        let code = Auth::live_code(&mut g)?;
        let mine = code.per_source.entry(src).or_insert(0);
        if *mine >= MAX_SOURCE_ATTEMPTS || code.attempts >= MAX_CODE_ATTEMPTS {
            g.pending.remove(pid);
            return Err(PairError::TooManyAttempts);
        }
        *mine += 1;
        code.attempts += 1;
        let value = code.value.clone();
        match crate::pake::respond(&value, &sid, ya) {
            Ok((yb, keys)) => {
                let cs = keys.confirm_s;
                if let Some(p) = g.pending.get_mut(pid) {
                    p.keys = Some(keys);
                }
                Ok((yb, cs))
            }
            Err(_) => {
                g.pending.remove(pid);
                Err(PairError::BadRequest)
            }
        }
    }

    /// Step 3: the phone proves it knew the code and sends its public key
    /// (sealed under the exchange key). On success the device is stored, the
    /// code is spent, and the engine's public key is returned, sealed the same way.
    pub fn pair_finish(
        &self,
        src: IpAddr,
        pid: &str,
        confirm_c: &[u8],
        sealed: &[u8],
        engine_public: &[u8; 65],
    ) -> Result<(String, Vec<u8>), PairError> {
        let mut g = self.inner.lock().unwrap();
        let p = match g.pending.remove(pid) {
            Some(p) if p.src == src && p.keys.is_some() => p,
            _ => return Err(PairError::UnknownSession),
        };
        let keys = p.keys.expect("checked above");
        if !ct_eq(&keys.confirm_c, confirm_c) {
            return Err(PairError::WrongCode);
        }
        Auth::live_code(&mut g)?;
        let plain = open_once(&keys.enc_c2s, AAD_C2S, sealed)?;
        if plain.len() < 66 || crate::secure::parse_public(&plain[..65]).is_none() {
            return Err(PairError::BadRequest);
        }
        let name: String = String::from_utf8_lossy(&plain[65..])
            .chars()
            .filter(|c| !c.is_control())
            .take(MAX_NAME)
            .collect();
        if g.devices.len() >= MAX_DEVICES {
            return Err(PairError::TooManyDevices);
        }
        let mut idb = [0u8; 6];
        random_bytes(&mut idb).map_err(|e| PairError::Storage(e.to_string()))?;
        let dev = Device {
            id: hex(&idb),
            name: if name.trim().is_empty() {
                "Phone".into()
            } else {
                name
            },
            pubkey: hex(&plain[..65]),
            created: now_unix(),
            last_seen: 0,
        };
        let mut next = g.devices.clone();
        next.push(dev.clone());
        Auth::save(&self.path, &next).map_err(PairError::Storage)?;
        g.devices = next;
        g.code = None;
        g.pending.clear();
        let reply = seal_once(&keys.enc_s2c, AAD_S2C, engine_public)?;
        Ok((dev.id, reply))
    }

    pub fn device(&self, id: &str) -> Option<Device> {
        self.inner
            .lock()
            .unwrap()
            .devices
            .iter()
            .find(|d| d.id == id)
            .cloned()
    }

    pub fn exists(&self, id: &str) -> bool {
        self.device(id).is_some()
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
    use crate::pake;
    use crate::secure;

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

    fn store() -> (tempdir::Dir, Auth) {
        let d = tempdir::Dir::new();
        let a = Auth::open(&d.path().join("devices.json")).unwrap();
        (d, a)
    }

    fn ip(s: &str) -> IpAddr {
        s.parse().unwrap()
    }

    const ENGINE_PUB: [u8; 65] = [4u8; 65];

    /// Run the whole exchange as a phone that types `code`. Returns the engine
    /// side's verdict and the phone's key if it succeeded.
    fn pair_as(
        a: &Auth,
        src: IpAddr,
        code: &str,
        name: &str,
    ) -> Result<(String, secure::SecretKeyBox), PairError> {
        let (pid, sid) = a.pair_begin(src)?;
        let c = pake::client_start(code, &sid).unwrap();
        let (yb, cs) = a.pair_init(src, &pid, &c.ya)?;
        let keys = c.finish(&yb).unwrap();
        assert_eq!(
            keys.confirm_s == cs,
            code == a
                .inner
                .lock()
                .unwrap()
                .code
                .as_ref()
                .map(|c| c.value.clone())
                .unwrap_or_default()
        );
        let sk = secure::new_secret();
        let mut plain = secure::public_bytes(&sk).to_vec();
        plain.extend_from_slice(name.as_bytes());
        let sealed = seal_once(&keys.enc_c2s, AAD_C2S, &plain).unwrap();
        let (id, reply) = a.pair_finish(src, &pid, &keys.confirm_c, &sealed, &ENGINE_PUB)?;
        assert_eq!(
            open_once(&keys.enc_s2c, AAD_S2C, &reply).unwrap(),
            ENGINE_PUB.to_vec()
        );
        Ok((id, secure::SecretKeyBox(sk)))
    }

    #[test]
    fn pair_then_persist_public_key_only() {
        let (d, a) = store();
        let (code, _) = a.new_code().unwrap();
        let (id, sk) = pair_as(&a, ip("10.0.0.5"), &code, "My phone").unwrap();
        let dev = a.device(&id).unwrap();
        assert_eq!(dev.pubkey, hex(&secure::public_bytes(&sk.0)));
        let raw = std::fs::read_to_string(d.path().join("devices.json")).unwrap();
        assert!(!raw.contains(&hex(&sk.0.to_bytes())), "no secret on disk");
        let again = Auth::open(&d.path().join("devices.json")).unwrap();
        assert_eq!(again.device(&id).unwrap().name, "My phone");
        let mode = std::fs::metadata(d.path().join("devices.json"))
            .unwrap()
            .permissions()
            .mode();
        assert_eq!(mode & 0o777, 0o600);
    }

    #[test]
    fn a_wrong_code_is_refused_at_confirmation_and_stores_nothing() {
        let (_d, a) = store();
        let (code, _) = a.new_code().unwrap();
        let wrong = if code == "000000" { "000001" } else { "000000" };
        assert_eq!(
            pair_as(&a, ip("10.0.0.5"), wrong, "x").err(),
            Some(PairError::WrongCode)
        );
        assert!(a.devices().is_empty());
    }

    #[test]
    fn a_code_is_single_use() {
        let (_d, a) = store();
        let (code, _) = a.new_code().unwrap();
        pair_as(&a, ip("10.0.0.5"), &code, "p").unwrap();
        assert_eq!(
            a.pair_begin(ip("10.0.0.5")).err(),
            Some(PairError::NoCodeActive)
        );
    }

    #[test]
    fn no_code_means_no_pairing() {
        let (_d, a) = store();
        assert_eq!(
            a.pair_begin(ip("10.0.0.5")).err(),
            Some(PairError::NoCodeActive)
        );
    }

    #[test]
    fn one_noisy_source_cannot_burn_the_owners_code() {
        let (_d, a) = store();
        let (code, _) = a.new_code().unwrap();
        let wrong = if code == "000000" { "000001" } else { "000000" };
        let attacker = ip("10.0.0.99");
        for _ in 0..MAX_SOURCE_ATTEMPTS {
            assert_eq!(
                pair_as(&a, attacker, wrong, "x").err(),
                Some(PairError::WrongCode)
            );
        }
        // The attacker is cut off for this code ...
        assert_eq!(
            pair_as(&a, attacker, &code, "x").err(),
            Some(PairError::TooManyAttempts)
        );
        // ... but the owner, from another address, still pairs with the same code.
        pair_as(&a, ip("10.0.0.5"), &code, "Owner phone").unwrap();
    }

    #[test]
    fn many_sources_cannot_exceed_the_total_and_a_new_code_resets_everything() {
        let (_d, a) = store();
        let (code, _) = a.new_code().unwrap();
        let wrong = if code == "000000" { "000001" } else { "000000" };
        for i in 0..MAX_CODE_ATTEMPTS {
            let src = ip(&format!("10.0.1.{}", i + 1));
            assert_eq!(
                pair_as(&a, src, wrong, "x").err(),
                Some(PairError::WrongCode)
            );
        }
        assert_eq!(
            pair_as(&a, ip("10.0.0.5"), &code, "x").err(),
            Some(PairError::TooManyAttempts)
        );
        let (code2, _) = a.new_code().unwrap();
        pair_as(&a, ip("10.0.0.5"), &code2, "x").unwrap();
    }

    #[test]
    fn starts_are_rate_limited_per_source_not_globally() {
        let (_d, a) = store();
        a.new_code().unwrap();
        let noisy = ip("10.0.0.99");
        let mut limited = false;
        for _ in 0..40 {
            if a.pair_begin(noisy).err() == Some(PairError::RateLimited) {
                limited = true;
            }
        }
        assert!(limited);
        assert!(
            a.pair_begin(ip("10.0.0.5")).is_ok(),
            "another source is not affected"
        );
    }

    #[test]
    fn an_exchange_belongs_to_its_source_and_runs_once() {
        let (_d, a) = store();
        let (code, _) = a.new_code().unwrap();
        let (pid, sid) = a.pair_begin(ip("10.0.0.5")).unwrap();
        let c = pake::client_start(&code, &sid).unwrap();
        assert_eq!(
            a.pair_init(ip("10.0.0.6"), &pid, &c.ya).err(),
            Some(PairError::UnknownSession)
        );
        a.pair_init(ip("10.0.0.5"), &pid, &c.ya).unwrap();
        assert_eq!(
            a.pair_init(ip("10.0.0.5"), &pid, &c.ya).err(),
            Some(PairError::UnknownSession),
            "no second guess on one exchange"
        );
    }

    #[test]
    fn a_tampered_blob_or_bad_key_is_refused() {
        let (_d, a) = store();
        let (code, _) = a.new_code().unwrap();
        let src = ip("10.0.0.5");
        let (pid, sid) = a.pair_begin(src).unwrap();
        let c = pake::client_start(&code, &sid).unwrap();
        let (yb, _) = a.pair_init(src, &pid, &c.ya).unwrap();
        let k = c.finish(&yb).unwrap();
        let mut sealed = seal_once(&k.enc_c2s, AAD_C2S, &[4u8; 70]).unwrap(); // not a curve point
        assert_eq!(
            a.pair_finish(src, &pid, &k.confirm_c, &sealed, &ENGINE_PUB)
                .err(),
            Some(PairError::BadRequest)
        );
        let (pid, sid) = a.pair_begin(src).unwrap();
        let c = pake::client_start(&code, &sid).unwrap();
        let (yb, _) = a.pair_init(src, &pid, &c.ya).unwrap();
        let k = c.finish(&yb).unwrap();
        sealed = seal_once(
            &k.enc_c2s,
            AAD_C2S,
            &secure::public_bytes(&secure::new_secret()),
        )
        .unwrap();
        sealed[3] ^= 1;
        assert!(a
            .pair_finish(src, &pid, &k.confirm_c, &sealed, &ENGINE_PUB)
            .is_err());
        assert!(a.devices().is_empty());
    }

    #[test]
    fn revoke_removes_a_device() {
        let (d, a) = store();
        let (code, _) = a.new_code().unwrap();
        let (id, _) = pair_as(&a, ip("10.0.0.5"), &code, "p").unwrap();
        assert_eq!(a.revoke(Some(&id)).unwrap(), 1);
        assert!(!a.exists(&id));
        assert!(Auth::open(&d.path().join("devices.json"))
            .unwrap()
            .device(&id)
            .is_none());
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
