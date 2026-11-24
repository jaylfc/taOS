// SPDX-License-Identifier: AGPL-3.0-or-later
//! Sign-in, sign-out and the current user.
//!
//! Routes (all in `tinyagentos/routes/auth.py`, router prefix `/auth`):
//! `POST /auth/login` (JSON form), `POST /auth/pin-login`, `POST /auth/logout`,
//! `GET /auth/me`.

use serde::{Deserialize, Serialize};
use serde_json::json;

use crate::client::{Client, Req};
use crate::error::{Error, Result};
use crate::session::SESSION_COOKIE;
use crate::util::Timestamp;

/// The public profile of a user (`AuthManager._public_user`).
#[derive(Debug, Clone, PartialEq, Deserialize, Serialize, Default)]
pub struct User {
    /// Stable user id.
    #[serde(default)]
    pub id: String,
    /// Login name.
    #[serde(default)]
    pub username: String,
    /// Display name.
    #[serde(default)]
    pub full_name: String,
    /// Email address, possibly empty.
    #[serde(default)]
    pub email: String,
    /// Administrators may download and delete models and restart AI services.
    #[serde(default)]
    pub is_admin: bool,
    /// True while an invited user has not finished onboarding.
    #[serde(default)]
    pub pending: bool,
    /// Last sign-in time.
    #[serde(default)]
    pub last_login_at: Option<Timestamp>,
    /// Account creation time.
    #[serde(default)]
    pub created_at: Option<Timestamp>,
    /// Capability names granted to the user.
    #[serde(default, deserialize_with = "crate::util::null_default")]
    pub capabilities: Vec<String>,
    /// Whether a PIN exists (never the PIN itself).
    #[serde(default)]
    pub has_pin: bool,
}

/// The outcome of a successful sign-in.
#[derive(Debug, Clone, PartialEq)]
pub struct LoginOutcome {
    /// The signed-in user.
    pub user: User,
    /// True when the user signed in with an invite code and must still finish
    /// onboarding.
    pub needs_onboarding: bool,
}

#[derive(Deserialize)]
struct LoginBody {
    #[serde(default)]
    user: Option<User>,
    #[serde(default)]
    needs_onboarding: bool,
}

impl Client {
    /// Sign in with a password. Calls `POST /auth/login` with a JSON body, the
    /// variant meant for programmatic clients (the form variant redirects).
    ///
    /// `username` may be `None` on a single-user controller. `auto_login`
    /// requests a long-lived session; `None` lets the server choose (long-lived
    /// on single-user installs, short on multi-user ones).
    ///
    /// On success the session and CSRF cookies are held by this client; use
    /// [`Client::export_session`] to persist them. Wrong credentials give
    /// [`Error::Unauthenticated`]; too many attempts give [`Error::RateLimited`].
    pub async fn login(
        &self,
        username: Option<&str>,
        password: &str,
        auto_login: Option<bool>,
    ) -> Result<LoginOutcome> {
        // A stale cookie must not ride along: the server would clear it anyway.
        self.forget_session();
        let mut body = json!({ "password": password });
        if let Some(u) = username.filter(|u| !u.is_empty()) {
            body["username"] = json!(u);
        }
        if let Some(a) = auto_login {
            body["auto_login"] = json!(a);
        }
        self.finish_login(Req::post("/auth/login").json_body(&body)?)
            .await
    }

    /// Sign in with the console PIN. Calls `POST /auth/pin-login`. The server
    /// refuses (404 "PIN sign-in is not available") unless the request comes
    /// from the device's own screen: a loopback peer and no forwarding header
    /// (`is_console_origin` in `tinyagentos/auth.py`). Unlike the other
    /// `/auth/lock-*` routes, this one does not require the `X-taOS-Console`
    /// header, so none is sent.
    pub async fn pin_login(&self, username: Option<&str>, pin: &str) -> Result<LoginOutcome> {
        self.forget_session();
        let mut body = json!({ "pin": pin });
        if let Some(u) = username.filter(|u| !u.is_empty()) {
            body["username"] = json!(u);
        }
        self.finish_login(Req::post("/auth/pin-login").json_body(&body)?)
            .await
    }

    async fn finish_login(&self, req: Req) -> Result<LoginOutcome> {
        let resp = self.send(&req).await?;
        let bytes = resp.bytes().await.map_err(Error::Transport)?;
        let body: LoginBody = crate::client::decode(&bytes).map_err(Error::without_body)?;
        if !self.is_signed_in() {
            return Err(Error::Protocol(format!(
                "login succeeded but the server set no {SESSION_COOKIE} cookie"
            )));
        }
        Ok(LoginOutcome {
            user: body.user.unwrap_or_default(),
            needs_onboarding: body.needs_onboarding,
        })
    }

    /// Sign out. Calls `POST /auth/logout` (which needs the CSRF header and
    /// answers with a 303 to the login page, treated as success) and then
    /// forgets the local session whatever the server said.
    pub async fn logout(&self) -> Result<()> {
        let result = self.send(&Req::post("/auth/logout").redirect_ok()).await;
        self.forget_session();
        match result {
            Ok(_) => Ok(()),
            // Already signed out is the state the caller wanted.
            Err(Error::Unauthenticated { .. }) => Ok(()),
            Err(e) => Err(e),
        }
    }

    /// The signed-in user. Calls `GET /auth/me`; [`Error::Unauthenticated`]
    /// means the session is gone and the UI should show the lock screen.
    pub async fn me(&self) -> Result<User> {
        #[derive(Deserialize)]
        struct Me {
            user: User,
        }
        Ok(self.json::<Me>(Req::get("/auth/me")).await?.user)
    }
}
