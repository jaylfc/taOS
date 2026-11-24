// SPDX-License-Identifier: AGPL-3.0-or-later
//! The Agents app: the agent list with live container state, one agent,
//! the lifecycle actions and the built-in taOS agent's configuration.
//!
//! Source: `tinyagentos/routes/agents.py` (`list_agents`, `list_agent_containers`,
//! `get_agent_endpoint`, `start_agent`, `stop_agent`, `restart_agent`,
//! `pause_agent`, `resume_agent`), `tinyagentos/routes/agent_registry.py`
//! (`list_registry`) and `tinyagentos/routes/taos_agent.py` (`get_config`).
//! The merge of stored status and live container state ports `applyLiveState`
//! and the row's paused rule from the web app (`desktop/src/apps/AgentsApp.tsx`,
//! `desktop/src/apps/agents/AgentRow.tsx`).
//!
//! Who may do what is decided by the controller on every call; a refusal comes
//! back as [`crate::Error::Forbidden`] (or [`crate::Error::NotFound`] where the route hides
//! existence) and its message is the controller's own sentence. Access and scope
//! requests are not here: see [`decisions`](crate::decisions).

use std::collections::HashMap;

use serde::{Deserialize, Serialize};
use serde_json::Value;

use crate::client::{Client, Req};
use crate::error::Result;
use crate::models::{Worker, WorkerState};
use crate::util::{encode_segment, lenient_vec, null_default, string_enum};

string_enum! {
    /// The status stored in the agent's config row.
    pub enum StoredStatus {
        /// The agent is deployed and its container should be up.
        Running => "running",
        /// Stopped.
        Stopped => "stopped",
        /// The last operation failed.
        Error => "error",
        /// A deploy is in progress.
        Deploying => "deploying",
    }
}

string_enum! {
    /// What an agent reads as once the live container state is applied: the
    /// stored status, or `Paused` for a paused flag or a frozen container.
    pub enum AgentState {
        /// Running.
        Running => "running",
        /// Paused or frozen.
        Paused => "paused",
        /// Stopped.
        Stopped => "stopped",
        /// Failed.
        Error => "error",
        /// Being deployed.
        Deploying => "deploying",
    }
}

/// A lifecycle action: `POST /api/agents/{name}/{action}`.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Hash)]
pub enum Lifecycle {
    /// Start a stopped agent.
    Start,
    /// Gracefully stop it (the server force-kills after a 2 s grace).
    Stop,
    /// Restart the container.
    Restart,
    /// Freeze the container.
    Pause,
    /// Unfreeze it and clear the paused flag.
    Resume,
}

impl Lifecycle {
    /// The path segment and the wire name.
    pub fn as_str(self) -> &'static str {
        match self {
            Self::Start => "start",
            Self::Stop => "stop",
            Self::Restart => "restart",
            Self::Pause => "pause",
            Self::Resume => "resume",
        }
    }

    /// Parse the wire name.
    pub fn parse(s: &str) -> Option<Self> {
        Some(match s {
            "start" => Self::Start,
            "stop" => Self::Stop,
            "restart" => Self::Restart,
            "pause" => Self::Pause,
            "resume" => Self::Resume,
            _ => return None,
        })
    }

    /// Stop and Restart interrupt work, so a UI asks first (web `pendingConfirm`).
    pub fn needs_confirmation(self) -> bool {
        matches!(self, Self::Stop | Self::Restart)
    }
}

/// The lifecycle actions offered in a state, in display order. A running agent
/// can be paused, restarted or stopped; a paused one only resumed (as in the web
/// row); a stopped or failed one started; a deploying one nothing.
pub fn actions_for(state: &AgentState) -> Vec<Lifecycle> {
    match state {
        AgentState::Running => vec![Lifecycle::Pause, Lifecycle::Restart, Lifecycle::Stop],
        AgentState::Paused => vec![Lifecycle::Resume],
        AgentState::Stopped => vec![Lifecycle::Start],
        AgentState::Error => vec![Lifecycle::Start, Lifecycle::Restart],
        AgentState::Deploying | AgentState::Unknown(_) => vec![],
    }
}

