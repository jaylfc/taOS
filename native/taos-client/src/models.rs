//! Models: the catalog, downloaded files, cluster workers, cloud providers,
//! downloads with progress, and deleting.
//!
//! Sources: `tinyagentos/routes/models.py`, `routes/cluster.py`,
//! `routes/providers.py`. Downloading and deleting are admin-only on the
//! server: a member gets [`Error::Forbidden`](crate::Error::Forbidden); check
//! [`User::is_admin`](crate::auth::User::is_admin) to hide the actions.
//!
//! The controller has **no download cancel route**. [`DownloadWatch::cancel`]
//! stops this client from polling; the server-side download keeps running.

use std::pin::Pin;
use std::task::{Context, Poll};
use std::time::Duration;

use futures_util::Stream;
use serde::{Deserialize, Deserializer, Serialize};
use serde_json::Value;
use tokio::sync::mpsc;
use tokio::task::JoinHandle;

use crate::client::{Client, Req};
use crate::error::Result;
use crate::util::{encode_segment, lenient_vec, string_enum};

string_enum! {
    /// How well a model fits the hardware.
    pub enum Compatibility {
        /// Recommended.
        Green => "green",
        /// May be slow.
        Yellow => "yellow",
        /// Too large.
        Red => "red",
    }
}

string_enum! {
    /// State of a download.
    pub enum DownloadStatus {
        /// Queued.
        Pending => "pending",
        /// Running.
        Downloading => "downloading",
        /// Finished.
        Complete => "complete",
        /// Failed; see `error`.
        Error => "error",
    }
}

/// A model reference that the controller sends either as a bare string or as
/// an object `{id, name, size_mb}` (worker and provider model lists).
#[derive(Debug, Clone, PartialEq, Default, Serialize)]
pub struct ModelRef {
    /// Model id.
    pub id: String,
    /// Display name (the id when none was sent).
    pub name: String,
    /// Size in MB when known.
    pub size_mb: Option<f64>,
}

impl<'de> Deserialize<'de> for ModelRef {
    fn deserialize<D: Deserializer<'de>>(d: D) -> Result<Self, D::Error> {
        Ok(match Value::deserialize(d)? {
            Value::String(s) => Self {
                id: s.clone(),
                name: s,
                size_mb: None,
            },
            Value::Object(o) => {
                let text = |k: &str| o.get(k).and_then(Value::as_str).map(str::to_string);
                let id = text("id")
                    .or_else(|| text("name"))
                    .or_else(|| text("model"))
                    .unwrap_or_default();
                let name = text("name").unwrap_or_else(|| id.clone());
                Self {
                    id,
                    name,
                    size_mb: o.get("size_mb").and_then(Value::as_f64),
                }
            }
            _ => Self::default(),
        })
    }
}

/// A downloadable variant of a catalog model.
#[derive(Debug, Clone, PartialEq, Deserialize, Serialize, Default)]
pub struct Variant {
    /// Variant id.
    #[serde(default)]
    pub id: String,
    /// Size in MB.
    #[serde(default)]
    pub size_mb: Option<f64>,
    /// File format, for example `gguf`.
    #[serde(default)]
    pub format: Option<String>,
    /// Whether this variant is installed.
    #[serde(default)]
    pub downloaded: bool,
    /// Fit for the hardware.
    #[serde(default)]
    pub compatibility: Compatibility,
}

/// A catalog entry (`models[]` of `GET /api/models`).
#[derive(Debug, Clone, PartialEq, Deserialize, Serialize, Default)]
pub struct CatalogModel {
    /// Manifest id.
    #[serde(default)]
    pub id: String,
    /// Display name.
    #[serde(default)]
    pub name: String,
    /// Version.
    #[serde(default)]
    pub version: Option<String>,
    /// Description.
    #[serde(default)]
    pub description: Option<String>,
    /// Best compatibility across variants.
    #[serde(default)]
    pub compatibility: Compatibility,
    /// Capability tags.
    #[serde(default, deserialize_with = "lenient_vec")]
    pub capabilities: Vec<String>,
    /// Variants.
    #[serde(default, deserialize_with = "lenient_vec")]
    pub variants: Vec<Variant>,
    /// Any variant is installed (the server's install flag).
    #[serde(default)]
    pub has_downloaded_variant: bool,
}

impl CatalogModel {
    /// The variant the web UI downloads: the smallest with a positive size,
    /// otherwise the first.
    pub fn default_variant(&self) -> Option<&Variant> {
        self.variants
            .iter()
            .filter(|v| v.size_mb.is_some_and(|s| s > 0.0))
            .min_by(|a, b| {
                a.size_mb
                    .partial_cmp(&b.size_mb)
                    .unwrap_or(std::cmp::Ordering::Equal)
            })
            .or_else(|| self.variants.first())
    }
}

