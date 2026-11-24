// SPDX-License-Identifier: AGPL-3.0-or-later
//! The session layer: mutual authentication and the encrypted channel.
//!
//! Each side owns a long-term P-256 identity key and knows the other's public
//! key (exchanged once, inside the pairing exchange, protected by the PAKE).
//! A connection runs a signed ephemeral ECDH:
//!
//!   phone  -> engine   0x01 | device id (6) | nonce_c (16) | ephemeral public (65)
//!   engine -> phone    0x02 | nonce_s (16) | ephemeral public (65) | signature (64)
//!   phone  -> engine   0x03 | signature (64)
//!   engine -> phone    0x10 frame (counter 0)  ("ready"), then all traffic
//!
//! Both signatures cover the whole transcript (id, both nonces, both ephemeral
//! keys) under a role label, so neither can be replayed or reflected. The
//! channel keys come from HKDF over the ECDH secret with both nonces as salt.
//! The device secret never leaves the phone; the engine holds only the public
//! key. Data frames are AES-256-GCM with a per-direction key, the counter in
//! the nonce and in the associated data, and a strict "next counter only"
//! rule: a replayed, reordered, dropped or tampered frame ends the connection.

use aes_gcm::aead::{Aead, KeyInit, Payload};
use aes_gcm::{Aes256Gcm, Nonce};
use hkdf::Hkdf;
use p256::ecdsa::signature::{Signer, Verifier};
use p256::ecdsa::{Signature, SigningKey, VerifyingKey};
use p256::{PublicKey, SecretKey};
use sha2::{Digest, Sha256};
use std::path::Path;

pub const SUBPROTOCOL: &str = "taos-input.v2";
pub const T_HELLO: u8 = 0x01;
pub const T_SERVER: u8 = 0x02;
pub const T_AUTH: u8 = 0x03;
pub const T_DATA: u8 = 0x10;
pub const MAX_FRAME: usize = 20 * 1024;

#[derive(Debug, PartialEq, Eq)]
pub enum ChanError {
    Short,
    BadType,
    /// Counter is not the next expected one: replay, reorder or a gap.
    Counter,
    /// Authentication tag did not verify: tampered or wrong key.
    Auth,
    Exhausted,
}

fn random32() -> [u8; 32] {
    let mut b = [0u8; 32];
    crate::auth::random_bytes(&mut b).expect("kernel randomness");
    b
}

/// Test helper: a secret key that can be moved around.
#[cfg(test)]
pub struct SecretKeyBox(pub SecretKey);

/// A fresh P-256 secret key.
pub fn new_secret() -> SecretKey {
    loop {
        if let Ok(k) = SecretKey::from_slice(&random32()) {
            return k;
        }
    }
}

pub fn public_bytes(k: &SecretKey) -> [u8; 65] {
    let p = k.public_key().to_sec1_bytes();
    let mut out = [0u8; 65];
    out.copy_from_slice(&p);
    out
}

pub fn parse_public(b: &[u8]) -> Option<PublicKey> {
    PublicKey::from_sec1_bytes(b).ok()
}

pub fn ecdh(secret: &SecretKey, peer: &PublicKey) -> Vec<u8> {
    p256::ecdh::diffie_hellman(secret.to_nonzero_scalar(), peer.as_affine())
        .raw_secret_bytes()
        .to_vec()
}

/// The long-term key of this engine, kept in a 0600 file.
pub struct Identity {
    secret: SecretKey,
}

impl Identity {
    pub fn load_or_create(path: &Path) -> Result<Identity, String> {
        use std::io::Write;
        use std::os::unix::fs::OpenOptionsExt;
        match std::fs::read_to_string(path) {
            Ok(s) => {
                let raw = (0..s.trim().len() / 2)
                    .map(|i| u8::from_str_radix(&s.trim()[2 * i..2 * i + 2], 16))
                    .collect::<Result<Vec<u8>, _>>()
                    .map_err(|_| format!("{} is not valid", path.display()))?;
                let secret = SecretKey::from_slice(&raw)
                    .map_err(|_| format!("{} is not a valid key", path.display()))?;
                Ok(Identity { secret })
            }
            Err(e) if e.kind() == std::io::ErrorKind::NotFound => {
                let secret = new_secret();
                let mut f = std::fs::OpenOptions::new()
                    .write(true)
                    .create_new(true)
                    .mode(0o600)
                    .open(path)
                    .map_err(|e| format!("{}: {e}", path.display()))?;
                f.write_all(crate::auth::hex(&secret.to_bytes()).as_bytes())
                    .map_err(|e| e.to_string())?;
                f.sync_all().map_err(|e| e.to_string())?;
                Ok(Identity { secret })
            }
            Err(e) => Err(format!("cannot read {}: {e}", path.display())),
        }
    }

    pub fn from_secret(secret: SecretKey) -> Identity {
        Identity { secret }
    }

    pub fn public(&self) -> [u8; 65] {
        public_bytes(&self.secret)
    }