/// One row of `GET /api/agents/containers`: the live container of an agent.
#[derive(Debug, Clone, PartialEq, Deserialize, Serialize, Default)]
pub struct Container {
    /// Container name, `taos-agent-<agent>`.
    #[serde(default)]
    pub name: String,
    /// The agent it belongs to.
    #[serde(default)]
    pub agent_name: String,
    /// Incus status: `Running`, `Frozen`, `Stopped`, `Error`, ...
    #[serde(default)]
    pub status: String,
    /// Address, when running.
    #[serde(default, deserialize_with = "null_default")]
    pub ip: String,
    /// Memory in use, MB.
    #[serde(default)]
    pub memory_mb: Option<f64>,
    /// CPU cores.
    #[serde(default)]
    pub cpu_cores: Option<f64>,
}

/// The device an agent runs on. The config row records only `remote` (the
/// cluster worker the container was created on, `agents.py:1114` at origin/dev;
/// absent for an agent on the controller's own device). The device kind and
/// whether it is reachable come from `GET /api/cluster/workers` (`kind`,
/// `status`), joined by worker name. There is no per-device display name or
/// icon field on the server: the worker's name is the device name.
#[derive(Debug, Clone, PartialEq, Deserialize, Serialize, Default)]
pub struct Device {
    /// Worker name; empty for the controller's own device.
    #[serde(default)]
    pub name: String,
    /// True for the controller's own device.
    #[serde(default)]
    pub local: bool,
    /// The worker's `kind` (for example `worker`); empty when unknown.
    #[serde(default)]
    pub kind: String,
    /// `online`, `offline` or `unknown` (no worker row, or the cluster list was refused).
    #[serde(default)]
    pub reach: String,
}

fn device_of(remote: &str, workers: &HashMap<String, Worker>) -> Device {
    if remote.is_empty() {
        return Device {
            local: true,
            kind: "controller".into(),
            reach: "online".into(),
            ..Default::default()
        };
    }
    let w = workers.get(remote);
    let reach = match w.map(|w| &w.status) {
        Some(WorkerState::Online | WorkerState::Busy) => "online",
        Some(WorkerState::Offline) => "offline",
        _ => "unknown",
    };
    Device {
        name: remote.into(),
        local: false,
        kind: w.and_then(|w| w.kind.clone()).unwrap_or_default(),
        reach: reach.into(),
    }
}

/// An agent as the list shows it: the config row, with the live container
/// state applied and the owner joined in when it is known.
#[derive(Debug, Clone, PartialEq, Deserialize, Serialize, Default)]
pub struct Agent {
    /// Unique slug, the key of every per-agent route.
    #[serde(default)]
    pub name: String,
    /// Name to show; empty when the row has none (show `name`).
    #[serde(default, deserialize_with = "null_default")]
    pub display_name: String,
    /// Host the agent runs on.
    #[serde(default, deserialize_with = "null_default")]
    pub host: String,
    /// Identity colour from the config row (data, not a theme colour).
    #[serde(default, deserialize_with = "null_default")]
    pub color: String,
    /// Optional emoji.
    #[serde(default, deserialize_with = "null_default")]
    pub emoji: String,
    /// The framework id, for example `openclaw`.
    #[serde(default, deserialize_with = "null_default")]
    pub framework: String,
    /// The chat model.
    #[serde(default, deserialize_with = "null_default")]
    pub model: String,
    /// Stored status, before the live overlay.
    #[serde(default = "stopped")]
    pub status: StoredStatus,
    /// Number of memory vectors (the config row may hold an integer or a float).
    #[serde(default, deserialize_with = "null_default")]
    pub vectors: f64,
    /// The paused flag of the config row.
    #[serde(default)]
    pub paused: bool,
    /// The registry id that attributes the agent to a person.
    #[serde(default, deserialize_with = "null_default")]
    pub registry_canonical_id: String,
    /// Framework build the agent runs.
    #[serde(default)]
    pub framework_version_sha: Option<String>,
    /// `both`, `framework` or `taosmd`.
    #[serde(default)]
    pub memory_mode: Option<String>,
    /// The cluster worker the container lives on; empty on the controller's device.
    #[serde(default, deserialize_with = "null_default")]
    pub remote: String,
    /// Filled in by the client: the device it runs on.
    #[serde(default)]
    pub device: Device,
    /// Filled in by the client: the live container, when the controller sent one.
    #[serde(default)]
    pub container: Option<Container>,
    /// Filled in by the client: the container is frozen.
    #[serde(default)]
    pub frozen: bool,
    /// Filled in by the client: what the agent reads as.
    #[serde(default)]
    pub state: AgentState,
    /// Filled in by the client for admins: the registry's `user_id`.
    #[serde(default)]
    pub owner_id: Option<String>,
    /// Filled in by the client: the lifecycle actions offered now.
    #[serde(default)]
    pub actions: Vec<String>,
    /// True for the built-in taOS agent: no lifecycle, no delete.
    #[serde(default)]
    pub protected: bool,
}

