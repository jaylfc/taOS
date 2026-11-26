//! WebSocket chat (`/ws/chat`, `/ws/chat/{agent}`).
//!
//! Out of scope for this version of the crate: the five first apps do not use
//! it. The session cookie this client holds is what those sockets
//! authenticate with (they read only the cookie, never a bearer token), so a
//! later change can add the socket here on top of
//! [`Client::export_session`](crate::Client::export_session).
