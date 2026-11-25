// SPDX-License-Identifier: AGPL-3.0-or-later
//! Radio identity registry.
//!
//! A radio (node id) is assigned to an agent (`agent:<id>`), a taOS person
//! (`person:<account id>`) or a named non-taOS contact (`contact:<id>`).
//! Every change is one event in an append-only log; the registry state is
//! the replay of that log. Nothing is deleted.
//!
//! Link semantics (owner ruling, spec addendum 5):
//! * a contact can later be linked to a taOS account;
//! * both parties keep both sides of their shared conversations (direct
//!   messages between the observer's radios and the contact's radios);
//! * the linked account receives the contact's radio history: those direct
//!   conversations and the positions the contact's radio broadcast;
//! * overheard public-channel traffic stays with the observer;
//! * a contact carries "told about stored positions" (flag + date the owner
//!   confirms);
//! * once linked, the account may hide its own location history (a
//!   tombstone; the raw records stay on disk, pending the owner's ruling on
//!   deletion versus "kept indefinitely").

use crate::model::{now_ms, Attribution, IdentityRef, Record};
use crate::store::{LineLog, StoreError};
use serde::{Deserialize, Serialize};
use std::collections::{BTreeMap, BTreeSet};
use std::path::Path;

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
#[serde(tag = "ev", rename_all = "snake_case")]
pub enum Event {
    ContactCreate {
        id: String,
        name: String,
        notes: Option<String>,
        ts: u64,
        by: String,
    },
    ContactUpdate {
        id: String,
        name: Option<String>,
        notes: Option<String>,
        ts: u64,
        by: String,
    },
    /// Assign (or reassign) a radio. `identity` is None to unassign.
    Assign {
        node: String,
        identity: Option<IdentityRef>,
        name: Option<String>,
        ts: u64,
        by: String,
    },
    Link {
        contact: String,
        account: IdentityRef,
        ts: u64,
        by: String,
    },
    Told {
        contact: String,
        told: bool,
        date: Option<String>,
        ts: u64,
        by: String,
    },
    /// The account hides its own location history up to `ts`.
    LocationsHidden {
        contact: String,
        ts: u64,
        by: String,
    },
}

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq)]
pub struct Contact {
    pub id: String,
    pub name: String,
    pub notes: Option<String>,
    pub created_ts: u64,
    pub created_by: String,
    pub linked_account: Option<IdentityRef>,
    pub linked_ts: Option<u64>,
    pub positions_told: bool,
    pub positions_told_date: Option<String>,
    pub locations_hidden_ts: Option<u64>,
}

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq)]
pub struct Assignment {
    pub node: String,
    pub identity: IdentityRef,
    pub name: Option<String>,
}

#[derive(Default)]
pub struct Registry {
    pub contacts: BTreeMap<String, Contact>,
    pub assignments: BTreeMap<String, Assignment>,
    /// Display names for agents and persons, set when assigning.
    pub names: BTreeMap<IdentityRef, String>,
}

pub fn valid_identity(r: &str) -> bool {
    let Some((kind, id)) = r.split_once(':') else {
        return false;
    };
    matches!(kind, "agent" | "person" | "contact")
        && !id.is_empty()
        && !id.contains(char::is_whitespace)
}

impl Registry {
    fn apply(&mut self, e: &Event) {
        match e {
            Event::ContactCreate {
                id,
                name,
                notes,
                ts,
                by,
            } => {
                self.contacts.insert(
                    id.clone(),
                    Contact {
                        id: id.clone(),
                        name: name.clone(),
                        notes: notes.clone(),
                        created_ts: *ts,
                        created_by: by.clone(),
                        linked_account: None,
                        linked_ts: None,
                        positions_told: false,
                        positions_told_date: None,
                        locations_hidden_ts: None,
                    },
                );
            }
            Event::ContactUpdate {
                id, name, notes, ..
            } => {
                if let Some(c) = self.contacts.get_mut(id) {
                    if let Some(n) = name {
                        c.name = n.clone();
                    }
                    if notes.is_some() {
                        c.notes = notes.clone();
                    }
                }
            }
            Event::Assign {
                node,
                identity,
                name,
                ..
            } => match identity {
                Some(i) => {
                    if let Some(n) = name {
                        self.names.insert(i.clone(), n.clone());
                    }
                    self.assignments.insert(
                        node.clone(),
                        Assignment {
                            node: node.clone(),
                            identity: i.clone(),
                            name: name.clone(),
                        },
                    );
                }
                None => {
                    self.assignments.remove(node);
                }
            },
            Event::Link {
                contact,
                account,
                ts,
                ..
            } => {
                if let Some(c) = self.contacts.get_mut(contact) {
                    c.linked_account = Some(account.clone());
                    c.linked_ts = Some(*ts);
                }
            }
            Event::Told {
                contact,
                told,
                date,
                ..
            } => {
                if let Some(c) = self.contacts.get_mut(contact) {
                    c.positions_told = *told;
                    c.positions_told_date = date.clone();
                }
            }
            Event::LocationsHidden { contact, ts, .. } => {
                if let Some(c) = self.contacts.get_mut(contact) {
                    c.locations_hidden_ts = Some(*ts);
                }
            }
        }
    }

