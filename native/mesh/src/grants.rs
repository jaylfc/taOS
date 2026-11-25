// SPDX-License-Identifier: AGPL-3.0-or-later
//! Owner consent for agents: per-channel send grants with an airtime budget,
//! and read grants. Agents have no radio access by default.
//!
//! A grant is an event in an append-only log; revoking appends a revoke
//! event. The owner (the person running the engine) is never limited by
//! grants.

use crate::model::now_ms;
use crate::store::{LineLog, StoreError};
use serde::{Deserialize, Serialize};
use std::collections::BTreeMap;
use std::path::Path;

pub const OWNER: &str = "owner";

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct Grant {
    pub id: String,
    /// "send" or "read"
    pub kind: String,
    pub agent: String,
    pub network: String,
    /// Channel index, or None for every channel of that network.
    pub channel: Option<u32>,
    /// Send grants: budget per rolling hour. None means no limit on that axis.
    pub max_msgs_per_hour: Option<u32>,
    pub max_bytes_per_hour: Option<u32>,
    /// Absolute expiry, ms since 1970.
    pub expires_ms: Option<u64>,
    pub created_ms: u64,
    pub created_by: String,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(tag = "ev", rename_all = "snake_case")]
enum Ev {
    Grant(Grant),
    Revoke { id: String, ts: u64, by: String },
}

#[derive(Debug, PartialEq)]
pub enum Denied {
    NoGrant,
    Expired,
    MessageBudget { limit: u32 },
    ByteBudget { limit: u32 },
}

impl std::fmt::Display for Denied {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        match self {
            Denied::NoGrant => write!(f, "no grant: the owner has not allowed this agent to send on this channel"),
            Denied::Expired => write!(f, "the send grant has expired"),
            Denied::MessageBudget { limit } => write!(f, "airtime budget used up: {limit} messages per hour"),
            Denied::ByteBudget { limit } => write!(f, "airtime budget used up: {limit} bytes per hour"),
        }
    }
}

pub struct Grants {
    log: LineLog,
    pub active: BTreeMap<String, Grant>,
    /// (agent, ts_ms, bytes) of sends, for the rolling hour. Rebuilt from the
    /// record store at start-up.
    sent: Vec<(String, String, Option<u32>, u64, u32)>,
}

const HOUR_MS: u64 = 3_600_000;

fn grant_id(g: &Grant) -> String {
    use sha2::{Digest, Sha256};
    let mut h = Sha256::new();
    static N: std::sync::atomic::AtomicU64 = std::sync::atomic::AtomicU64::new(0);
    let n = N.fetch_add(1, std::sync::atomic::Ordering::Relaxed);
    let nanos = std::time::SystemTime::now().duration_since(std::time::UNIX_EPOCH).map(|d| d.as_nanos()).unwrap_or(0);
    h.update(format!("{}|{}|{}|{:?}|{}|{}|{}", g.kind, g.agent, g.network, g.channel, g.created_ms, nanos, n).as_bytes());
    format!("g{}", crate::model::to_hex(&h.finalize()[..5]))
}

impl Grants {
    pub fn open(dir: &Path) -> Result<Self, StoreError> {
        let log = LineLog::open(&dir.join("grants.jsonl"))?;
        let mut active = BTreeMap::new();
        for (i, l) in LineLog::read_lines(log.path())?.iter().enumerate() {
            match serde_json::from_str::<Ev>(l).map_err(|e| StoreError(format!("grants.jsonl line {}: {e}", i + 1)))? {
                Ev::Grant(g) => {
                    active.insert(g.id.clone(), g);
                }
                Ev::Revoke { id, .. } => {
                    active.remove(&id);
                }
            }
        }
        Ok(Grants { log, active, sent: vec![] })
    }

    pub fn add(&mut self, mut g: Grant) -> Result<Grant, StoreError> {
        if g.kind != "send" && g.kind != "read" {
            return Err(StoreError("grant kind must be send or read".into()));
        }
        if g.agent.is_empty() || g.agent == OWNER {
            return Err(StoreError("a grant names an agent".into()));
        }
        g.created_ms = now_ms();
        g.id = grant_id(&g);
        let line = serde_json::to_string(&Ev::Grant(g.clone())).map_err(|e| StoreError(e.to_string()))?;
        self.log.append_line(&line)?;
        self.active.insert(g.id.clone(), g.clone());
        Ok(g)
    }

    pub fn revoke(&mut self, id: &str, by: &str) -> Result<(), StoreError> {
        if !self.active.contains_key(id) {
            return Err(StoreError(format!("no active grant {id}")));
        }
        let line = serde_json::to_string(&Ev::Revoke { id: id.into(), ts: now_ms(), by: by.into() }).map_err(|e| StoreError(e.to_string()))?;
        self.log.append_line(&line)?;
        self.active.remove(id);
        Ok(())
    }

    /// Seed the rolling window from past outbound records.
    pub fn note_sent(&mut self, principal: &str, network: &str, channel: Option<u32>, ts_ms: u64, bytes: u32) {
        self.sent.push((principal.into(), network.into(), channel, ts_ms, bytes));
    }

    fn matches(g: &Grant, kind: &str, agent: &str, network: &str, channel: Option<u32>) -> bool {
        g.kind == kind && g.agent == agent && g.network == network && (g.channel.is_none() || g.channel == channel)
    }

    /// Check a send. On success the send is counted against the budget.
    pub fn authorize_send(&mut self, principal: &str, network: &str, channel: Option<u32>, bytes: u32, now: u64) -> Result<(), Denied> {
        if principal == OWNER {
            return Ok(());
        }
        let cands: Vec<&Grant> = self.active.values().filter(|g| Self::matches(g, "send", principal, network, channel)).collect();
        if cands.is_empty() {
            return Err(Denied::NoGrant);
        }
        let live: Vec<&&Grant> = cands.iter().filter(|g| g.expires_ms.is_none_or(|e| e > now)).collect();
        if live.is_empty() {
            return Err(Denied::Expired);
        }
        let mut last = Denied::NoGrant;
        for g in live {
            // count this agent's sends inside the grant's scope in the last hour
            let (mut n, mut b) = (0u32, 0u32);
            for (p, net, ch, ts, by) in &self.sent {
                if p == principal && net == network && now.saturating_sub(*ts) < HOUR_MS && (g.channel.is_none() || *ch == g.channel) {
                    n += 1;
                    b += by;
                }
            }
            if let Some(l) = g.max_msgs_per_hour {
                if n + 1 > l {
                    last = Denied::MessageBudget { limit: l };
                    continue;
                }
            }
            if let Some(l) = g.max_bytes_per_hour {
                if b + bytes > l {
                    last = Denied::ByteBudget { limit: l };
                    continue;
                }
            }
            self.sent.push((principal.into(), network.into(), channel, now, bytes));
            return Ok(());
        }
        Err(last)
    }

    /// May this agent read the owner's mesh records on this channel?
    pub fn may_read(&self, principal: &str, network: &str, channel: Option<u32>, now: u64) -> bool {
        principal == OWNER || self.active.values().any(|g| Self::matches(g, "read", principal, network, channel) && g.expires_ms.is_none_or(|e| e > now))
    }

    pub fn list(&self) -> Vec<Grant> {
        self.active.values().cloned().collect()
    }
}