/// A model file on the controller's disk.
#[derive(Debug, Clone, PartialEq, Deserialize, Serialize, Default)]
pub struct DownloadedFile {
    /// File name.
    #[serde(default)]
    pub filename: String,
    /// Size in MB.
    #[serde(default)]
    pub size_mb: f64,
    /// Format.
    #[serde(default)]
    pub format: Option<String>,
    /// Absolute path on the controller.
    #[serde(default)]
    pub path: Option<String>,
    /// Matching catalog id, when known.
    #[serde(default)]
    pub model_id: Option<String>,
    /// Path relative to the models root.
    #[serde(default)]
    pub relative_path: Option<String>,
}

/// `GET /api/models`.
#[derive(Debug, Clone, PartialEq, Deserialize, Serialize, Default)]
pub struct ModelCatalog {
    /// Catalog entries.
    #[serde(default, deserialize_with = "lenient_vec")]
    pub models: Vec<CatalogModel>,
    /// Files on disk.
    #[serde(default, deserialize_with = "lenient_vec")]
    pub downloaded_files: Vec<DownloadedFile>,
    /// Hardware profile the compatibility was computed for.
    #[serde(default)]
    pub hardware_profile_id: Option<String>,
}

/// A backend reported by a worker.
#[derive(Debug, Clone, PartialEq, Deserialize, Serialize, Default)]
pub struct WorkerBackend {
    /// Backend name (`type:port` on the controller's records).
    #[serde(default)]
    pub name: Option<String>,
    /// Backend type.
    #[serde(default, rename = "type")]
    pub kind: Option<String>,
    /// Models the backend can serve.
    #[serde(default, deserialize_with = "lenient_vec")]
    pub models: Vec<ModelRef>,
    /// Models currently resident in memory.
    #[serde(default, deserialize_with = "lenient_vec")]
    pub loaded_models: Vec<ModelRef>,
}

string_enum! {
    /// Worker state as the server reports it.
    pub enum WorkerState {
        /// Reachable.
        Online => "online",
        /// Not reachable.
        Offline => "offline",
        /// Busy.
        Busy => "busy",
    }
}

/// Worker liveness derived from heartbeat age (Activity app rules).
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Liveness {
    /// Heartbeat under 60 s old.
    Online,
    /// Under 300 s old.
    Stale,
    /// 300 s or older.
    Offline,
    /// No heartbeat recorded.
    Unknown,
}

/// A cluster worker (`GET /api/cluster/workers`). Non-admin sessions get a
/// reduced record (name, status, tier, kind) and the other fields stay empty.
#[derive(Debug, Clone, PartialEq, Deserialize, Serialize, Default)]
pub struct Worker {
    /// Worker name.
    #[serde(default)]
    pub name: String,
    /// Worker base URL.
    #[serde(default)]
    pub url: Option<String>,
    /// Server-side state.
    #[serde(default)]
    pub status: WorkerState,
    /// `worker` or `device`.
    #[serde(default)]
    pub kind: Option<String>,
    /// Hardware tier id.
    #[serde(default)]
    pub tier_id: Option<String>,
    /// `linux`, `windows`, `macos`.
    #[serde(default)]
    pub platform: Option<String>,
    /// Last heartbeat, epoch seconds.
    #[serde(default)]
    pub last_heartbeat: Option<f64>,
    /// Backends.
    #[serde(default, deserialize_with = "lenient_vec")]
    pub backends: Vec<WorkerBackend>,
    /// Flat list of loaded models (older workers).
    #[serde(default, deserialize_with = "lenient_vec")]
    pub models: Vec<ModelRef>,
    /// What the worker can do now.
    #[serde(default, deserialize_with = "lenient_vec")]
    pub capabilities: Vec<String>,
    /// What it could do after loading more.
    #[serde(default, deserialize_with = "lenient_vec")]
    pub potential_capabilities: Vec<String>,
}

impl Worker {
    /// Liveness from `last_heartbeat` against `now` (epoch seconds):
    /// under 60 s online, under 300 s stale, otherwise offline; unknown when
    /// there is no heartbeat.
    pub fn liveness(&self, now: f64) -> Liveness {
        match self.last_heartbeat {
            Some(hb) if hb > 0.0 => {
                let age = now - hb;
                if age < 60.0 {
                    Liveness::Online
                } else if age < 300.0 {
                    Liveness::Stale
                } else {
                    Liveness::Offline
                }
            }
            _ => Liveness::Unknown,
        }
    }