fn stopped() -> StoredStatus {
    StoredStatus::Stopped
}

impl Agent {
    /// Apply the live container status (`applyLiveState` of the web app) and
    /// derive `state`, `frozen` and `actions`. An unknown live status keeps the
    /// stored one.
    pub fn apply_live(&mut self, live: Option<&Container>) {
        self.container = live.cloned();
        self.frozen = false;
        let mut status = self.status.clone();
        match live.map(|c| c.status.as_str()) {
            Some("Running") => status = StoredStatus::Running,
            Some("Frozen") => {
                status = StoredStatus::Running;
                self.frozen = true;
            }
            Some("Stopped") => status = StoredStatus::Stopped,
            Some("Error") => status = StoredStatus::Error,
            _ => {}
        }
        // A row without a status reads as stopped, as in the web list.
        if status == StoredStatus::Unknown(String::new()) {
            status = StoredStatus::Stopped;
        }
        // A stopped container wins over a stale paused flag; otherwise the flag
        // or a frozen container reads as paused (AgentRow.tsx:228-232).
        let stopped = status == StoredStatus::Stopped;
        let paused = !stopped && (self.paused || self.frozen);
        self.state = if paused {
            AgentState::Paused
        } else {
            match status {
                StoredStatus::Running => AgentState::Running,
                StoredStatus::Stopped => AgentState::Stopped,
                StoredStatus::Error => AgentState::Error,
                StoredStatus::Deploying => AgentState::Deploying,
                StoredStatus::Unknown(s) => AgentState::Unknown(s),
            }
        };
        self.actions = if self.protected {
            Vec::new()
        } else {
            actions_for(&self.state)
                .into_iter()
                .map(|a| a.as_str().to_string())
                .collect()
        };
    }
}

/// The built-in taOS agent (`GET /api/taos-agent/config`).
#[derive(Debug, Clone, PartialEq, Deserialize, Serialize, Default)]
pub struct TaosAgentConfig {
    /// The chat model, when one is chosen.
    #[serde(default)]
    pub model: Option<String>,
    /// Models the agent may use.
    #[serde(default, deserialize_with = "lenient_vec")]
    pub permitted_models: Vec<String>,
    /// The harness, for example `opencode`.
    #[serde(default, deserialize_with = "null_default")]
    pub framework: String,
}

/// What a lifecycle call came to. A refusal or a failure is an [`crate::Error`]; a
/// call the server answered 200 but that did not do the job (a stop whose
/// container did not stop and was not force-killed) is `ok: false`.
#[derive(Debug, Clone, PartialEq, Serialize, Default)]
pub struct ActionOutcome {
    /// The action did what was asked.
    pub ok: bool,
    /// The controller's sentence when `ok` is false.
    pub message: String,
}

/// One row of `GET /api/agents/registry`; only what the list needs.
#[derive(Debug, Deserialize, Default)]
struct RegistryRow {
    #[serde(default, deserialize_with = "null_default")]
    canonical_id: String,
    #[serde(default, deserialize_with = "null_default")]
    user_id: String,
}

/// Agents calls. Obtain with [`Client::agents`].
#[derive(Debug, Clone)]
pub struct Agents {
    client: Client,
}

impl Client {
    /// Agents calls.
    pub fn agents(&self) -> Agents {
        Agents {
            client: self.clone(),
        }
    }
}

