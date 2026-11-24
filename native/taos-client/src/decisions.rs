// SPDX-License-Identifier: AGPL-3.0-or-later
//! The Decisions inbox: questions agents ask the user, plus the pending access
//! and scope requests shown beside them.
//!
//! Source: `tinyagentos/routes/decisions.py`, `tinyagentos/decisions/decision_store.py`,
//! `tinyagentos/routes/agent_auth_requests.py`. Approving or denying access and
//! scope requests lives in [`Notifications`](crate::notifications::Notifications).

use serde::{Deserialize, Serialize};
use serde_json::Value;

use crate::client::{Client, Req};
use crate::error::{Error, Result};
use crate::util::{encode_segment, lenient_vec, string_enum, Timestamp};

/// Maximum length of an "Other" answer or a free-text answer (web UI limit).
pub const MAX_ANSWER_CHARS: usize = 20_000;
/// Maximum length of an answer note or a standalone note (web UI limit).
pub const MAX_NOTE_CHARS: usize = 500;

string_enum! {
    /// How a decision is answered.
    pub enum DecisionType {
        /// Pick one option.
        SingleSelect => "single_select",
        /// Pick any number of options.
        MultiSelect => "multi_select",
        /// Approve or deny.
        ApproveDeny => "approve_deny",
        /// Free text.
        FreeText => "free_text",
    }
}

string_enum! {
    /// Lifecycle state of a decision.
    pub enum DecisionStatus {
        /// Waiting on the user.
        Pending => "pending",
        /// Answered.
        Answered => "answered",
        /// Timed out.
        Expired => "expired",
        /// Replaced by a later decision.
        Superseded => "superseded",
        /// Withdrawn by the asker.
        Withdrawn => "withdrawn",
    }
}

string_enum! {
    /// How urgent a decision is.
    pub enum Priority {
        /// Ordinary.
        Normal => "normal",
        /// The agent is blocked until answered.
        Blocking => "blocking",
    }
}

/// One selectable option of a decision.
#[derive(Debug, Clone, PartialEq, Deserialize, Serialize, Default)]
pub struct DecisionOption {
    /// Text to show.
    #[serde(default)]
    pub label: String,
    /// Identity of the option; send this back when answering. The server
    /// defaults it to the label.
    #[serde(default)]
    pub value: String,
    /// The asker recommends this option.
    #[serde(default)]
    pub recommended: bool,
    /// Why it is recommended.
    #[serde(default)]
    pub rationale: Option<String>,
}

/// The recorded answer of an answered decision.
#[derive(Debug, Clone, PartialEq, Deserialize, Serialize, Default)]
pub struct RecordedAnswer {
    /// An option value, a list of them, `approve`/`deny`, or the free text.
    #[serde(default)]
    pub value: Value,
    /// Who answered.
    #[serde(default)]
    pub answered_by: Option<String>,
    /// When, epoch seconds.
    #[serde(default)]
    pub answered_at: Option<f64>,
    /// `in_app` or `mirrored_from_chat`.
    #[serde(default)]
    pub source: Option<String>,
    /// Text of an "Other" answer.
    #[serde(default)]
    pub other_value: Option<String>,
    /// Optional note given with the answer.
    #[serde(default)]
    pub note: Option<String>,
}

/// A note attached to a decision.
#[derive(Debug, Clone, PartialEq, Deserialize, Serialize, Default)]
pub struct DecisionNote {
    /// Note id.
    #[serde(default)]
    pub id: String,
    /// The note text.
    #[serde(default)]
    pub text: String,
    /// Author user id or agent.
    #[serde(default)]
    pub author: String,
    /// `in_app` or `mirrored_from_chat`.
    #[serde(default)]
    pub source: String,
    /// When it was added.
    #[serde(default)]
    pub created_at: Option<Timestamp>,
}