    pub fn sign(&self, role: &[u8], transcript: &[u8]) -> [u8; 64] {
        sign(&self.secret, role, transcript)
    }
}

fn labelled(role: &[u8], transcript: &[u8]) -> Vec<u8> {
    let mut m = Vec::with_capacity(role.len() + transcript.len() + 32);
    m.extend_from_slice(b"taos-input/v2/");
    m.extend_from_slice(role);
    m.push(0);
    m.extend_from_slice(transcript);
    m
}

pub fn sign(secret: &SecretKey, role: &[u8], transcript: &[u8]) -> [u8; 64] {
    let sk = SigningKey::from(secret);
    let sig: Signature = sk.sign(&labelled(role, transcript));
    let mut out = [0u8; 64];
    out.copy_from_slice(&sig.to_bytes());
    out
}

pub fn verify(public: &[u8], role: &[u8], transcript: &[u8], sig: &[u8]) -> bool {
    let Some(pk) = parse_public(public) else {
        return false;
    };
    let Ok(sig) = Signature::from_slice(sig) else {
        return false;
    };
    VerifyingKey::from(&pk)
        .verify(&labelled(role, transcript), &sig)
        .is_ok()
}

pub const ROLE_SERVER: &[u8] = b"server";
pub const ROLE_DEVICE: &[u8] = b"device";

pub fn transcript(
    id: &[u8; 6],
    nc: &[u8; 16],
    ns: &[u8; 16],
    ec: &[u8; 65],
    es: &[u8; 65],
) -> Vec<u8> {
    let mut t = Vec::with_capacity(6 + 32 + 130);
    t.extend_from_slice(id);
    t.extend_from_slice(nc);
    t.extend_from_slice(ns);
    t.extend_from_slice(ec);
    t.extend_from_slice(es);
    t
}

/// One end of the encrypted channel.
pub struct Channel {
    send: Aes256Gcm,
    recv: Aes256Gcm,
    send_dir: u32,
    recv_dir: u32,
    send_ctr: u64,
    recv_ctr: u64,
}

impl Channel {
    /// `server` selects which of the two derived keys is for sending.
    pub fn new(shared: &[u8], nc: &[u8; 16], ns: &[u8; 16], server: bool) -> Channel {
        let mut salt = Sha256::new();
        salt.update(nc);
        salt.update(ns);
        let hk = Hkdf::<Sha256>::new(Some(&salt.finalize()), shared);
        let mut okm = [0u8; 64];
        hk.expand(b"taos-input/v2/keys", &mut okm)
            .expect("64 bytes");
        let c2s = Aes256Gcm::new_from_slice(&okm[..32]).expect("32 bytes");
        let s2c = Aes256Gcm::new_from_slice(&okm[32..]).expect("32 bytes");
        if server {
            Channel {
                send: s2c,
                recv: c2s,
                send_dir: 1,
                recv_dir: 0,
                send_ctr: 0,
                recv_ctr: 0,
            }
        } else {
            Channel {
                send: c2s,
                recv: s2c,
                send_dir: 0,
                recv_dir: 1,
                send_ctr: 0,
                recv_ctr: 0,
            }
        }
    }

    fn nonce(dir: u32, ctr: u64) -> [u8; 12] {
        let mut n = [0u8; 12];
        n[..4].copy_from_slice(&dir.to_be_bytes());
        n[4..].copy_from_slice(&ctr.to_be_bytes());
        n
    }

    /// Encrypt one message into a data frame.
    pub fn seal(&mut self, plaintext: &[u8]) -> Result<Vec<u8>, ChanError> {
        if self.send_ctr == u64::MAX {
            return Err(ChanError::Exhausted);
        }
        let mut head = [0u8; 9];
        head[0] = T_DATA;
        head[1..].copy_from_slice(&self.send_ctr.to_be_bytes());
        let ct = self
            .send
            .encrypt(
                Nonce::from_slice(&Self::nonce(self.send_dir, self.send_ctr)),
                Payload {
                    msg: plaintext,
                    aad: &head,
                },
            )
            .map_err(|_| ChanError::Auth)?;
        self.send_ctr += 1;
        let mut frame = head.to_vec();
        frame.extend_from_slice(&ct);
        Ok(frame)
    }

