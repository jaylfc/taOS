// SPDX-License-Identifier: AGPL-3.0-or-later
//! Password authenticated key exchange for pairing (CPace style, ristretto255).
//!
//! The six digit code never travels. Both sides turn code and a server chosen
//! session id into a group generator with the ristretto255 one-way map
//! (`from_uniform_bytes` here, `RistrettoPoint.hashToCurve` in noble-curves on
//! the phone; same map, pinned by a known answer test), exchange one point
//! each, and derive the same key only if they used the same code. Key
//! confirmation MACs prove it in both directions. An eavesdropper sees two
//! group elements and two MACs and can test no guess offline; an active
//! attacker gets exactly one guess per exchange, and the engine counts every
//! exchange (see `auth.rs`).
//!
//! Group and hash maths come from curve25519-dalek, sha2, hkdf and hmac; this
//! file only composes them.

use curve25519_dalek::ristretto::{CompressedRistretto, RistrettoPoint};
use curve25519_dalek::scalar::Scalar;
use curve25519_dalek::traits::IsIdentity;
use hkdf::Hkdf;
use hmac::{Hmac, Mac};
use sha2::{Digest, Sha256, Sha512};

const DSI_G: &[u8] = b"taos-input/cpace-v1/generator";
const DSI_T: &[u8] = b"taos-input/cpace-v1/transcript";

#[derive(Debug, PartialEq, Eq)]
pub enum PakeError {
    BadPoint,
    Random,
}

/// Keys both sides end up with when (and only when) the codes matched.
#[derive(Clone, PartialEq, Eq)]
pub struct Keys {
    pub confirm_s: [u8; 32],
    pub confirm_c: [u8; 32],
    pub enc_c2s: [u8; 32],
    pub enc_s2c: [u8; 32],
}

/// The generator for this code and session: a hash of both onto the group.
pub fn generator(code: &str, sid: &[u8; 16]) -> RistrettoPoint {
    let mut h = Sha512::new();
    h.update(DSI_G);
    h.update((code.len() as u32).to_be_bytes());
    h.update(code.as_bytes());
    h.update(sid);
    let wide: [u8; 64] = h.finalize().into();
    RistrettoPoint::from_uniform_bytes(&wide)
}

fn random_scalar() -> Result<Scalar, PakeError> {
    loop {
        let mut b = [0u8; 64];
        crate::auth::random_bytes(&mut b).map_err(|_| PakeError::Random)?;
        let s = Scalar::from_bytes_mod_order_wide(&b);
        if s != Scalar::ZERO {
            return Ok(s);
        }
    }
}

fn decompress(b: &[u8; 32]) -> Result<RistrettoPoint, PakeError> {
    let p = CompressedRistretto(*b)
        .decompress()
        .ok_or(PakeError::BadPoint)?;
    if p.is_identity() {
        return Err(PakeError::BadPoint);
    }
    Ok(p)
}

fn derive(
    k: &RistrettoPoint,
    sid: &[u8; 16],
    ya: &[u8; 32],
    yb: &[u8; 32],
) -> Result<Keys, PakeError> {
    if k.is_identity() {
        return Err(PakeError::BadPoint);
    }
    let mut t = Sha256::new();
    t.update(DSI_T);
    t.update(sid);
    t.update(ya);
    t.update(yb);
    let transcript: [u8; 32] = t.finalize().into();
    let hk = Hkdf::<Sha256>::new(Some(&transcript), k.compress().as_bytes());
    let mac = |label: &[u8]| -> [u8; 32] {
        let mut okm = [0u8; 32];
        hk.expand(label, &mut okm).expect("32 bytes");
        okm
    };
    // The confirmation values bind the transcript, not just the key.
    let tag = |key: [u8; 32]| -> [u8; 32] {
        let mut m = <Hmac<Sha256> as Mac>::new_from_slice(&key).expect("any key length");
        m.update(&transcript);
        m.finalize().into_bytes().into()
    };
    Ok(Keys {
        confirm_s: tag(mac(b"confirm-s")),
        confirm_c: tag(mac(b"confirm-c")),
        enc_c2s: mac(b"enc-c2s"),
        enc_s2c: mac(b"enc-s2c"),
    })
}

/// Server side: given the phone's point, return ours and the keys.
pub fn respond(code: &str, sid: &[u8; 16], ya: &[u8; 32]) -> Result<([u8; 32], Keys), PakeError> {
    decompress(ya)?;
    let g = generator(code, sid);
    let yb_s = random_scalar()?;
    let yb = (g * yb_s).compress().to_bytes();
    let k = decompress(ya)? * yb_s;
    Ok((yb, derive(&k, sid, ya, &yb)?))
}

/// Phone side (the reference client used by tests; the page has its own in JS).
pub struct ClientHello {
    secret: Scalar,
    pub ya: [u8; 32],
    sid: [u8; 16],
}

pub fn client_start(code: &str, sid: &[u8; 16]) -> Result<ClientHello, PakeError> {
    let g = generator(code, sid);
    let secret = random_scalar()?;
    Ok(ClientHello {
        secret,
        ya: (g * secret).compress().to_bytes(),
        sid: *sid,
    })
}

impl ClientHello {
    pub fn finish(&self, yb: &[u8; 32]) -> Result<Keys, PakeError> {
        let k = decompress(yb)? * self.secret;
        derive(&k, &self.sid, &self.ya, yb)
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn same_code_same_keys() {
        let sid = [9u8; 16];
        let c = client_start("123456", &sid).unwrap();
        let (yb, sk) = respond("123456", &sid, &c.ya).unwrap();
        assert!(sk == c.finish(&yb).unwrap());
    }

    #[test]
    fn different_code_different_keys_and_nothing_matches() {
        let sid = [9u8; 16];
        let c = client_start("123456", &sid).unwrap();
        let (yb, sk) = respond("654321", &sid, &c.ya).unwrap();
        let ck = c.finish(&yb).unwrap();
        assert!(
            sk.confirm_c != ck.confirm_c
                && sk.confirm_s != ck.confirm_s
                && sk.enc_c2s != ck.enc_c2s
        );
    }

    #[test]
    fn a_different_session_id_gives_a_different_generator() {
        assert!(generator("123456", &[1; 16]) != generator("123456", &[2; 16]));
    }

    #[test]
    fn identity_and_garbage_points_are_refused() {
        let sid = [1u8; 16];
        assert_eq!(
            respond("123456", &sid, &[0u8; 32]).err(),
            Some(PakeError::BadPoint)
        );
        assert_eq!(
            respond("123456", &sid, &[0xffu8; 32]).err(),
            Some(PakeError::BadPoint)
        );
        let c = client_start("123456", &sid).unwrap();
        assert_eq!(c.finish(&[0u8; 32]).err(), Some(PakeError::BadPoint));
    }

    /// Known answers shared with the phone side (noble-curves 1.9.7): if the
    /// two libraries ever disagreed on the map to the group, pairing would
    /// silently fail for every phone; this pins them to the same values.
    #[test]
    fn ristretto_map_matches_the_phone_library() {
        let p = RistrettoPoint::from_uniform_bytes(&[7u8; 64]);
        assert_eq!(
            hex(p.compress().as_bytes()),
            "7a2dedd75f9b56de5b75abcefb7c4f393cf584aa630fb86c01940859ea33fb44"
        );
        let five = p * Scalar::from(5u64);
        assert_eq!(
            hex(five.compress().as_bytes()),
            "48e52bc973ea54c454226f548ac1094fe255cd714a5a5c2d7cfbdbd108710009"
        );
    }

    fn hex(b: &[u8]) -> String {
        crate::auth::hex(b)
    }
}
