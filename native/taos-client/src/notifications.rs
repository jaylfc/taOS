//! Notifications, plus the consent actions shown on notification rows.
//!
//! Source: `tinyagentos/routes/notifications.py`,
//! `tinyagentos/routes/agent_auth_requests.py`, `tinyagentos/routes/auth_broker.py`,
//! `tinyagentos/routes/projects.py`. Writes need the CSRF header, which the
//! transport adds.

use serde::{Deserialize, Serialize};
use serde_json::Value;

use crate::client::{Client, Req};
use crate::error::{Error, Result};
use crate::util::{encode_segment, string_enum};

string_enum! {
    /// Severity of a notification.
    pub enum Level {
        /// Informational.
        Info => "info",
        /// Something worked.
        Success => "success",
        /// Needs attention.
        Warning => "warning",
        /// Something failed.
        Error => "error",
    }
}

impl Level {
    /// The level to display: unknown values are shown as `Info`, as the web UI does.
    pub fn normalized(&self) -> Level {
        match self {
            Level::Unknown(_) => Level::Info,
            other => other.clone(),
        }
    }
}

/// Sources whose rows are pinned to the top and are only marked read, never
/// archived, while their request is pending (`PINNED_SOURCES` in
/// `tinyagentos/notifications.py`).
pub const PINNED_SOURCES: [&str; 2] = ["auth_requests", "agent_scope_requests"];

/// One server notification row.
///
/// Differences from the web client's model are intentional: this is the wire
/// shape. The web store's `srv-<id>` id is [`Notification::store_id`].
#[derive(Debug, Clone, PartialEq, Deserialize, Serialize, Default)]
pub struct Notification {
    /// Row id.
    #[serde(default)]
    pub id: i64,
    /// Creation time, epoch seconds.
    #[serde(default)]
    pub timestamp: f64,
    /// Severity.
    #[serde(default)]
    pub level: Level,
    /// Title line.
    #[serde(default)]
    pub title: String,
    /// Body text (the web client calls it `body`).
    #[serde(default)]
    pub message: String,
    /// Read by the calling user.
    #[serde(default)]
    pub read: bool,
    /// Producer, for example `auth_requests` or `system.update`.
    #[serde(default)]
    pub source: Option<String>,
    /// Source-specific payload (`request_id`, `target`, ...).
    #[serde(default)]
    pub data: Option<Value>,
    /// Owner user id, `None` for a broadcast.
    #[serde(default)]
    pub user_id: Option<String>,
    /// Set by the server on pinned grant rows in the user listing.
    #[serde(default)]
    pub pinned: bool,
}

impl Notification {
    /// The id the web store uses for server rows: `srv-<id>`.
    pub fn store_id(&self) -> String {
        format!("srv-{}", self.id)
    }

    /// True for consent rows (access and scope requests).
    pub fn is_pinned_grant(&self) -> bool {
        self.pinned
            || self
                .source
                .as_deref()
                .is_some_and(|s| PINNED_SOURCES.contains(&s))
    }

    /// The consent request id carried in `data.request_id`, if any.
    pub fn request_id(&self) -> Option<String> {
        match self.data.as_ref()?.get("request_id")? {
            Value::String(s) => Some(s.clone()),
            Value::Number(n) => Some(n.to_string()),
            _ => None,
        }
    }
}

/// What archiving a notification did.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum ArchiveOutcome {
    /// The row was archived.
    Archived,
    /// The row belongs to a request that is still pending, so the server only
    /// marked it read (`{"ok": true, "kept": "pending-request"}`).
    KeptPendingRequest,
}

/// Result of [`Notifications::clear`].
#[derive(Debug, Clone, Copy, PartialEq, Eq, Default)]
pub struct ClearSummary {
    /// Rows archived.
    pub archived: usize,
    /// Pending grant rows left in place (only marked read when the server kept them).
    pub kept: usize,
}

/// The scope vocabulary (`GET /api/agents/scope-vocabulary`).
#[derive(Debug, Clone, PartialEq, Deserialize, Serialize, Default)]
pub struct ScopeVocabulary {
    /// Every grantable scope. The web spec lists only `project_scopes`; the
    /// route also returns this.
    #[serde(default)]
    pub valid_scopes: Vec<String>,
    /// Scopes that cannot be granted without a project.
    #[serde(default)]
    pub project_scopes: Vec<String>,
}

/// Body for approving an access (auth) request.
#[derive(Debug, Clone, PartialEq, Serialize, Default)]
pub struct ApproveAccess {
    /// Scopes to grant; may narrow but never widen the request.
    pub granted_scopes: Vec<String>,
    /// Project for project-bound scopes.
    #[serde(skip_serializing_if = "Option::is_none")]
    pub project_id: Option<String>,
    /// Make the agent the project's lead.
    #[serde(skip_serializing_if = "is_false")]
    pub is_lead: bool,
}