    /// A host label: the worker name, or the hostname of its URL when the
    /// name is empty, `localhost` or `127.0.0.1`.
    pub fn host_label(&self) -> String {
        let generic = matches!(self.name.as_str(), "" | "localhost" | "127.0.0.1");
        if generic {
            if let Some(h) = self
                .url
                .as_deref()
                .and_then(|u| url::Url::parse(u).ok())
                .and_then(|u| u.host_str().map(str::to_string))
            {
                return h;
            }
        }
        self.name.clone()
    }
}

/// A configured provider (`GET /api/providers`). The `api_key` field the
/// server returns to admins is deliberately not modelled.
#[derive(Debug, Clone, PartialEq, Deserialize, Serialize, Default)]
pub struct Provider {
    /// Provider name.
    #[serde(default)]
    pub name: String,
    /// Provider type, for example `openai` or `anthropic`.
    #[serde(default, rename = "type")]
    pub kind: String,
    /// Default model.
    #[serde(default)]
    pub model: Option<String>,
    /// Models, from a live probe or the configured seed.
    #[serde(default, deserialize_with = "lenient_vec")]
    pub models: Vec<ModelRef>,
    /// `local` or `worker`.
    #[serde(default)]
    pub source: Option<String>,
    /// `local`, `network` or `cloud`.
    #[serde(default)]
    pub category: Option<String>,
    /// Probe status, for example `ok`.
    #[serde(default)]
    pub status: Option<String>,
    /// Backend URL.
    #[serde(default)]
    pub url: Option<String>,
    /// Enabled flag (default true).
    #[serde(default = "yes")]
    pub enabled: bool,
}

fn yes() -> bool {
    true
}

/// `POST /api/models/download` reply.
#[derive(Debug, Clone, PartialEq, Deserialize, Serialize, Default)]
pub struct DownloadStart {
    /// `started`.
    #[serde(default)]
    pub status: String,
    /// Id to poll; the controller builds it as `<app_id>-<variant_id>`.
    #[serde(default)]
    pub download_id: String,
    /// Echo of the request.
    #[serde(default)]
    pub app_id: Option<String>,
    /// Echo of the request.
    #[serde(default)]
    pub variant_id: Option<String>,
}

/// Progress of one download.
#[derive(Debug, Clone, PartialEq, Deserialize, Serialize, Default)]
pub struct DownloadProgress {
    /// Download id.
    #[serde(default)]
    pub id: String,
    /// Source URL.
    #[serde(default)]
    pub url: Option<String>,
    /// Destination path on the controller.
    #[serde(default)]
    pub dest: Option<String>,
    /// Expected size; 0 until known.
    #[serde(default)]
    pub total_bytes: u64,
    /// Bytes so far.
    #[serde(default)]
    pub downloaded_bytes: u64,
    /// 0 to 100, one decimal.
    #[serde(default)]
    pub percent: f64,
    /// State.
    #[serde(default)]
    pub status: DownloadStatus,
    /// Failure text when `status` is `error`.
    #[serde(default)]
    pub error: Option<String>,
    /// Start time, epoch seconds.
    #[serde(default)]
    pub started_at: Option<f64>,
    /// Finish time, epoch seconds.
    #[serde(default)]
    pub completed_at: Option<f64>,
}

/// `DELETE /api/models/{id}` reply.
#[derive(Debug, Clone, PartialEq, Deserialize, Serialize, Default)]
pub struct DeleteResult {
    /// `deleted`.
    #[serde(default)]
    pub status: String,
    /// The model.
    #[serde(default)]
    pub model_id: String,
    /// File names removed.
    #[serde(default)]
    pub deleted_files: Vec<String>,
}

/// One update from a [`DownloadWatch`].
#[derive(Debug, Clone, PartialEq)]
pub enum DownloadUpdate {
    /// Still running.
    Progress(DownloadProgress),
    /// Finished; the watch ends. Refetch the catalog.
    Complete(DownloadProgress),
    /// The server reported a failure (its text, or "Download failed"); the watch ends.
    Failed(String),
    /// Three polls in a row failed; the watch ends ("Lost contact with the
    /// download; retry to continue").
    LostContact,
}

/// A background poll of one download. Polls are sequential: the next one is
/// scheduled only after the previous one finished. Dropping or
/// [`cancel`](Self::cancel)ling stops the polling.
pub struct DownloadWatch {
    rx: mpsc::Receiver<DownloadUpdate>,
    task: JoinHandle<()>,
}

impl DownloadWatch {
    /// Stop polling. The download itself continues on the controller.
    pub fn cancel(self) {}
}

impl Drop for DownloadWatch {
    fn drop(&mut self) {
        self.task.abort();
    }
}

impl Stream for DownloadWatch {
    type Item = DownloadUpdate;
    fn poll_next(mut self: Pin<&mut Self>, cx: &mut Context<'_>) -> Poll<Option<Self::Item>> {
        self.rx.poll_recv(cx)
    }
}