/// A decision. Unknown fields and unknown enum values are tolerated.
#[derive(Debug, Clone, PartialEq, Deserialize, Serialize, Default)]
pub struct Decision {
    /// Decision id.
    #[serde(default)]
    pub id: String,
    /// The asking agent.
    #[serde(default)]
    pub from_agent: String,
    /// Project the question belongs to.
    #[serde(default)]
    pub project_id: Option<String>,
    /// Owning user.
    #[serde(default)]
    pub user_id: Option<String>,
    /// The question.
    #[serde(default)]
    pub question: String,
    /// How it is answered (JSON key `type`).
    #[serde(default, rename = "type")]
    pub kind: DecisionType,
    /// Options for the select types.
    #[serde(default, deserialize_with = "lenient_vec")]
    pub options: Vec<DecisionOption>,
    /// Extra context.
    #[serde(default)]
    pub context: Option<String>,
    /// Urgency.
    #[serde(default)]
    pub priority: Priority,
    /// Lifecycle state.
    #[serde(default)]
    pub status: DecisionStatus,
    /// The answer, once answered.
    #[serde(default)]
    pub answer: Option<RecordedAnswer>,
    /// Creation time. The store writes epoch seconds; ISO strings are accepted too.
    #[serde(default)]
    pub created_at: Option<Timestamp>,
    /// Answer time, epoch seconds.
    #[serde(default)]
    pub answered_at: Option<f64>,
    /// Deadline, epoch seconds.
    #[serde(default)]
    pub deadline: Option<f64>,
    /// The decision this one supersedes.
    #[serde(default)]
    pub parent_decision_id: Option<String>,
    /// Notes, oldest first.
    #[serde(default, deserialize_with = "lenient_vec")]
    pub notes: Vec<DecisionNote>,
    /// Why the asker withdrew it.
    #[serde(default)]
    pub withdraw_reason: Option<String>,
    /// When it was withdrawn.
    #[serde(default)]
    pub withdrawn_at: Option<f64>,
    /// Who withdrew it.
    #[serde(default)]
    pub withdrawn_by: Option<String>,
    /// Kind-specific data, for example gate-grant details (`metadata.kind`).
    #[serde(default)]
    pub metadata: Value,
}

/// The value part of an answer.
#[derive(Debug, Clone, PartialEq)]
pub enum AnswerValue {
    /// One string: an option value, `approve`/`deny`, or free text.
    Single(String),
    /// Several option values (multi-select).
    Multi(Vec<String>),
}

/// An answer to send with [`Decisions::answer`].
///
/// The four forms of the web UI, plus "Other":
/// * single select: [`AnswerRequest::option`]
/// * multi select: [`AnswerRequest::options`]
/// * approve or deny: [`AnswerRequest::approve`] / [`AnswerRequest::deny`]
/// * free text: [`AnswerRequest::text`]
/// * "Other" on a select type: [`AnswerRequest::other`] / [`AnswerRequest::options_and_other`]
#[derive(Debug, Clone, PartialEq)]
pub struct AnswerRequest {
    value: AnswerValue,
    other_value: Option<String>,
    note: Option<String>,
}

impl AnswerRequest {
    /// Pick one option of a `single_select` decision.
    pub fn option(value: impl Into<String>) -> Self {
        Self {
            value: AnswerValue::Single(value.into()),
            other_value: None,
            note: None,
        }
    }
    /// Pick options of a `multi_select` decision.
    pub fn options<I, S>(values: I) -> Self
    where
        I: IntoIterator<Item = S>,
        S: Into<String>,
    {
        Self {
            value: AnswerValue::Multi(values.into_iter().map(Into::into).collect()),
            other_value: None,
            note: None,
        }
    }
    /// Approve an `approve_deny` decision.
    pub fn approve() -> Self {
        Self::option("approve")
    }
    /// Deny an `approve_deny` decision.
    pub fn deny() -> Self {
        Self::option("deny")
    }
    /// Answer a `free_text` decision.
    pub fn text(text: impl Into<String>) -> Self {
        Self::option(text)
    }
    /// The "Other" row of a `single_select` decision: an empty value plus the text.
    pub fn other(text: impl Into<String>) -> Self {
        Self {
            value: AnswerValue::Single(String::new()),
            other_value: Some(text.into()),
            note: None,
        }
    }
    /// Options plus an "Other" text on a `multi_select` decision (either part
    /// may be empty, not both).
    pub fn options_and_other<I, S>(values: I, other: impl Into<String>) -> Self
    where
        I: IntoIterator<Item = S>,
        S: Into<String>,
    {
        Self {
            value: AnswerValue::Multi(values.into_iter().map(Into::into).collect()),
            other_value: Some(other.into()),
            note: None,
        }
    }
    /// Attach the optional answer note (at most [`MAX_NOTE_CHARS`] characters).
    pub fn with_note(mut self, note: impl Into<String>) -> Self {
        self.note = Some(note.into());
        self
    }