    pub fn name_of(&self, r: &str) -> String {
        if let Some(id) = r.strip_prefix("contact:") {
            if let Some(c) = self.contacts.get(id) {
                return c.name.clone();
            }
        }
        self.names
            .get(r)
            .cloned()
            .unwrap_or_else(|| r.split_once(':').map(|x| x.1).unwrap_or(r).to_string())
    }

    pub fn attribute(&self, node: &str) -> Option<Attribution> {
        let a = self.assignments.get(node)?;
        let linked = a
            .identity
            .strip_prefix("contact:")
            .and_then(|c| self.contacts.get(c))
            .and_then(|c| c.linked_account.clone());
        Some(Attribution {
            identity: a.identity.clone(),
            name: self.name_of(&a.identity),
            linked_account: linked,
        })
    }

    pub fn nodes_of(&self, identity: &str) -> BTreeSet<String> {
        self.assignments
            .values()
            .filter(|a| a.identity == identity)
            .map(|a| a.node.clone())
            .collect()
    }

    /// Contacts linked to `account`.
    pub fn contacts_linked_to(&self, account: &str) -> Vec<&Contact> {
        self.contacts
            .values()
            .filter(|c| c.linked_account.as_deref() == Some(account))
            .collect()
    }
}

pub struct IdentityStore {
    log: LineLog,
    pub reg: Registry,
}

fn new_contact_id(name: &str, ts: u64, n: usize) -> String {
    use sha2::{Digest, Sha256};
    let mut h = Sha256::new();
    h.update(name.as_bytes());
    h.update(ts.to_be_bytes());
    h.update((n as u64).to_be_bytes());
    format!("c{}", crate::model::to_hex(&h.finalize()[..5]))
}

impl IdentityStore {
    pub fn open(dir: &Path) -> Result<Self, StoreError> {
        let log = LineLog::open(&dir.join("identities.jsonl"))?;
        let mut reg = Registry::default();
        for (i, l) in LineLog::read_lines(log.path())?.iter().enumerate() {
            let e: Event = serde_json::from_str(l)
                .map_err(|e| StoreError(format!("identities.jsonl line {}: {e}", i + 1)))?;
            reg.apply(&e);
        }
        Ok(IdentityStore { log, reg })
    }

    fn commit(&mut self, e: Event) -> Result<(), StoreError> {
        let line = serde_json::to_string(&e).map_err(|e| StoreError(e.to_string()))?;
        // durable first, then visible
        self.log.append_line(&line)?;
        self.reg.apply(&e);
        Ok(())
    }

    pub fn create_contact(
        &mut self,
        name: &str,
        notes: Option<&str>,
        by: &str,
    ) -> Result<String, StoreError> {
        if name.trim().is_empty() {
            return Err(StoreError("contact name is empty".into()));
        }
        let ts = now_ms();
        let id = new_contact_id(name, ts, self.reg.contacts.len());
        self.commit(Event::ContactCreate {
            id: id.clone(),
            name: name.into(),
            notes: notes.map(String::from),
            ts,
            by: by.into(),
        })?;
        Ok(id)
    }

    pub fn update_contact(
        &mut self,
        id: &str,
        name: Option<&str>,
        notes: Option<&str>,
        by: &str,
    ) -> Result<(), StoreError> {
        if !self.reg.contacts.contains_key(id) {
            return Err(StoreError(format!("no contact {id}")));
        }
        self.commit(Event::ContactUpdate {
            id: id.into(),
            name: name.map(String::from),
            notes: notes.map(String::from),
            ts: now_ms(),
            by: by.into(),
        })
    }