impl Agents {
    /// `GET /api/agents` merged with `GET /api/agents/containers`.
    ///
    /// The agent list decides success: a 403 or 401 on it is returned. The
    /// containers call is best effort (a failure leaves each agent on its stored
    /// status, as the web app does). With `with_owners` the registry list
    /// (`GET /api/agents/registry`, admins see every row) is joined on
    /// `registry_canonical_id` to fill `owner_id`; its failure is ignored too.
    /// Sorted by display name, case-insensitively.
    pub async fn list(&self, with_owners: bool) -> Result<Vec<Agent>> {
        let agents_req = Req::get("/api/agents");
        let (agents, live, owners, workers) = tokio::join!(
            self.client.json::<Vec<Agent>>(agents_req),
            self.containers_best_effort(),
            self.owners_best_effort(with_owners),
            self.workers_best_effort()
        );
        let mut agents = agents?;
        for a in &mut agents {
            a.apply_live(live.get(&a.name));
            a.device = device_of(&a.remote, &workers);
            if !a.registry_canonical_id.is_empty() {
                a.owner_id = owners.get(&a.registry_canonical_id).cloned();
            }
        }
        agents.sort_by_key(|a| a.display().to_lowercase());
        Ok(agents)
    }

    /// `GET /api/agents/{name}` plus the live container. 403 for a caller that
    /// neither owns the agent nor is an admin; 404 when it does not exist.
    pub async fn get(&self, name: &str) -> Result<Agent> {
        let req = Req::get(format!("/api/agents/{}", encode_segment(name)));
        let (agent, live, workers) = tokio::join!(
            self.client.json::<Agent>(req),
            self.containers_best_effort(),
            self.workers_best_effort()
        );
        let mut agent = agent?;
        agent.apply_live(live.get(&agent.name));
        agent.device = device_of(&agent.remote, &workers);
        Ok(agent)
    }

    /// `GET /api/agents/containers`: the live containers keyed by agent name.
    pub async fn containers(&self) -> Result<Vec<Container>> {
        self.client.json(Req::get("/api/agents/containers")).await
    }

    /// Run a lifecycle action: `POST /api/agents/{name}/{action}`.
    ///
    /// Owner or admin. 404 (no such agent or container), 409 (pause of an agent
    /// that is not running) and 500 (the container operation failed, with the
    /// controller's `{"error"}` sentence) are errors. A stop answered 200 whose
    /// `stop_result.success` is false and that was not force-killed is
    /// `ok: false` with the container's output (`AgentsApp.tsx:204-209`).
    pub async fn act(&self, name: &str, action: Lifecycle) -> Result<ActionOutcome> {
        let req = Req::post(format!(
            "/api/agents/{}/{}",
            encode_segment(name),
            action.as_str()
        ));
        let v: Value = self.client.json(req).await?;
        Ok(outcome_of(action, &v))
    }

    /// `GET /api/taos-agent/config`: the built-in agent's model and harness.
    pub async fn taos_agent(&self) -> Result<TaosAgentConfig> {
        self.client.json(Req::get("/api/taos-agent/config")).await
    }

    async fn containers_best_effort(&self) -> HashMap<String, Container> {
        match self.containers().await {
            Ok(rows) => rows
                .into_iter()
                .filter(|c| !c.agent_name.is_empty() && !c.status.is_empty())
                .map(|c| (c.agent_name.clone(), c))
                .collect(),
            Err(_) => HashMap::new(),
        }
    }

    async fn workers_best_effort(&self) -> HashMap<String, Worker> {
        match self.client.models().workers().await {
            Ok(ws) => ws.into_iter().map(|w| (w.name.clone(), w)).collect(),
            Err(_) => HashMap::new(),
        }
    }

    async fn owners_best_effort(&self, wanted: bool) -> HashMap<String, String> {
        if !wanted {
            return HashMap::new();
        }
        let rows: Result<Vec<RegistryRow>> =
            self.client.json(Req::get("/api/agents/registry")).await;
        match rows {
            Ok(rows) => rows
                .into_iter()
                .filter(|r| !r.canonical_id.is_empty() && !r.user_id.is_empty())
                .map(|r| (r.canonical_id, r.user_id))
                .collect(),
            Err(_) => HashMap::new(),
        }
    }
}

impl Agent {
    /// The name to show: the display name, else the slug.
    pub fn display(&self) -> &str {
        if self.display_name.is_empty() {
            &self.name
        } else {
            &self.display_name
        }
    }
}

