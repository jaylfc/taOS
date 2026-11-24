// SPDX-License-Identifier: AGPL-3.0-or-later
//! Session state: the cookie jar and the exportable [`Session`].
//!
//! Nothing here ever prints a cookie value. `Debug` for both [`Session`] and
//! the internal jar is written by hand and redacts every secret.

use std::collections::BTreeMap;
use std::fmt;

use serde::{Deserialize, Serialize};

/// Name of the session cookie minted by `POST /auth/login`.
pub(crate) const SESSION_COOKIE: &str = "taos_session";
/// Name of the CSRF double-submit cookie set by the CSRF middleware.
pub(crate) const CSRF_COOKIE: &str = "csrf_token";

/// An opaque, serialisable snapshot of a signed-in client.
///
/// Obtain one with [`Client::export_session`](crate::Client::export_session)
/// and restore it with [`Client::with_session`](crate::Client::with_session).
/// The crate never writes it anywhere: an app is expected to put it in the OS
/// keyring. The serialised form contains the live session token, so treat it
/// like a password.
///
/// The controller binds a session to a hash of the `User-Agent` it was created
/// with (`AuthManager.create_session` / `validate_session` in
/// `tinyagentos/auth.py`). The snapshot therefore records the exact
/// `User-Agent` string, and a restored client keeps sending it, so a session
/// stays valid across upgrades of this crate even though the default string
/// carries the crate version.
#[derive(Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct Session {
    /// Format version of this struct, for forward compatibility.
    v: u8,
    base_url: String,
    user_agent: String,
    session: String,
    csrf: Option<String>,
}

impl Session {
    pub(crate) fn new(
        base_url: String,
        user_agent: String,
        session: String,
        csrf: Option<String>,
    ) -> Self {
        Self {
            v: 1,
            base_url,
            user_agent,
            session,
            csrf,
        }
    }

    /// The controller base URL this session belongs to.
    pub fn base_url(&self) -> &str {
        &self.base_url
    }

    pub(crate) fn user_agent(&self) -> &str {
        &self.user_agent
    }

    pub(crate) fn token(&self) -> &str {
        &self.session
    }

    pub(crate) fn csrf(&self) -> Option<&str> {
        self.csrf.as_deref()
    }
}

impl fmt::Debug for Session {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        f.debug_struct("Session")
            .field("base_url", &self.base_url)
            .field("user_agent", &self.user_agent)
            .field("session", &"<redacted>")
            .field("csrf", &self.csrf.as_ref().map(|_| "<redacted>"))
            .finish()
    }
}

/// A minimal cookie jar for a single origin.
///
/// The controller only ever sets host-only cookies on `/`, so the jar keys by
/// name and ignores domain, path and flags other than deletion.
#[derive(Default)]
pub(crate) struct Jar {
    cookies: BTreeMap<String, String>,
}

impl Jar {
    /// Absorb one `Set-Cookie` header value.
    pub(crate) fn absorb(&mut self, set_cookie: &str) {
        let mut parts = set_cookie.split(';');
        let Some((name, value)) = parts.next().and_then(|nv| nv.split_once('=')) else {
            return;
        };
        let name = name.trim();
        let value = value.trim().trim_matches('"');
        let mut expired = value.is_empty();
        for attr in parts {
            let attr = attr.trim();
            if let Some(age) = attr
                .split_once('=')
                .filter(|(k, _)| k.eq_ignore_ascii_case("max-age"))
                .map(|(_, v)| v)
            {
                if age.trim().parse::<i64>().map(|a| a <= 0).unwrap_or(false) {
                    expired = true;
                }
            }
        }
        if name.is_empty() {
            return;
        }
        if expired {
            self.cookies.remove(name);
        } else {
            self.cookies.insert(name.to_string(), value.to_string());
        }
    }

    pub(crate) fn set(&mut self, name: &str, value: &str) {
        self.cookies.insert(name.to_string(), value.to_string());
    }

    pub(crate) fn get(&self, name: &str) -> Option<&str> {
        self.cookies.get(name).map(String::as_str)
    }

    pub(crate) fn clear(&mut self) {
        self.cookies.clear();
    }

    /// The `Cookie` request header value, if any cookie is held.
    pub(crate) fn header(&self) -> Option<String> {
        if self.cookies.is_empty() {
            return None;
        }
        Some(
            self.cookies
                .iter()
                .map(|(k, v)| format!("{k}={v}"))
                .collect::<Vec<_>>()
                .join("; "),
        )
    }
}

impl fmt::Debug for Jar {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        f.debug_struct("Jar")
            .field("cookie_names", &self.cookies.keys().collect::<Vec<_>>())
            .finish()
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn set_and_delete() {
        let mut j = Jar::default();
        j.absorb("taos_session=abc; HttpOnly; Path=/; SameSite=strict");
        j.absorb("csrf_token=n.m; Path=/; SameSite=strict");
        assert_eq!(j.get(SESSION_COOKIE), Some("abc"));
        assert_eq!(
            j.header().as_deref(),
            Some("csrf_token=n.m; taos_session=abc")
        );
        j.absorb("taos_session=\"\"; expires=Thu, 01 Jan 1970 00:00:00 GMT; Max-Age=0; Path=/");
        assert_eq!(j.get(SESSION_COOKIE), None);
    }
}
