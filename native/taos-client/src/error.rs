//! The single error type of this crate.
//!
//! The controller reports errors in two shapes: FastAPI's `{"detail": ...}`
//! and a hand-written `{"error": ...}` (sometimes both inside one router). On
//! the LLM gateway an OpenAI-shaped `{"error": {"message": ...}}` is used. All
//! three are parsed into the `message` of the matching variant.

use std::time::Duration;

use serde_json::Value;

/// Result alias used across the crate.
pub type Result<T, E = Error> = std::result::Result<T, E>;

/// One failing field of a 422 response.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct FieldError {
    /// Dotted location, for example `body.granted_scopes.0`.
    pub location: String,
    /// Human readable message from the server.
    pub message: String,
}

/// Everything that can go wrong when talking to the controller.
#[derive(Debug, thiserror::Error)]
pub enum Error {
    /// HTTP 401. A UI should drop to the lock screen. `message` may be
    /// `onboarding_required` on a controller that has no user yet.
    #[error("not signed in: {message}")]
    Unauthenticated {
        /// Message from the server.
        message: String,
    },
    /// HTTP 403.
    #[error("forbidden: {message}")]
    Forbidden {
        /// Message from the server.
        message: String,
        /// True when the server rejected the CSRF double-submit check. The
        /// client already retries once after re-reading the re-issued cookie,
        /// so seeing this means the retry failed too.
        csrf: bool,
    },
    /// HTTP 404, or a 404-shaped refusal such as an unknown notification.
    #[error("not found: {message}")]
    NotFound {
        /// Message from the server.
        message: String,
    },
    /// HTTP 409 (already answered, target exists, restore conflict, ...).
    #[error("conflict: {message}")]
    Conflict {
        /// Message from the server.
        message: String,
    },
    /// HTTP 429.
    #[error("rate limited: {message}")]
    RateLimited {
        /// Message from the server.
        message: String,
        /// Value of the `Retry-After` header or the body's `retry_after`.
        retry_after: Option<Duration>,
    },
    /// HTTP 422 from FastAPI request validation.
    #[error("validation failed: {message}")]
    Validation {
        /// Summary message.
        message: String,
        /// Per-field details.
        fields: Vec<FieldError>,
    },
    /// HTTP 400.
    #[error("bad request: {message}")]
    BadRequest {
        /// Message from the server.
        message: String,
    },
    /// Any other 4xx status (for example 410 Gone on an expired sign-in card).
    #[error("request refused ({status}): {message}")]
    Http {
        /// HTTP status code.
        status: u16,
        /// Message from the server.
        message: String,
    },
    /// HTTP 5xx.
    #[error("server error ({status}): {message}")]
    Server {
        /// HTTP status code.
        status: u16,
        /// Message from the server.
        message: String,
    },
    /// The request never produced a response (DNS, connect, TLS, reset, timeout).
    #[error("transport error: {0}")]
    Transport(#[source] reqwest::Error),
    /// The response arrived but could not be decoded into the expected type.
    #[error("could not decode response at `{path}`: {message} (body: {body})")]
    Decode {
        /// JSON path of the failing value, `.` for the root.
        path: String,
        /// Decoder message.
        message: String,
        /// The first part of the body, truncated.
        body: String,
    },
    /// A live stream was closed by the server and is being re-established.
    #[error("stream closed by the server; reconnecting")]
    Disconnected,
    /// The server reported an error inside a stream (an `event: error` frame).
    #[error("stream error: {0}")]
    Stream(String),
    /// The operation does not exist for this location kind. Nothing was sent.
    #[error("unsupported: {0}")]
    Unsupported(&'static str),
    /// A caller supplied value was rejected before any request was made.
    #[error("invalid input: {0}")]
    InvalidInput(String),
    /// The server answered in a way that breaks the protocol (for example a
    /// successful login that set no session cookie).
    #[error("protocol error: {0}")]
    Protocol(String),
    /// A local I/O failure while reading a file to upload.
    #[error("i/o error: {0}")]
    Io(#[from] std::io::Error),
}

impl Error {
    /// True for HTTP 401, so a UI can drop to the lock screen.
    pub fn is_unauthenticated(&self) -> bool {
        matches!(self, Self::Unauthenticated { .. })
    }

    /// True when the failure is transient and the same call may succeed later.
    pub fn is_retryable(&self) -> bool {
        matches!(
            self,
            Self::Transport(_)
                | Self::Server { .. }
                | Self::RateLimited { .. }
                | Self::Disconnected
        )
    }

    /// The HTTP status behind this error, when there was one.
    pub fn status(&self) -> Option<u16> {
        Some(match self {
            Self::Unauthenticated { .. } => 401,
            Self::Forbidden { .. } => 403,
            Self::NotFound { .. } => 404,
            Self::Conflict { .. } => 409,
            Self::RateLimited { .. } => 429,
            Self::Validation { .. } => 422,
            Self::BadRequest { .. } => 400,
            Self::Http { status, .. } | Self::Server { status, .. } => *status,
            _ => return None,
        })
    }

    /// Build the error for a non-success HTTP response.
    pub(crate) fn from_response(
        status: u16,
        retry_after_header: Option<&str>,
        body: &[u8],
    ) -> Self {
        let parsed: Option<Value> = serde_json::from_slice(body).ok();
        let (message, fields) = extract_message(status, parsed.as_ref(), body);
        match status {
            401 => Self::Unauthenticated { message },
            403 => {
                let csrf = message.starts_with("CSRF token");
                Self::Forbidden { message, csrf }
            }
            404 => Self::NotFound { message },
            409 => Self::Conflict { message },
            429 => {
                let from_body = parsed
                    .as_ref()
                    .and_then(|v| v.get("retry_after"))
                    .and_then(Value::as_f64);
                let from_header = retry_after_header.and_then(|h| h.trim().parse::<f64>().ok());
                let retry_after = from_header
                    .or(from_body)
                    .filter(|s| s.is_finite() && *s >= 0.0)
                    .map(Duration::from_secs_f64);
                Self::RateLimited {
                    message,
                    retry_after,
                }
            }
            422 => Self::Validation { message, fields },
            400 => Self::BadRequest { message },
            500..=599 => Self::Server { status, message },
            _ => Self::Http { status, message },
        }
    }

    /// Drop the quoted body from a decode error (for routes whose bodies can
    /// hold credentials, such as `/api/providers`, which carries `api_key`
    /// for admins).
    pub(crate) fn without_body(self) -> Self {
        match self {
            Self::Decode { path, message, .. } => Self::Decode {
                path,
                message,
                body: "<body withheld>".into(),
            },
            other => other,
        }
    }

    /// Build a decode error, truncating the body.
    pub(crate) fn decode(path: String, message: String, body: &[u8]) -> Self {
        Self::Decode {
            path,
            message,
            body: truncate(body, 300),
        }
    }
}

/// Lossy, length-limited rendering of a body for error messages.
pub(crate) fn truncate(body: &[u8], max: usize) -> String {
    let text = String::from_utf8_lossy(body);
    if text.chars().count() <= max {
        text.into_owned()
    } else {
        let cut: String = text.chars().take(max).collect();
        format!("{cut}...")
    }
}

fn extract_message(status: u16, v: Option<&Value>, raw: &[u8]) -> (String, Vec<FieldError>) {
    let mut fields = Vec::new();
    if let Some(v) = v {
        // `detail` may be a string or FastAPI's list of validation problems.
        if let Some(detail) = v.get("detail") {
            match detail {
                Value::String(s) => return (s.clone(), fields),
                Value::Array(items) => {
                    for item in items {
                        let location = item
                            .get("loc")
                            .and_then(Value::as_array)
                            .map(|l| {
                                l.iter()
                                    .map(|p| match p {
                                        Value::String(s) => s.clone(),
                                        other => other.to_string(),
                                    })
                                    .collect::<Vec<_>>()
                                    .join(".")
                            })
                            .unwrap_or_default();
                        let message = item
                            .get("msg")
                            .and_then(Value::as_str)
                            .unwrap_or("invalid")
                            .to_string();
                        fields.push(FieldError { location, message });
                    }
                    let summary = fields
                        .iter()
                        .map(|f| format!("{}: {}", f.location, f.message))
                        .collect::<Vec<_>>()
                        .join("; ");
                    return (summary, fields);
                }
                other => return (other.to_string(), fields),
            }
        }
        // `error` may be a string or an OpenAI-style object.
        if let Some(err) = v.get("error") {
            match err {
                Value::String(s) => return (s.clone(), fields),
                Value::Object(o) => {
                    if let Some(m) = o.get("message").and_then(Value::as_str) {
                        return (m.to_string(), fields);
                    }
                }
                _ => {}
            }
        }
        if let Some(m) = v.get("message").and_then(Value::as_str) {
            return (m.to_string(), fields);
        }
    }
    // Not JSON (or an unknown shape): never echo HTML, fall back to the status.
    let text = String::from_utf8_lossy(raw);
    let looks_html = text.trim_start().starts_with('<');
    if raw.is_empty() || looks_html {
        (format!("HTTP {status}"), fields)
    } else {
        (truncate(raw, 200), fields)
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn detail_and_error_envelopes() {
        let e = Error::from_response(409, None, br#"{"error":"already answered or not pending"}"#);
        assert!(
            matches!(e, Error::Conflict { ref message } if message == "already answered or not pending")
        );
        let e = Error::from_response(404, None, br#"{"detail":"notification not found"}"#);
        assert!(
            matches!(e, Error::NotFound { ref message } if message == "notification not found")
        );
        let e = Error::from_response(400, None, br#"{"error":{"message":"bad"}}"#);
        assert!(matches!(e, Error::BadRequest { ref message } if message == "bad"));
    }

    #[test]
    fn html_body_falls_back_to_status() {
        let e = Error::from_response(502, None, b"<html>bad gateway</html>");
        assert!(matches!(e, Error::Server { status: 502, ref message } if message == "HTTP 502"));
    }

    #[test]
    fn csrf_is_flagged() {
        let e = Error::from_response(403, None, br#"{"detail":"CSRF token mismatch"}"#);
        assert!(matches!(e, Error::Forbidden { csrf: true, .. }));
        let e = Error::from_response(403, None, br#"{"detail":"forbidden"}"#);
        assert!(matches!(e, Error::Forbidden { csrf: false, .. }));
    }
}