    fn validate(&self) -> Result<()> {
        let too_long = |s: &str, max: usize| s.chars().count() > max;
        if let Some(o) = &self.other_value {
            if too_long(o, MAX_ANSWER_CHARS) {
                return Err(Error::InvalidInput(format!(
                    "answer is longer than {MAX_ANSWER_CHARS} characters"
                )));
            }
        }
        if let AnswerValue::Single(v) = &self.value {
            if too_long(v, MAX_ANSWER_CHARS) {
                return Err(Error::InvalidInput(format!(
                    "answer is longer than {MAX_ANSWER_CHARS} characters"
                )));
            }
        }
        if self
            .note
            .as_deref()
            .is_some_and(|n| too_long(n, MAX_NOTE_CHARS))
        {
            return Err(Error::InvalidInput(format!(
                "note is longer than {MAX_NOTE_CHARS} characters"
            )));
        }
        Ok(())
    }

    fn to_json(&self) -> Value {
        let value = match &self.value {
            AnswerValue::Single(s) => Value::String(s.clone()),
            AnswerValue::Multi(v) => Value::Array(v.iter().cloned().map(Value::String).collect()),
        };
        let mut body = serde_json::json!({ "value": value });
        if let Some(o) = &self.other_value {
            body["other_value"] = Value::String(o.clone());
        }
        if let Some(n) = &self.note {
            body["note"] = Value::String(n.clone());
        }
        body
    }
}

/// A pending access (auth) request from an external agent
/// (`GET /api/agents/auth-requests`). The row's token fields are never read.
#[derive(Debug, Clone, PartialEq, Deserialize, Serialize, Default)]
pub struct AccessRequest {
    /// Request id.
    #[serde(default)]
    pub id: String,
    /// The identity the agent claims.
    #[serde(default)]
    pub identity_claim: String,
    /// The agent's framework.
    #[serde(default)]
    pub framework: String,
    /// Scopes asked for.
    #[serde(default)]
    pub requested_scopes: Vec<String>,
    /// Why.
    #[serde(default)]
    pub reason: Option<String>,
    /// Project asked for.
    #[serde(default)]
    pub project_id: Option<String>,
    /// `pending`.
    #[serde(default)]
    pub status: String,
    /// Requested validity in seconds.
    #[serde(default)]
    pub duration_secs: Option<i64>,
    /// Human readable validity, for example "expires 2 hours after approval".
    #[serde(default)]
    pub human_duration: Option<String>,
}

/// A pending scope request from an existing agent
/// (`GET /api/agents/scope-requests`).
#[derive(Debug, Clone, PartialEq, Deserialize, Serialize, Default)]
pub struct ScopeRequest {
    /// Request id.
    #[serde(default)]
    pub id: String,
    /// The agent asking.
    #[serde(default)]
    pub canonical_id: String,
    /// The agent's display name.
    #[serde(default)]
    pub agent_display_name: Option<String>,
    /// Scopes asked for.
    #[serde(default)]
    pub requested_scopes: Vec<String>,
    /// Project asked for.
    #[serde(default)]
    pub project_id: Option<String>,
    /// Why.
    #[serde(default)]
    pub reason: Option<String>,
    /// `pending`, `accepted` or `refused`.
    #[serde(default)]
    pub status: String,
    /// Scopes granted by the decision.
    #[serde(default)]
    pub granted_scopes: Option<Vec<String>>,
    /// Creation time, epoch seconds.
    #[serde(default)]
    pub created_ts: Option<f64>,
    /// Decision time, epoch seconds.
    #[serde(default)]
    pub decided_ts: Option<f64>,
    /// Reason given when it was refused.
    #[serde(default)]
    pub deny_reason: Option<String>,
}

/// Which decisions to list.
#[derive(Debug, Clone, Default)]
pub struct DecisionFilter {
    /// `pending`, `answered` or `withdrawn` (any status string is passed through).
    pub status: Option<String>,
    /// Only this project.
    pub project_id: Option<String>,
    /// Maximum rows (server default 200, maximum 500).
    pub limit: Option<u32>,
}

/// Decisions calls. Obtain with [`Client::decisions`].
#[derive(Debug, Clone)]
pub struct Decisions {
    client: Client,
}