fn is_false(b: &bool) -> bool {
    !*b
}

/// Result of approving an access request.
#[derive(Debug, Clone, PartialEq, Deserialize, Serialize, Default)]
pub struct AccessApproval {
    /// `accepted`.
    #[serde(default)]
    pub status: String,
    /// The minted agent identity.
    #[serde(default)]
    pub canonical_id: String,
    /// Present only when `is_lead` was sent: whether the lead pointer landed.
    #[serde(default)]
    pub lead_assigned: Option<bool>,
    /// Requested validity in seconds.
    #[serde(default)]
    pub duration_secs: Option<i64>,
    /// Human readable validity.
    #[serde(default)]
    pub human_duration: Option<String>,
}

impl AccessApproval {
    /// True when the caller asked for lead but the server did not confirm it:
    /// the "lead unconfirmed" state of the consent UI.
    pub fn lead_unconfirmed(&self, requested_lead: bool) -> bool {
        requested_lead && self.lead_assigned != Some(true)
    }
}

/// Body for approving a scope request.
#[derive(Debug, Clone, PartialEq, Serialize, Default)]
pub struct ApproveScopes {
    /// Scopes to grant; may narrow but never widen the request.
    pub granted_scopes: Vec<String>,
    /// Project for project-bound scopes.
    #[serde(skip_serializing_if = "Option::is_none")]
    pub project_id: Option<String>,
}

/// Result of approving a scope request.
#[derive(Debug, Clone, PartialEq, Deserialize, Serialize, Default)]
pub struct ScopeApproval {
    /// `accepted`.
    #[serde(default)]
    pub status: String,
    /// The agent whose grants changed.
    #[serde(default)]
    pub canonical_id: String,
    /// The scopes now granted by this decision.
    #[serde(default)]
    pub granted_scopes: Vec<String>,
}

/// A project as the consent picker needs it.
#[derive(Debug, Clone, PartialEq, Deserialize, Serialize, Default)]
pub struct ProjectRef {
    /// Project id.
    #[serde(default)]
    pub id: String,
    /// Display name.
    #[serde(default)]
    pub name: String,
    /// URL slug.
    #[serde(default)]
    pub slug: String,
}

/// Notification and consent calls. Obtain with [`Client::notifications`].
#[derive(Debug, Clone)]
pub struct Notifications {
    client: Client,
}

impl Client {
    /// Notification and consent calls.
    pub fn notifications(&self) -> Notifications {
        Notifications {
            client: self.clone(),
        }
    }
}

impl Notifications {
    /// `GET /api/notifications[?unread_only=true]`.
    ///
    /// The server returns pinned grant rows (all of them) plus at most 20
    /// other rows, newest first. A body that is not a JSON array is treated as
    /// an empty list, as the web client does. The `hx-request` header is never
    /// sent (it would switch the route to HTML).
    pub async fn list(&self, unread_only: bool) -> Result<Vec<Notification>> {
        let mut req = Req::get("/api/notifications");
        if unread_only {
            req = req.query("unread_only", "true");
        }
        let resp = self.client.send(&req).await?;
        let bytes = resp.bytes().await.map_err(Error::Transport)?;
        let value: Value = match serde_json::from_slice(&bytes) {
            Ok(v) => v,
            Err(_) => return Ok(Vec::new()),
        };
        let Value::Array(items) = value else {
            return Ok(Vec::new());
        };
        items
            .into_iter()
            .map(|i| {
                serde_json::from_value(i)
                    .map_err(|e| Error::decode(".".into(), e.to_string(), &bytes))
            })
            .collect()
    }

    /// `POST /api/notifications/{id}/read`. 404 when the row is not yours.
    pub async fn mark_read(&self, id: i64) -> Result<()> {
        self.client
            .send(&Req::post(format!("/api/notifications/{id}/read")))
            .await?;
        Ok(())
    }

    /// `POST /api/notifications/read-all`; returns how many rows were marked.
    pub async fn mark_all_read(&self) -> Result<u64> {
        #[derive(Deserialize)]
        struct R {
            #[serde(default)]
            marked: u64,
        }
        Ok(self
            .client
            .json::<R>(Req::post("/api/notifications/read-all"))
            .await?
            .marked)
    }

