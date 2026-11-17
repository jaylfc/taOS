//! The transport: a cheap-to-clone [`Client`] over `reqwest` with a private
//! cookie jar, CSRF handling and uniform error mapping.

use std::fmt;
use std::sync::{Arc, Mutex, MutexGuard};
use std::time::Duration;

use reqwest::header::{HeaderValue, ACCEPT, CONTENT_TYPE, COOKIE, RETRY_AFTER, SET_COOKIE};
use reqwest::{Method, RequestBuilder, Response};
use serde::de::DeserializeOwned;
use serde::Serialize;

use crate::error::{Error, Result};
use crate::session::{Jar, Session, CSRF_COOKIE, SESSION_COOKIE};

/// Default controller address.
pub const DEFAULT_BASE_URL: &str = "http://127.0.0.1:6969";

/// The constant `User-Agent` sent by every request, `taOS-native/<crate version>`.
///
/// The controller stores a SHA-256 of the `User-Agent` a session was created
/// with and rejects the session when a later request carries a different one
/// (`tinyagentos/auth.py`, `create_session` and `validate_session`; the same
/// check runs in the auth middleware and in `/auth/me`). The string must
/// therefore never vary between requests of one session. A restored
/// [`Session`] carries the string it was created with and the client keeps
/// using it.
pub const USER_AGENT: &str = concat!("taOS-native/", env!("CARGO_PKG_VERSION"));

/// Header carrying the CSRF token on state-changing requests.
const CSRF_HEADER: &str = "X-CSRF-Token";
/// Header the lock-screen routes require (`LOCK_CONSOLE_HEADER` in
/// `tinyagentos/routes/auth.py`).
const CONSOLE_HEADER: &str = "X-taOS-Console";

/// Builder for a [`Client`].
#[derive(Debug, Clone)]
pub struct ClientBuilder {
    base_url: String,
    session: Option<Session>,
    request_timeout: Duration,
    connect_timeout: Duration,
}

impl Default for ClientBuilder {
    fn default() -> Self {
        Self {
            base_url: DEFAULT_BASE_URL.to_string(),
            session: None,
            request_timeout: Duration::from_secs(30),
            connect_timeout: Duration::from_secs(10),
        }
    }
}

impl ClientBuilder {
    /// Controller base URL, for example `http://127.0.0.1:6969`. A path prefix
    /// is kept, so a controller mounted under a sub-path works.
    pub fn base_url(mut self, url: impl Into<String>) -> Self {
        self.base_url = url.into();
        self
    }

    /// Restore a saved session. Also adopts the session's base URL and the
    /// exact `User-Agent` string it was created with.
    pub fn session(mut self, session: Session) -> Self {
        self.base_url = session.base_url().to_string();
        self.session = Some(session);
        self
    }

    /// Timeout for ordinary JSON requests (default 30 s). Streams and file
    /// transfers are not subject to it.
    pub fn request_timeout(mut self, d: Duration) -> Self {
        self.request_timeout = d;
        self
    }

    /// TCP connect timeout (default 10 s).
    pub fn connect_timeout(mut self, d: Duration) -> Self {
        self.connect_timeout = d;
        self
    }

    /// Build the client.
    pub fn build(self) -> Result<Client> {
        let base = self.base_url.trim_end_matches('/').to_string();
        let parsed = url::Url::parse(&base)
            .map_err(|e| Error::InvalidInput(format!("base url `{base}`: {e}")))?;
        if !matches!(parsed.scheme(), "http" | "https") {
            return Err(Error::InvalidInput(format!(
                "base url `{base}` must be http or https"
            )));
        }
        let user_agent = self
            .session
            .as_ref()
            .map(|s| s.user_agent().to_string())
            .unwrap_or_else(|| USER_AGENT.to_string());
        let http = reqwest::Client::builder()
            .user_agent(user_agent.clone())
            .connect_timeout(self.connect_timeout)
            // The API never redirects a JSON caller; a 303 to the login page
            // must surface as an error rather than silently fetch HTML.
            .redirect(reqwest::redirect::Policy::none())
            .build()
            .map_err(Error::Transport)?;
        let mut jar = Jar::default();
        if let Some(s) = &self.session {
            jar.set(SESSION_COOKIE, s.token());
            if let Some(c) = s.csrf() {
                jar.set(CSRF_COOKIE, c);
            }
        }
        Ok(Client {
            inner: Arc::new(Inner {
                http,
                base,
                user_agent,
                request_timeout: self.request_timeout,
                jar: Mutex::new(jar),
            }),
        })
    }
}