    pub fn assign(
        &mut self,
        node: &str,
        identity: Option<&str>,
        name: Option<&str>,
        by: &str,
    ) -> Result<(), StoreError> {
        if let Some(i) = identity {
            if !valid_identity(i) {
                return Err(StoreError(format!(
                    "bad identity {i:?}: use agent:<id>, person:<id> or contact:<id>"
                )));
            }
            if let Some(c) = i.strip_prefix("contact:") {
                if !self.reg.contacts.contains_key(c) {
                    return Err(StoreError(format!("no contact {c}")));
                }
            }
        }
        self.commit(Event::Assign {
            node: node.into(),
            identity: identity.map(String::from),
            name: name.map(String::from),
            ts: now_ms(),
            by: by.into(),
        })
    }

    pub fn link(&mut self, contact: &str, account: &str, by: &str) -> Result<(), StoreError> {
        if !self.reg.contacts.contains_key(contact) {
            return Err(StoreError(format!("no contact {contact}")));
        }
        if !account.starts_with("person:") || !valid_identity(account) {
            return Err(StoreError(
                "a contact links to a taOS account: person:<account id>".into(),
            ));
        }
        self.commit(Event::Link {
            contact: contact.into(),
            account: account.into(),
            ts: now_ms(),
            by: by.into(),
        })
    }

    pub fn set_told(
        &mut self,
        contact: &str,
        told: bool,
        date: Option<&str>,
        by: &str,
    ) -> Result<(), StoreError> {
        if !self.reg.contacts.contains_key(contact) {
            return Err(StoreError(format!("no contact {contact}")));
        }
        self.commit(Event::Told {
            contact: contact.into(),
            told,
            date: date.map(String::from),
            ts: now_ms(),
            by: by.into(),
        })
    }

    pub fn hide_locations(&mut self, contact: &str, by: &str) -> Result<(), StoreError> {
        let c = self
            .reg
            .contacts
            .get(contact)
            .ok_or_else(|| StoreError(format!("no contact {contact}")))?;
        match &c.linked_account {
            Some(a) if a == by => {}
            _ => {
                return Err(StoreError(
                    "only the linked account can hide its own location history".into(),
                ))
            }
        }
        self.commit(Event::LocationsHidden {
            contact: contact.into(),
            ts: now_ms(),
            by: by.into(),
        })
    }
}

/// Visibility of a stored record to a viewer.
///
/// * `owner` (the person running the engine) and any identity equal to the
///   record's `observer` see everything that observer heard or sent.
/// * a linked account sees what its contact's radio history is: every frame
///   the contact's radios sent (text, positions, telemetry, node info), and
///   the direct messages addressed to them. It does not see traffic from
///   other people that the observer merely overheard on a channel.
/// * state records (send progress) are not messages; callers ask for them
///   explicitly.
pub fn visible_to(reg: &Registry, rec: &Record, viewer: &str, owner: &str) -> bool {
    if rec.dir == "state" {
        return false;
    }
    if viewer == owner || rec.observer.as_deref() == Some(viewer) {
        return true;
    }
    if !matches!(
        rec.kind.as_str(),
        "text" | "position" | "telemetry" | "nodeinfo"
    ) {
        return false;
    }
    for c in reg.contacts_linked_to(viewer) {
        let cid = format!("contact:{}", c.id);
        let cnodes = reg.nodes_of(&cid);
        let from_c = rec.sender.as_ref().is_some_and(|s| s.identity == cid)
            || rec.from.as_ref().is_some_and(|f| cnodes.contains(f));
        let to_c = rec.recipient.as_ref().is_some_and(|s| s.identity == cid)
            || rec.to.as_ref().is_some_and(|t| cnodes.contains(t));
        if from_c {
            return true;
        }
        if rec.kind == "text" && !rec.broadcast && to_c {
            return true;
        }
    }
    false
}

/// Apply the linked account's "hide my location history" to a record that is
/// about to be returned: a position record disappears (returns false), and a
/// position carried on a text or node-info record is removed from the copy.
/// The stored line is untouched.
pub fn redact_hidden_locations(reg: &Registry, rec: &mut Record) -> bool {
    let Some(s) = &rec.sender else { return true };
    let Some(c) = s
        .identity
        .strip_prefix("contact:")
        .and_then(|c| reg.contacts.get(c))
    else {
        return true;
    };
    let Some(t) = c.locations_hidden_ts else {
        return true;
    };
    if rec.host_ts_ms > t {
        return true;
    }
    if rec.kind == "position" {
        return false;
    }
    rec.position = None;
    true
}
