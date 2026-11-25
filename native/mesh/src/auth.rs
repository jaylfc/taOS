// SPDX-License-Identifier: AGPL-3.0-or-later
//! Who may talk to the daemon, and as whom.
//!
//! 1. Connection: the socket is private to the owner (0600 in a 0700
//!    directory) and every connection's peer uid is checked with
//!    SO_PEERCRED against the owner's uid.
//! 2. Principal: `owner` is whoever passed the check above. Any other
//!    principal (`agent:<id>`, `person:<id>`) is accepted only with a
//!    capability token minted by the taOS controller, which authenticates
//!    the agent or person itself. Agents never talk to this socket directly;
//!    they go through the controller, which passes the token along.
//!
//! Token format (version 1), the same on both sides:
//!
//! ```text
//! token   = hex(payload) "." hex(mac)
//! payload = UTF-8 JSON {"v":1,"sub":"agent:scout","exp":<ms since 1970>,"methods":["messages","send"]}
//! mac     = HMAC-SHA256(key, hex(payload))      (ASCII of the hex string)
//! key     = the bytes of <data dir>/controller.key (at least 32 bytes, mode 0600),
//!           written by the controller when it pairs with this daemon
//! ```
//!
//! The daemon accepts a request whose `principal` equals `sub`, whose token
//! has not expired, and whose method is listed in `methods` (`"*"` allows
//! all). With no key file the daemon refuses every non-owner principal.

use hmac::{Hmac, Mac};
use serde::{Deserialize, Serialize};
use sha2::Sha256;
use std::path::Path;

type HmacSha256 = Hmac<Sha256>;

pub const MIN_KEY_BYTES: usize = 32;

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq)]
pub struct Claims {
    pub v: u32,
    pub sub: String,
    pub exp: u64,
    pub methods: Vec<String>,
}

#[derive(Clone)]
pub struct TokenVerifier {
    key: Vec<u8>,
}

fn hex(b: &[u8]) -> String {
    crate::model::to_hex(b)
}

impl TokenVerifier {
    pub fn new(key: Vec<u8>) -> Result<Self, String> {
        if key.len() < MIN_KEY_BYTES {
            return Err(format!(
                "the controller key must be at least {MIN_KEY_BYTES} bytes"
            ));
        }
        Ok(TokenVerifier { key })
    }

    /// Load `controller.key` from the data directory if there is one.
    pub fn from_dir(dir: &Path) -> Result<Option<Self>, String> {
        let p = dir.join("controller.key");
        match std::fs::read(&p) {
            Ok(k) => {
                use std::os::unix::fs::MetadataExt;
                let m = std::fs::metadata(&p).map_err(|e| e.to_string())?;
                if m.mode() & 0o077 != 0 {
                    return Err(format!(
                        "{} must not be readable by anyone but its owner",
                        p.display()
                    ));
                }
                Self::new(k).map(Some)
            }
            Err(e) if e.kind() == std::io::ErrorKind::NotFound => Ok(None),
            Err(e) => Err(e.to_string()),
        }
    }

    fn mac(&self, payload_hex: &str) -> HmacSha256 {
        let mut m = HmacSha256::new_from_slice(&self.key).expect("any key length");
        m.update(payload_hex.as_bytes());
        m
    }

    pub fn mint(&self, sub: &str, exp_ms: u64, methods: &[&str]) -> String {
        let claims = Claims {
            v: 1,
            sub: sub.into(),
            exp: exp_ms,
            methods: methods.iter().map(|s| s.to_string()).collect(),
        };
        let payload = hex(serde_json::to_string(&claims)
            .expect("claims serialise")
            .as_bytes());
        let mac = hex(&self.mac(&payload).finalize().into_bytes());
        format!("{payload}.{mac}")
    }

    /// Check a token for this principal and method at time `now_ms`.
    pub fn verify(
        &self,
        token: &str,
        principal: &str,
        method: &str,
        now_ms: u64,
    ) -> Result<Claims, String> {
        let (payload, mac) = token.split_once('.').ok_or("malformed token")?;
        let mac = crate::model::from_hex(mac).ok_or("malformed token")?;
        self.mac(payload)
            .verify_slice(&mac)
            .map_err(|_| "bad token signature")?;
        let bytes = crate::model::from_hex(payload).ok_or("malformed token")?;
        let c: Claims = serde_json::from_slice(&bytes).map_err(|_| "malformed token")?;
        if c.v != 1 {
            return Err("unsupported token version".into());
        }
        if c.sub != principal {
            return Err("token is for a different principal".into());
        }
        if c.exp <= now_ms {
            return Err("token expired".into());
        }
        if !c.methods.iter().any(|m| m == "*" || m == method) {
            return Err(format!("token does not allow {method}"));
        }
        Ok(c)
    }
}