fn outcome_of(action: Lifecycle, v: &Value) -> ActionOutcome {
    if action == Lifecycle::Stop {
        let stop = v.get("stop_result");
        let success = stop.and_then(|s| s.get("success")).and_then(Value::as_bool);
        let forced = v
            .get("force_killed")
            .and_then(Value::as_bool)
            .unwrap_or(false);
        if success == Some(false) && !forced {
            let out = stop
                .and_then(|s| s.get("output"))
                .and_then(Value::as_str)
                .unwrap_or("");
            return ActionOutcome {
                ok: false,
                message: if out.trim().is_empty() {
                    "The container did not stop.".to_string()
                } else {
                    out.to_string()
                },
            };
        }
    }
    ActionOutcome {
        ok: true,
        message: String::new(),
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn container(agent: &str, status: &str) -> Container {
        Container {
            name: format!("taos-agent-{agent}"),
            agent_name: agent.into(),
            status: status.into(),
            ..Default::default()
        }
    }

    fn agent(status: &str, paused: bool) -> Agent {
        serde_json::from_value(serde_json::json!({
            "name": "a", "status": status, "paused": paused
        }))
        .expect("agent")
    }

    #[test]
    fn live_state_overlay_matches_the_web_rules() {
        let cases: &[(&str, bool, Option<&str>, AgentState, bool)] = &[
            (
                "stopped",
                false,
                Some("Running"),
                AgentState::Running,
                false,
            ),
            ("running", false, Some("Frozen"), AgentState::Paused, true),
            ("running", true, Some("Stopped"), AgentState::Stopped, false),
            ("running", false, Some("Error"), AgentState::Error, false),
            // a stopped container wins over a stale paused flag
            ("running", true, Some("Stopped"), AgentState::Stopped, false),
            // no live row: the stored status and the flag decide
            ("running", true, None, AgentState::Paused, false),
            ("running", false, None, AgentState::Running, false),
            ("deploying", false, None, AgentState::Deploying, false),
            // an unknown live status keeps the stored one
            (
                "stopped",
                false,
                Some("Starting"),
                AgentState::Stopped,
                false,
            ),
        ];
        for (stored, paused, live, want, frozen) in cases {
            let mut a = agent(stored, *paused);
            let c = live.map(|s| container("a", s));
            a.apply_live(c.as_ref());
            assert_eq!(&a.state, want, "{stored} {paused} {live:?}");
            assert_eq!(a.frozen, *frozen, "{stored} {paused} {live:?}");
        }
    }

    #[test]
    fn actions_follow_the_state() {
        let names = |s: AgentState| -> Vec<&'static str> {
            actions_for(&s).into_iter().map(Lifecycle::as_str).collect()
        };
        assert_eq!(names(AgentState::Running), ["pause", "restart", "stop"]);
        assert_eq!(names(AgentState::Paused), ["resume"]);
        assert_eq!(names(AgentState::Stopped), ["start"]);
        assert_eq!(names(AgentState::Error), ["start", "restart"]);
        assert!(names(AgentState::Deploying).is_empty());
        let mut a = agent("running", false);
        a.protected = true;
        a.apply_live(None);
        assert!(a.actions.is_empty(), "the taOS agent has no lifecycle");
    }

    #[test]
    fn only_stop_and_restart_ask_first() {
        for (a, ask) in [
            (Lifecycle::Start, false),
            (Lifecycle::Stop, true),
            (Lifecycle::Restart, true),
            (Lifecycle::Pause, false),
            (Lifecycle::Resume, false),
        ] {
            assert_eq!(a.needs_confirmation(), ask, "{a:?}");
            assert_eq!(Lifecycle::parse(a.as_str()), Some(a));
        }
        assert_eq!(Lifecycle::parse("delete"), None);
    }

    #[test]
    fn a_failed_stop_is_not_a_success_unless_force_killed() {
        let fail = serde_json::json!({"stop_result": {"success": false, "output": "busy"}, "force_killed": false});
        let o = outcome_of(Lifecycle::Stop, &fail);
        assert!(!o.ok);
        assert_eq!(o.message, "busy");
        let rescued = serde_json::json!({"stop_result": {"success": false, "output": "busy"}, "force_killed": true});
        assert!(outcome_of(Lifecycle::Stop, &rescued).ok);
        let silent = serde_json::json!({"stop_result": {"success": false, "output": " "}});
        assert_eq!(
            outcome_of(Lifecycle::Stop, &silent).message,
            "The container did not stop."
        );
        // the same body on another action is not a stop
        assert!(outcome_of(Lifecycle::Restart, &fail).ok);
    }
}