struct Inner {
    http: reqwest::Client,
    base: String,
    user_agent: String,
    request_timeout: Duration,
    jar: Mutex<Jar>,
}

/// A handle to one taOS controller. Cloning is cheap and clones share the
/// same cookie jar, so a sign-in on one clone is visible on all of them.
#[derive(Clone)]
pub struct Client {
    inner: Arc<Inner>,
}

impl fmt::Debug for Client {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        f.debug_struct("Client")
            .field("base_url", &self.inner.base)
            .field("user_agent", &self.inner.user_agent)
            .field("signed_in", &self.is_signed_in())
            .finish()
    }
}

impl Client {
    /// A client for `http://127.0.0.1:6969`.
    pub fn new() -> Result<Self> {
        ClientBuilder::default().build()
    }

    /// A client for the given base URL.
    pub fn with_base_url(url: impl Into<String>) -> Result<Self> {
        ClientBuilder::default().base_url(url).build()
    }

    /// A client restored from a saved [`Session`]. The session may have
    /// expired; the first call then returns [`Error::Unauthenticated`].
    pub fn with_session(session: Session) -> Result<Self> {
        ClientBuilder::default().session(session).build()
    }

    /// A builder with all options.
    pub fn builder() -> ClientBuilder {
        ClientBuilder::default()
    }

    /// The base URL requests go to (no trailing slash).
    pub fn base_url(&self) -> &str {
        &self.inner.base
    }

    /// The `User-Agent` this client sends.
    pub fn user_agent(&self) -> &str {
        &self.inner.user_agent
    }

    /// True while a session cookie is held (it may still have expired).
    pub fn is_signed_in(&self) -> bool {
        self.jar().get(SESSION_COOKIE).is_some()
    }

    /// Snapshot the current sign-in for storage in the OS keyring. `None` when
    /// not signed in. The crate writes nothing to disk itself.
    pub fn export_session(&self) -> Option<Session> {
        let jar = self.jar();
        let token = jar.get(SESSION_COOKIE)?.to_string();
        let csrf = jar.get(CSRF_COOKIE).map(str::to_string);
        Some(Session::new(
            self.inner.base.clone(),
            self.inner.user_agent.clone(),
            token,
            csrf,
        ))
    }

    /// Forget the local session without telling the server.
    pub fn forget_session(&self) {
        self.jar().clear();
    }

    fn jar(&self) -> MutexGuard<'_, Jar> {
        // A poisoned lock only means another thread panicked mid-update of a
        // string map; the data is still usable.
        self.inner.jar.lock().unwrap_or_else(|p| p.into_inner())
    }

    fn absorb_cookies(&self, resp: &Response) {
        let mut jar = self.jar();
        for v in resp.headers().get_all(SET_COOKIE) {
            if let Ok(s) = v.to_str() {
                jar.absorb(s);
            }
        }
    }

    pub(crate) fn url(&self, path: &str) -> String {
        format!("{}{}", self.inner.base, path)
    }

    /// Send a request, mapping every non-success status to an [`Error`].
    ///
    /// `attach` adds the body; it is called again if the request must be
    /// replayed after a CSRF rejection, so it must be able to build a fresh
    /// body each time.
    pub(crate) async fn send_with(
        &self,
        req: &Req,
        attach: &(dyn Fn(RequestBuilder) -> RequestBuilder + Send + Sync),
    ) -> Result<Response> {
        let mutating = !matches!(req.method, Method::GET | Method::HEAD | Method::OPTIONS);
        let mut replayed = false;
        loop {
            let mut rb = self
                .inner
                .http
                .request(req.method.clone(), self.url(&req.path))
                .header(ACCEPT, req.accept);
            let (cookie, csrf) = {
                let jar = self.jar();
                (jar.header(), jar.get(CSRF_COOKIE).map(str::to_string))
            };
            if let Some(c) = cookie {
                if let Ok(mut hv) = HeaderValue::from_str(&c) {
                    hv.set_sensitive(true);
                    rb = rb.header(COOKIE, hv);
                }
            }
            if mutating {
                if let Some(t) = csrf {
                    if let Ok(mut hv) = HeaderValue::from_str(&t) {
                        hv.set_sensitive(true);
                        rb = rb.header(CSRF_HEADER, hv);
                    }
                }
            }
            if req.console {
                rb = rb.header(CONSOLE_HEADER, "1");
            }
            if !req.query.is_empty() {
                rb = rb.query(&req.query);
            }
            if req.stream {
                // No overall timeout: the body may legitimately take long.
            } else {
                rb = rb.timeout(self.inner.request_timeout);
            }
            rb = attach(rb);
            let resp = rb.send().await.map_err(Error::Transport)?;
            self.absorb_cookies(&resp);
            let status = resp.status();
            if status.is_success() || (req.redirect_ok && status.is_redirection()) {
                return Ok(resp);
            }
            let retry_after = resp
                .headers()
                .get(RETRY_AFTER)
                .and_then(|v| v.to_str().ok())
                .map(str::to_string);
            let body = resp.bytes().await.map_err(Error::Transport)?;
            let err = Error::from_response(status.as_u16(), retry_after.as_deref(), &body);
            // The CSRF middleware re-issues a bound cookie on the very response
            // that rejected the token, and `absorb_cookies` has just stored it.
            // Replay once with the fresh token (the web client does not).
            if mutating && !replayed && matches!(err, Error::Forbidden { csrf: true, .. }) {
                replayed = true;
                continue;
            }
            return Err(err);
        }
    }

    /// Send a request whose body is the JSON in `req`.
    pub(crate) async fn send(&self, req: &Req) -> Result<Response> {
        let body = req.json.clone();
        self.send_with(req, &move |rb| match &body {
            Some(b) => rb
                .header(CONTENT_TYPE, "application/json")
                .body(b.to_string()),
            None => rb,
        })
        .await
    }

    /// Send and decode a JSON response into `T`.
    pub(crate) async fn json<T: DeserializeOwned>(&self, req: Req) -> Result<T> {
        let resp = self.send(&req).await?;
        let bytes = resp.bytes().await.map_err(Error::Transport)?;
        decode(&bytes)
    }
}

