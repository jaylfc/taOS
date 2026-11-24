// SPDX-License-Identifier: AGPL-3.0-or-later
//! `taos-client`: the shared, GUI-toolkit-independent Rust client for the taOS
//! controller API.
//!
//! * [`Client`] is an async, cheap-to-clone handle over `reqwest` with rustls.
//!   It signs in with the same session cookie the web UI uses, keeps the CSRF
//!   token in step, and sends a constant `User-Agent` (`taOS-native/<version>`)
//!   because the controller binds sessions to the User-Agent hash.
//! * [`Error`] maps every HTTP failure; a 401 is always
//!   [`Error::Unauthenticated`] so a UI can drop to the lock screen.
//! * Typed domain modules: [`notifications`], [`decisions`], [`files`],
//!   [`models`], [`dashboard`] and [`agents`].
//! * [`events`] has a reconnecting SSE client and [`events::PollWhileVisible`].
//!
//! Streams and pollers spawn Tokio tasks, so create them inside a Tokio
//! runtime. Nothing in this crate writes files, logs, or prints secrets.
//!
//! ```no_run
//! # async fn demo() -> taos_client::Result<()> {
//! let client = taos_client::Client::with_base_url("http://127.0.0.1:6969")?;
//! client.login(Some("alex"), "password", Some(true)).await?;
//! let unread = client.notifications().list(true).await?;
//! println!("{} unread", unread.len());
//! # Ok(()) }
//! ```

#![warn(missing_docs)]

mod client;
mod error;
mod session;
mod util;

pub mod agents;
pub mod auth;
pub mod chat;
pub mod dashboard;
pub mod decisions;
pub mod events;
pub mod files;
pub mod models;
pub mod notifications;

pub use client::{Client, ClientBuilder, DEFAULT_BASE_URL, USER_AGENT};
pub use error::{Error, FieldError, Result};
pub use session::Session;
pub use util::Timestamp;
