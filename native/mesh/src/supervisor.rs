// SPDX-License-Identifier: AGPL-3.0-or-later
//! Keeps a radio connected: connect, run the session, and when the link
//! drops (unplugged, out of BLE range, TCP reset) reconnect with
//! exponential back-off. The state (`reconnecting`, attempt, next retry,
//! last error) is visible in the API through the radio's status.

use crate::engine::{Engine, EngineError, Session};
use crate::link::{Connector, Link};
use crate::model::now_ms;
use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::Arc;
use std::time::Duration;

/// 1 s, 2 s, 4 s ... capped (default 60 s).
#[derive(Debug, Clone)]
pub struct Backoff {
    pub base: Duration,
    pub max: Duration,
    attempt: u32,
}

impl Backoff {
    pub fn new(base: Duration, max: Duration) -> Self {
        Backoff { base, max, attempt: 0 }
    }
    pub fn standard() -> Self {
        Self::new(Duration::from_secs(1), Duration::from_secs(60))
    }
    pub fn attempt(&self) -> u32 {
        self.attempt
    }
    /// The delay before the next try; counts the attempt.
    pub fn next_delay(&mut self) -> Duration {
        let d = self.base.saturating_mul(1u32 << self.attempt.min(16));
        self.attempt += 1;
        d.min(self.max)
    }
    pub fn reset(&mut self) {
        self.attempt = 0;
    }
}

pub struct Supervised {
    pub engine: Arc<Engine>,
    pub connector: Arc<dyn Connector>,
    pub network: String,
    pub transmit: bool,
    pub stop: Arc<AtomicBool>,
    pub backoff: Backoff,
    /// A link already open (the discovery probe's) and the bytes it already
    /// received; used for the first session only.
    pub first: Option<(Box<dyn Link>, Vec<u8>)>,
}

/// Run until `stop` is set. `sleep` waits (and returns early when stopped);
/// tests pass a recorder that does not really wait.
pub fn run(mut s: Supervised, sleep: &dyn Fn(Duration)) {
    let label = s.connector.describe();
    s.engine
        .register_radio(&label, &s.network, &label, super::engine::kind_name(s.connector.kind()));
    let mut radio_id = label.clone();
    while !s.stop.load(Ordering::Relaxed) {
        let attempt = match s.first.take() {
            Some((link, pre)) => Ok((link, pre)),
            None => s.connector.connect().map(|l| (l, vec![])),
        };
        let why = match attempt {
            Err(e) => format!("connect failed: {e}"),
            Ok((link, pre)) => {
                let known = (radio_id != label).then_some(radio_id.as_str());
                match Session::new_as(s.engine.clone(), link, &s.network, known) {
                    Err(e) => e.to_string(),
                    Ok(mut sess) => {
                        sess.transmit_enabled = s.transmit;
                        sess.prefeed(pre);
                        let mut stored_any = false;
                        let mut last: Option<EngineError> = None;
                        while !s.stop.load(Ordering::Relaxed) {
                            match sess.pump() {
                                Ok(n) => {
                                    if n > 0 && !stored_any {
                                        stored_any = true;
                                        s.backoff.reset();
                                    }
                                }
                                Err(EngineError::NoRadio(m)) => {
                                    last = Some(EngineError::NoRadio(m));
                                    break;
                                }
                                // a store failure is not a link failure: it is
                                // in `health`; keep the link and retry
                                Err(_) => std::thread::sleep(Duration::from_millis(500)),
                            }
                        }
                        radio_id = sess.radio_id().to_string();
                        match last {
                            Some(e) => e.to_string(),
                            None => break,
                        }
                    }
                }
            }
        };
        let delay = s.backoff.next_delay();
        s.engine.radio_reconnecting(
            &radio_id,
            s.backoff.attempt(),
            now_ms() + delay.as_millis() as u64,
            &why,
        );
        sleep(delay);
    }
    s.engine.radio_offline(&radio_id, "stopped");
}

/// A real sleep that wakes early when stopped.
pub fn sleep_unless_stopped(stop: &Arc<AtomicBool>, d: Duration) {
    let end = std::time::Instant::now() + d;
    while std::time::Instant::now() < end && !stop.load(Ordering::Relaxed) {
        std::thread::sleep(Duration::from_millis(100).min(d));
    }
}