/// Decode `bytes` as `T`, reporting the failing JSON path.
pub(crate) fn decode<T: DeserializeOwned>(bytes: &[u8]) -> Result<T> {
    let mut de = serde_json::Deserializer::from_slice(bytes);
    serde_path_to_error::deserialize(&mut de).map_err(|e| {
        let path = e.path().to_string();
        Error::decode(path, e.inner().to_string(), bytes)
    })
}

/// A request description, built fluently and executed by [`Client`].
#[derive(Debug, Clone)]
pub(crate) struct Req {
    pub(crate) method: Method,
    /// Already percent-encoded path, starting with `/`.
    pub(crate) path: String,
    pub(crate) query: Vec<(String, String)>,
    pub(crate) json: Option<serde_json::Value>,
    pub(crate) console: bool,
    pub(crate) accept: &'static str,
    pub(crate) redirect_ok: bool,
    pub(crate) stream: bool,
}

impl Req {
    fn new(method: Method, path: impl Into<String>) -> Self {
        Self {
            method,
            path: path.into(),
            query: Vec::new(),
            json: None,
            console: false,
            accept: "application/json",
            redirect_ok: false,
            stream: false,
        }
    }
    pub(crate) fn get(path: impl Into<String>) -> Self {
        Self::new(Method::GET, path)
    }
    pub(crate) fn post(path: impl Into<String>) -> Self {
        Self::new(Method::POST, path)
    }
    pub(crate) fn delete(path: impl Into<String>) -> Self {
        Self::new(Method::DELETE, path)
    }
    pub(crate) fn query(mut self, k: &str, v: impl ToString) -> Self {
        self.query.push((k.to_string(), v.to_string()));
        self
    }
    pub(crate) fn opt_query(self, k: &str, v: Option<impl ToString>) -> Self {
        match v {
            Some(v) => self.query(k, v),
            None => self,
        }
    }
    pub(crate) fn json_body<B: Serialize>(mut self, body: &B) -> Result<Self> {
        self.json = Some(
            serde_json::to_value(body)
                .map_err(|e| Error::InvalidInput(format!("request body: {e}")))?,
        );
        Ok(self)
    }
    pub(crate) fn console(mut self) -> Self {
        self.console = true;
        self
    }
    pub(crate) fn redirect_ok(mut self) -> Self {
        self.redirect_ok = true;
        self
    }
    pub(crate) fn streaming(mut self, accept: &'static str) -> Self {
        self.stream = true;
        self.accept = accept;
        self
    }
}