    /// `POST /api/notifications/{id}/archive` (the web UI's "dismiss").
    ///
    /// For a pending consent row the server only marks it read and says so,
    /// reported as [`ArchiveOutcome::KeptPendingRequest`].
    pub async fn archive(&self, id: i64) -> Result<ArchiveOutcome> {
        #[derive(Deserialize)]
        struct R {
            #[serde(default)]
            kept: Option<String>,
        }
        let r: R = self
            .client
            .json(Req::post(format!("/api/notifications/{id}/archive")))
            .await?;
        Ok(match r.kept.as_deref() {
            Some("pending-request") => ArchiveOutcome::KeptPendingRequest,
            _ => ArchiveOutcome::Archived,
        })
    }

    /// "Clear all": list the notifications and archive every one except
    /// pending grant rows (`auth_requests`, `agent_scope_requests`), one
    /// `archive` call per row, as the web store does.
    pub async fn clear(&self) -> Result<ClearSummary> {
        let mut summary = ClearSummary::default();
        for n in self.list(false).await? {
            if n.is_pinned_grant() {
                summary.kept += 1;
                continue;
            }
            match self.archive(n.id).await? {
                ArchiveOutcome::Archived => summary.archived += 1,
                ArchiveOutcome::KeptPendingRequest => summary.kept += 1,
            }
        }
        Ok(summary)
    }

    // ----- consent actions -------------------------------------------------

    /// `GET /api/agents/scope-vocabulary`. The consent UI keeps Allow disabled
    /// until this is known.
    pub async fn scope_vocabulary(&self) -> Result<ScopeVocabulary> {
        self.client
            .json(Req::get("/api/agents/scope-vocabulary"))
            .await
    }

    /// `GET /api/projects?status=active`, for the project picker. The route
    /// returns `{"items": [...]}`.
    pub async fn active_projects(&self) -> Result<Vec<ProjectRef>> {
        #[derive(Deserialize)]
        struct R {
            #[serde(default)]
            items: Vec<ProjectRef>,
        }
        Ok(self
            .client
            .json::<R>(Req::get("/api/projects").query("status", "active"))
            .await?
            .items)
    }

    /// `POST /api/projects {name, slug}`: the picker's inline create. A name or
    /// slug clash gives [`Error::Conflict`].
    pub async fn create_project(&self, name: &str, slug: &str) -> Result<ProjectRef> {
        let body = serde_json::json!({ "name": name, "slug": slug });
        self.client
            .json(Req::post("/api/projects").json_body(&body)?)
            .await
    }

    /// `POST /api/agents/auth-requests/{id}/approve`. Admin only.
    pub async fn approve_access_request(
        &self,
        request_id: &str,
        body: &ApproveAccess,
    ) -> Result<AccessApproval> {
        let path = format!(
            "/api/agents/auth-requests/{}/approve",
            encode_segment(request_id)
        );
        self.client.json(Req::post(path).json_body(body)?).await
    }

    /// `POST /api/agents/auth-requests/{id}/deny`. Admin only. The route takes
    /// no body, so there is no way to send a reason for an access request.
    pub async fn deny_access_request(&self, request_id: &str) -> Result<()> {
        let path = format!(
            "/api/agents/auth-requests/{}/deny",
            encode_segment(request_id)
        );
        self.client.send(&Req::post(path)).await?;
        Ok(())
    }

    /// `POST /api/agents/registry/{canonical_id}/scope-requests/{id}/approve`.
    pub async fn approve_scope_request(
        &self,
        canonical_id: &str,
        request_id: &str,
        body: &ApproveScopes,
    ) -> Result<ScopeApproval> {
        let path = format!(
            "/api/agents/registry/{}/scope-requests/{}/approve",
            encode_segment(canonical_id),
            encode_segment(request_id)
        );
        self.client.json(Req::post(path).json_body(body)?).await
    }

    /// `POST /api/agents/registry/{canonical_id}/scope-requests/{id}/deny`
    /// with an optional reason (at most 2000 characters).
    pub async fn deny_scope_request(
        &self,
        canonical_id: &str,
        request_id: &str,
        reason: Option<&str>,
    ) -> Result<()> {
        let path = format!(
            "/api/agents/registry/{}/scope-requests/{}/deny",
            encode_segment(canonical_id),
            encode_segment(request_id)
        );
        let body = serde_json::json!({ "reason": reason.unwrap_or("") });
        self.client.send(&Req::post(path).json_body(&body)?).await?;
        Ok(())
    }

    /// `POST /api/auth-broker/requests/{id}/code {code}`: answer a sign-in
    /// card. 410 (expired) arrives as [`Error::Http`] with status 410 and 409
    /// as [`Error::Conflict`].
    pub async fn submit_sign_in_code(&self, request_id: &str, code: &str) -> Result<()> {
        let path = format!(
            "/api/auth-broker/requests/{}/code",
            encode_segment(request_id)
        );
        let body = serde_json::json!({ "code": code });
        self.client.send(&Req::post(path).json_body(&body)?).await?;
        Ok(())
    }
}