/// Models calls. Obtain with [`Client::models`].
#[derive(Debug, Clone)]
pub struct Models {
    client: Client,
}

impl Client {
    /// Models calls.
    pub fn models(&self) -> Models {
        Models {
            client: self.clone(),
        }
    }
}

impl Models {
    /// `GET /api/models`: the catalog and the downloaded files.
    pub async fn catalog(&self) -> Result<ModelCatalog> {
        self.client.json(Req::get("/api/models")).await
    }

    /// `GET /api/cluster/workers`. The route returns a bare array; an object
    /// `{"workers": [...]}` is accepted too.
    pub async fn workers(&self) -> Result<Vec<Worker>> {
        workers(&self.client).await
    }

    /// `GET /api/providers`.
    pub async fn providers(&self) -> Result<Vec<Provider>> {
        self.client
            .json(Req::get("/api/providers").sensitive())
            .await
    }

    /// `POST /api/models/download {app_id, variant_id}`. Admin only.
    pub async fn start_download(&self, app_id: &str, variant_id: &str) -> Result<DownloadStart> {
        let body = serde_json::json!({ "app_id": app_id, "variant_id": variant_id });
        self.client
            .json(Req::post("/api/models/download").json_body(&body)?)
            .await
    }

    /// `GET /api/models/downloads/{id}`: progress of one download.
    pub async fn download_progress(&self, download_id: &str) -> Result<DownloadProgress> {
        self.client
            .json(Req::get(format!(
                "/api/models/downloads/{}",
                encode_segment(download_id)
            )))
            .await
    }

    /// `GET /api/models/downloads`: every download the controller knows,
    /// including ones started in an earlier session. The route wraps them as
    /// `{"downloads": [...]}`.
    pub async fn downloads(&self) -> Result<Vec<DownloadProgress>> {
        #[derive(Deserialize)]
        struct R {
            #[serde(default, deserialize_with = "lenient_vec")]
            downloads: Vec<DownloadProgress>,
        }
        Ok(self
            .client
            .json::<R>(Req::get("/api/models/downloads"))
            .await?
            .downloads)
    }

    /// Follow a download: poll `GET /api/models/downloads/{id}` every
    /// `interval` (the web UI uses 1000 ms) in a background task until it
    /// completes, fails, or three consecutive polls fail. Must be called
    /// inside a Tokio runtime.
    pub fn watch_download(&self, download_id: &str, interval: Duration) -> DownloadWatch {
        let (tx, rx) = mpsc::channel(16);
        let models = self.clone();
        let id = download_id.to_string();
        let task = tokio::spawn(async move {
            let mut failures = 0u32;
            loop {
                match models.download_progress(&id).await {
                    Ok(p) => {
                        failures = 0;
                        let (update, last) = match p.status {
                            DownloadStatus::Complete => {
                                let mut done = p.clone();
                                done.percent = 100.0;
                                (DownloadUpdate::Complete(done), true)
                            }
                            DownloadStatus::Error => {
                                let text = p
                                    .error
                                    .clone()
                                    .filter(|e| !e.is_empty())
                                    .unwrap_or_else(|| "Download failed".into());
                                (DownloadUpdate::Failed(text), true)
                            }
                            _ => (DownloadUpdate::Progress(p), false),
                        };
                        if tx.send(update).await.is_err() || last {
                            return;
                        }
                    }
                    Err(_) => {
                        failures += 1;
                        if failures >= 3 {
                            let _ = tx.send(DownloadUpdate::LostContact).await;
                            return;
                        }
                    }
                }
                tokio::time::sleep(interval).await;
            }
        });
        DownloadWatch { rx, task }
    }

    /// `DELETE /api/models/{id}`. Admin only. 404 when the id is not a catalog model.
    pub async fn delete(&self, model_id: &str) -> Result<DeleteResult> {
        self.client
            .json(Req::delete(format!(
                "/api/models/{}",
                encode_segment(model_id)
            )))
            .await
    }
}

pub(crate) async fn workers(client: &Client) -> Result<Vec<Worker>> {
    #[derive(Deserialize)]
    struct Wrapped {
        #[serde(default, deserialize_with = "lenient_vec")]
        workers: Vec<Worker>,
    }
    let resp = client.send(&Req::get("/api/cluster/workers")).await?;
    let bytes = resp.bytes().await.map_err(crate::error::Error::Transport)?;
    // Decide by the first byte rather than with an untagged enum, so a decode
    // error keeps its JSON path.
    if bytes.iter().find(|b| !b.is_ascii_whitespace()) == Some(&b'[') {
        crate::client::decode::<Vec<Worker>>(&bytes)
    } else {
        Ok(crate::client::decode::<Wrapped>(&bytes)?.workers)
    }
}