    /// Decrypt one data frame. Only the very next counter is accepted; on any
    /// error the caller must close the connection.
    pub fn open(&mut self, frame: &[u8]) -> Result<Vec<u8>, ChanError> {
        if frame.len() < 9 + 16 {
            return Err(ChanError::Short);
        }
        if frame[0] != T_DATA {
            return Err(ChanError::BadType);
        }
        let mut c = [0u8; 8];
        c.copy_from_slice(&frame[1..9]);
        let ctr = u64::from_be_bytes(c);
        if ctr != self.recv_ctr {
            return Err(ChanError::Counter);
        }
        let pt = self
            .recv
            .decrypt(
                Nonce::from_slice(&Self::nonce(self.recv_dir, ctr)),
                Payload {
                    msg: &frame[9..],
                    aad: &frame[..9],
                },
            )
            .map_err(|_| ChanError::Auth)?;
        self.recv_ctr += 1;
        Ok(pt)
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn pair() -> (Channel, Channel) {
        let (a, b) = (new_secret(), new_secret());
        let shared = ecdh(&a, &b.public_key());
        assert_eq!(shared, ecdh(&b, &a.public_key()));
        let (nc, ns) = ([1u8; 16], [2u8; 16]);
        (
            Channel::new(&shared, &nc, &ns, false),
            Channel::new(&shared, &nc, &ns, true),
        )
    }

    #[test]
    fn round_trip_both_directions() {
        let (mut c, mut s) = pair();
        let f = c.seal(b"hello").unwrap();
        assert_eq!(s.open(&f).unwrap(), b"hello");
        let g = s.seal(b"world").unwrap();
        assert_eq!(c.open(&g).unwrap(), b"world");
        assert!(
            !f.windows(5).any(|w| w == b"hello"),
            "plaintext is not on the wire"
        );
    }

    #[test]
    fn a_replayed_frame_is_refused() {
        let (mut c, mut s) = pair();
        let f = c.seal(b"one").unwrap();
        s.open(&f).unwrap();
        assert_eq!(s.open(&f), Err(ChanError::Counter));
    }

    #[test]
    fn reordered_and_skipped_frames_are_refused() {
        let (mut c, mut s) = pair();
        let f0 = c.seal(b"0").unwrap();
        let f1 = c.seal(b"1").unwrap();
        let f2 = c.seal(b"2").unwrap();
        assert_eq!(s.open(&f1), Err(ChanError::Counter), "skipping ahead");
        s.open(&f0).unwrap();
        assert_eq!(s.open(&f2), Err(ChanError::Counter), "gap");
        s.open(&f1).unwrap();
        s.open(&f2).unwrap();
    }

    #[test]
    fn a_tampered_frame_is_refused() {
        let (mut c, mut s) = pair();
        let f = c.seal(b"click").unwrap();
        for i in 0..f.len() {
            let mut bad = f.clone();
            bad[i] ^= 1;
            let r = s.open(&bad);
            assert!(r.is_err(), "flip at byte {i} was accepted");
        }
        // The counter did not advance on the failures: the real frame still works.
        assert_eq!(s.open(&f).unwrap(), b"click");
    }

    #[test]
    fn a_frame_from_another_key_or_direction_is_refused() {
        let (mut c, mut s) = pair();
        let (mut c2, _) = pair();
        let f = c2.seal(b"x").unwrap();
        assert_eq!(s.open(&f), Err(ChanError::Auth));
        // Reflecting the server's own frame back at it fails (direction keys differ).
        let g = s.seal(b"y").unwrap();
        assert!(s.open(&g).is_err());
        assert!(c.open(&g).is_ok());
    }

    #[test]
    fn short_and_mistyped_frames_are_refused() {
        let (_, mut s) = pair();
        assert_eq!(s.open(&[T_DATA; 10]), Err(ChanError::Short));
        let mut f = vec![0x99u8; 40];
        f[0] = 0x99;
        assert_eq!(s.open(&f), Err(ChanError::BadType));
    }

    #[test]
    fn signatures_bind_role_and_transcript() {
        let k = new_secret();
        let t = transcript(&[1; 6], &[2; 16], &[3; 16], &[4; 65], &[5; 65]);
        let sig = sign(&k, ROLE_DEVICE, &t);
        let pubk = public_bytes(&k);
        assert!(verify(&pubk, ROLE_DEVICE, &t, &sig));
        assert!(
            !verify(&pubk, ROLE_SERVER, &t, &sig),
            "a device signature is not a server signature"
        );
        let mut t2 = t.clone();
        t2[0] ^= 1;
        assert!(!verify(&pubk, ROLE_DEVICE, &t2, &sig));
        let other = public_bytes(&new_secret());
        assert!(!verify(&other, ROLE_DEVICE, &t, &sig), "wrong key");
        assert!(!verify(&pubk, ROLE_DEVICE, &t, &sig[..63]));
        assert!(!verify(&[0u8; 65], ROLE_DEVICE, &t, &sig));
    }

    #[test]
    fn identity_is_created_once_with_private_permissions() {
        use std::os::unix::fs::PermissionsExt;
        let d = std::env::temp_dir().join(format!("taos-id-{}", std::process::id()));
        std::fs::create_dir_all(&d).unwrap();
        let p = d.join("identity.key");
        let a = Identity::load_or_create(&p).unwrap();
        let b = Identity::load_or_create(&p).unwrap();
        assert_eq!(a.public(), b.public());
        assert_eq!(
            std::fs::metadata(&p).unwrap().permissions().mode() & 0o777,
            0o600
        );
        std::fs::write(&p, "zz").unwrap();
        assert!(
            Identity::load_or_create(&p).is_err(),
            "a damaged key is an error, not a new identity"
        );
        std::fs::remove_dir_all(&d).ok();
    }
}