impl Client {
    /// Decisions calls.
    pub fn decisions(&self) -> Decisions {
        Decisions {
            client: self.clone(),
        }
    }
}

impl Decisions {
    /// `GET /api/decisions?status=&project_id=&limit=`. Non-admins get their
    /// own decisions, admins get all. The route returns `{"items": [...]}`; a
    /// bare array is accepted too.
    pub async fn list(&self, filter: &DecisionFilter) -> Result<Vec<Decision>> {
        let req = Req::get("/api/decisions")
            .opt_query("status", filter.status.as_deref())
            .opt_query("project_id", filter.project_id.as_deref())
            .opt_query("limit", filter.limit);
        self.items(req).await
    }

    /// `GET /api/decisions/{id}`.
    pub async fn get(&self, id: &str) -> Result<Decision> {
        self.client
            .json(Req::get(format!("/api/decisions/{}", encode_segment(id))))
            .await
    }

    /// `GET /api/decisions/{id}/history`: the supersession chain, oldest first.
    pub async fn history(&self, id: &str) -> Result<Vec<Decision>> {
        self.items(Req::get(format!(
            "/api/decisions/{}/history",
            encode_segment(id)
        )))
        .await
    }

    /// `POST /api/decisions/{id}/answer`.
    ///
    /// The server's refusals use `{"error": ...}` (and 409 "already answered
    /// or not pending"); the text is available in the error's `message`, so a
    /// UI can show it verbatim. Gate decisions cannot be answered by a device
    /// bearer; this client uses a session, which can.
    pub async fn answer(&self, id: &str, answer: &AnswerRequest) -> Result<Decision> {
        answer.validate()?;
        let req = Req::post(format!("/api/decisions/{}/answer", encode_segment(id)));
        let mut req = req;
        req.json = Some(answer.to_json());
        self.client.json(req).await
    }

    /// `POST /api/decisions/{id}/note`; returns the updated decision. An empty
    /// text is refused by the server with 400 "text must not be empty".
    pub async fn add_note(&self, id: &str, text: &str) -> Result<Decision> {
        if text.chars().count() > MAX_NOTE_CHARS {
            return Err(Error::InvalidInput(format!(
                "note is longer than {MAX_NOTE_CHARS} characters"
            )));
        }
        let body = serde_json::json!({ "text": text });
        let req =
            Req::post(format!("/api/decisions/{}/note", encode_segment(id))).json_body(&body)?;
        self.client.json(req).await
    }

    /// `GET /api/agents/auth-requests?status=pending`: access requests
    /// (admin only). The route returns `{"requests": [...]}`.
    pub async fn access_requests(&self) -> Result<Vec<AccessRequest>> {
        #[derive(Deserialize)]
        struct R {
            #[serde(default, deserialize_with = "lenient_vec")]
            requests: Vec<AccessRequest>,
        }
        Ok(self
            .client
            .json::<R>(
                Req::get("/api/agents/auth-requests")
                    .query("status", "pending")
                    .sensitive(),
            )
            .await?
            .requests)
    }

    /// `GET /api/agents/scope-requests?status=pending`: scope requests (admin
    /// or agent owner). The route returns `{"requests": [...]}`.
    pub async fn scope_requests(&self) -> Result<Vec<ScopeRequest>> {
        #[derive(Deserialize)]
        struct R {
            #[serde(default, deserialize_with = "lenient_vec")]
            requests: Vec<ScopeRequest>,
        }
        Ok(self
            .client
            .json::<R>(Req::get("/api/agents/scope-requests").query("status", "pending"))
            .await?
            .requests)
    }

    async fn items(&self, req: Req) -> Result<Vec<Decision>> {
        let resp = self.client.send(&req).await?;
        let bytes = resp.bytes().await.map_err(Error::Transport)?;
        #[derive(Deserialize)]
        struct Wrapped {
            #[serde(default, deserialize_with = "lenient_vec")]
            items: Vec<Decision>,
        }
        // Decide by the first byte rather than with an untagged enum, so a
        // decode error keeps its JSON path.
        if bytes.iter().find(|b| !b.is_ascii_whitespace()) == Some(&b'[') {
            crate::client::decode::<Vec<Decision>>(&bytes)
        } else {
            Ok(crate::client::decode::<Wrapped>(&bytes)?.items)
        }
    }
}
