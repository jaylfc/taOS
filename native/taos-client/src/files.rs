//! Files: locations, listing, download, upload, folders, delete and restore.
//!
//! Sources: `tinyagentos/routes/{user_workspace,agent_workspace,project_files,
//! shared_folders,recycle}.py`. The set of operations differs per location
//! kind; [`Location::supports`] says which exist and the methods return
//! [`Error::Unsupported`] without sending anything for the rest.

use std::path::PathBuf;
use std::pin::Pin;
use std::sync::Arc;
use std::task::{Context, Poll};

use bytes::Bytes;
use futures_util::{future::join3, Stream, StreamExt};
use reqwest::multipart::{Form, Part};
use reqwest::RequestBuilder;
use serde::{Deserialize, Serialize};
use tokio_util::io::ReaderStream;

use crate::client::{Client, Req};
use crate::error::{Error, Result};
use crate::events::{json_frame, EventStream, SseOptions};
use crate::util::{encode_path, encode_segment, lenient_vec, Timestamp};

/// Where files live. A web "Recycle Bin" location is not a variant: the bins
/// are reached through the `trash` and `recycle` methods.
#[derive(Debug, Clone, PartialEq, Eq, Hash)]
pub enum Location {
    /// The signed-in user's workspace (`workspace`).
    Workspace,
    /// An agent's workspace (`agent:<name>`).
    Agent(String),
    /// A project's files folder (`project:<slug>`).
    Project(String),
    /// A shared folder, by name.
    SharedFolder(String),
}

/// An operation a location may or may not support.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Op {
    /// List a directory.
    List,
    /// Live directory updates.
    Watch,
    /// Read or download a file.
    Read,
    /// Upload a file.
    Upload,
    /// Create a directory.
    Mkdir,
    /// Move to the trash.
    Delete,
    /// Rename or move.
    Rename,
    /// Copy.
    Copy,
    /// File count and size.
    Stats,
    /// List, restore and purge trashed items.
    Trash,
}

impl Location {
    /// Parse a web location id: `workspace`, `agent:<name>`, `project:<slug>`,
    /// anything else is a shared folder name.
    pub fn parse(id: &str) -> Self {
        if id == "workspace" {
            Self::Workspace
        } else if let Some(a) = id.strip_prefix("agent:") {
            Self::Agent(a.to_string())
        } else if let Some(p) = id.strip_prefix("project:") {
            Self::Project(p.to_string())
        } else {
            Self::SharedFolder(id.to_string())
        }
    }

    /// The web location id.
    pub fn id(&self) -> String {
        match self {
            Self::Workspace => "workspace".into(),
            Self::Agent(a) => format!("agent:{a}"),
            Self::Project(p) => format!("project:{p}"),
            Self::SharedFolder(n) => n.clone(),
        }
    }

    /// Workspace, agent and project locations are writable in the UI.
    pub fn is_writable(&self) -> bool {
        !matches!(self, Self::SharedFolder(_))
    }

    /// Whether the controller has a route for `op` on this location kind.
    ///
    /// Shared folders can only be listed and uploaded to: the controller has no
    /// file read route for them (the web UI's call 404s), no mkdir, delete or
    /// watch. Rename and copy exist for the workspace only.
    pub fn supports(&self, op: Op) -> bool {
        match self {
            Self::Workspace => true,
            Self::Agent(_) | Self::Project(_) => !matches!(op, Op::Rename | Op::Copy),
            Self::SharedFolder(_) => matches!(op, Op::List | Op::Upload),
        }
    }

    fn require(&self, op: Op, what: &'static str) -> Result<()> {
        if self.supports(op) {
            Ok(())
        } else {
            Err(Error::Unsupported(what))
        }
    }

    /// Route prefix for workspace, agent and project locations.
    fn root(&self) -> Result<String> {
        match self {
            Self::Workspace => Ok("/api/workspace".into()),
            Self::Agent(a) => Ok(format!("/api/agents/{}/workspace", encode_segment(a))),
            Self::Project(p) => Ok(format!("/api/projects/{}", encode_segment(p))),
            Self::SharedFolder(_) => Err(Error::Unsupported(
                "shared folders only support list and upload",
            )),
        }
    }
}

/// One directory entry. Shared-folder listings carry only `name`, `size_mb`
/// and `modified`; `path` then defaults to the name and `is_dir` to false.
#[derive(Debug, Clone, PartialEq, Deserialize, Serialize, Default)]
#[serde(from = "RawEntry")]
pub struct FileEntry {
    /// File or directory name.
    pub name: String,
    /// Path relative to the location root.
    pub path: String,
    /// True for directories.
    pub is_dir: bool,
    /// Size in bytes (0 for directories).
    pub size: u64,
    /// Modification time, epoch seconds.
    pub modified: Option<f64>,
}

#[derive(Deserialize)]
struct RawEntry {
    #[serde(default)]
    name: String,
    #[serde(default)]
    path: Option<String>,
    #[serde(default)]
    is_dir: Option<bool>,
    #[serde(default)]
    size: Option<f64>,
    #[serde(default)]
    size_mb: Option<f64>,
    #[serde(default)]
    modified: Option<f64>,
}

impl From<RawEntry> for FileEntry {
    fn from(r: RawEntry) -> Self {
        let size = r
            .size
            .or_else(|| r.size_mb.map(|mb| mb * 1024.0 * 1024.0))
            .filter(|s| s.is_finite() && *s >= 0.0)
            .map(|s| s.round() as u64)
            .unwrap_or(0);
        Self {
            path: r.path.unwrap_or_else(|| r.name.clone()),
            name: r.name,
            is_dir: r.is_dir.unwrap_or(false),
            size,
            modified: r.modified,
        }
    }
}

/// A shared folder (`GET /api/shared-folders`).
#[derive(Debug, Clone, PartialEq, Deserialize, Default)]
pub struct SharedFolder {
    /// Folder id.
    #[serde(default)]
    pub id: i64,
    /// Folder name, which is also its location id.
    #[serde(default)]
    pub name: String,
    /// Description.
    #[serde(default)]
    pub description: Option<String>,
    /// `owner_type` column.
    #[serde(default)]
    pub owner_type: Option<String>,
    /// `owner_name` column.
    #[serde(default)]
    pub owner_name: Option<String>,
}

/// An agent entry for the locations sidebar (`GET /api/agents`).
#[derive(Debug, Clone, PartialEq, Deserialize, Default)]
pub struct AgentInfo {
    /// Agent name; its workspace location is `agent:<name>`.
    #[serde(default)]
    pub name: String,
    /// Display name.
    #[serde(default)]
    pub display_name: Option<String>,
    /// Emoji avatar.
    #[serde(default)]
    pub emoji: Option<String>,
    /// Agent framework.
    #[serde(default)]
    pub framework: Option<String>,
}

/// File count and total size (`.../stats`).
#[derive(Debug, Clone, Copy, PartialEq, Eq, Deserialize, Default)]
pub struct Stats {
    /// Number of files.
    #[serde(default)]
    pub total_files: u64,
    /// Total bytes.
    #[serde(default)]
    pub total_size: u64,
}

/// What the sidebar of the Files app needs.
#[derive(Debug, Clone, PartialEq, Default)]
pub struct Locations {
    /// The workspace stats (file count and size), when the call worked.
    pub workspace: Option<Stats>,
    /// Shared folders.
    pub shared_folders: Vec<SharedFolder>,
    /// Agents whose workspaces can be browsed.
    pub agents: Vec<AgentInfo>,
    /// Parts that failed with something other than "not signed in"; the rest is still usable.
    pub warnings: Vec<String>,
}

/// Result of a mutating file call.
#[derive(Debug, Clone, PartialEq, Deserialize, Default)]
pub struct FileOpResult {
    /// Resulting (or affected) relative path.
    #[serde(default)]
    pub path: Option<String>,
    /// `created`, `renamed`, `copied`, `deleted`, `restored`, ...
    #[serde(default)]
    pub status: String,
}

/// Result of an upload.
#[derive(Debug, Clone, PartialEq, Deserialize, Default)]
pub struct Uploaded {
    /// Stored file name.
    #[serde(default)]
    pub name: String,
    /// Relative path (absent for shared folders).
    #[serde(default)]
    pub path: Option<String>,
    /// Stored size in bytes.
    #[serde(default)]
    pub size: u64,
    /// `uploaded`.
    #[serde(default)]
    pub status: String,
    /// Project uploads also report the stored path here.
    #[serde(default)]
    pub stored_as: Option<String>,
}

/// An item in a workspace, agent or project trash.
#[derive(Debug, Clone, PartialEq, Deserialize, Default)]
pub struct TrashItem {
    /// Item id.
    #[serde(default)]
    pub id: String,
    /// File name.
    #[serde(default)]
    pub name: String,
    /// Path it will be restored to.
    #[serde(default)]
    pub original_path: String,
    /// Directory flag.
    #[serde(default)]
    pub is_dir: bool,
    /// Size in bytes for files.
    #[serde(default)]
    pub size_bytes: Option<u64>,
    /// Deletion time (ISO 8601 string).
    #[serde(default)]
    pub deleted_at: Option<Timestamp>,
}

/// An item in an agent container's recycle bin (`/api/recycle`).
#[derive(Debug, Clone, PartialEq, Deserialize, Default)]
pub struct ContainerRecycleItem {
    /// Item id (base64url of the original path).
    #[serde(default)]
    pub id: String,
    /// Owning agent (present in the aggregated listing).
    #[serde(default)]
    pub agent_name: Option<String>,
    /// Absolute path inside the container.
    #[serde(default)]
    pub original_path: String,
    /// Deletion time (ISO 8601 string).
    #[serde(default)]
    pub deleted_at: Option<Timestamp>,
    /// Size when known.
    #[serde(default)]
    pub size_bytes: Option<u64>,
}

/// One agent's container recycle bin with its reachability.
#[derive(Debug, Clone, PartialEq, Deserialize, Default)]
pub struct AgentRecycle {
    /// Agent name.
    #[serde(default)]
    pub agent_name: String,
    /// Items.
    #[serde(default, deserialize_with = "lenient_vec")]
    pub items: Vec<ContainerRecycleItem>,
    /// `ok` or `container_offline`.
    #[serde(default)]
    pub status: String,
}

impl AgentRecycle {
    /// True when the agent's container could not be reached.
    pub fn container_offline(&self) -> bool {
        self.status == "container_offline"
    }
}

/// Upload progress: bytes handed to the HTTP stack so far and the total. This
/// counts what has been written to the connection, not what the server has
/// acknowledged; the upload is complete when the call returns.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct UploadProgress {
    /// Bytes sent so far.
    pub sent: u64,
    /// Total bytes.
    pub total: u64,
}

/// What to upload.
#[derive(Debug, Clone)]
pub enum UploadSource {
    /// In-memory data.
    Bytes {
        /// Name to store it under.
        file_name: String,
        /// Content.
        data: Bytes,
    },
    /// A file on disk, streamed from disk; its base name is used.
    File(PathBuf),
}

/// A file being downloaded: headers are known, the body is a byte stream.
///
/// The stream is not buffered: chunks arrive as the server sends them.
pub struct Download {
    /// `Content-Length`, when the server sent it.
    pub content_length: Option<u64>,
    /// `Content-Type`.
    pub content_type: Option<String>,
    /// File name from `Content-Disposition`.
    pub file_name: Option<String>,
    stream: Pin<Box<dyn Stream<Item = Result<Bytes>> + Send>>,
}

impl std::fmt::Debug for Download {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        f.debug_struct("Download")
            .field("content_length", &self.content_length)
            .field("content_type", &self.content_type)
            .field("file_name", &self.file_name)
            .finish_non_exhaustive()
    }
}

impl Stream for Download {
    type Item = Result<Bytes>;
    fn poll_next(mut self: Pin<&mut Self>, cx: &mut Context<'_>) -> Poll<Option<Self::Item>> {
        self.stream.as_mut().poll_next(cx)
    }
}

impl Download {
    /// Collect the whole body into memory.
    pub async fn to_bytes(mut self) -> Result<Bytes> {
        let mut out = Vec::new();
        while let Some(chunk) = self.next().await {
            out.extend_from_slice(&chunk?);
        }
        Ok(out.into())
    }

    /// Stream the body into a file, returning the number of bytes written.
    pub async fn save_to(mut self, path: impl AsRef<std::path::Path>) -> Result<u64> {
        use tokio::io::AsyncWriteExt;
        let mut file = tokio::fs::File::create(path).await?;
        let mut total = 0u64;
        while let Some(chunk) = self.next().await {
            let chunk = chunk?;
            file.write_all(&chunk).await?;
            total += chunk.len() as u64;
        }
        file.flush().await?;
        Ok(total)
    }
}

/// Files calls. Obtain with [`Client::files`].
#[derive(Debug, Clone)]
pub struct Files {
    client: Client,
}

impl Client {
    /// Files calls.
    pub fn files(&self) -> Files {
        Files {
            client: self.clone(),
        }
    }
}

fn file_path(root: &str, rel: &str) -> Result<String> {
    let enc = encode_path(rel)
        .ok_or_else(|| Error::InvalidInput(format!("path `{rel}` contains `.` or `..`")))?;
    Ok(format!("{root}/files/{enc}"))
}

fn disposition_name(header: &str) -> Option<String> {
    let rest = header.split("filename=").nth(1)?;
    let name = rest.split(';').next()?.trim().trim_matches('"');
    (!name.is_empty()).then(|| name.to_string())
}

impl Files {
    /// What the Files sidebar shows: `GET /api/workspace/stats`,
    /// `GET /api/shared-folders` and `GET /api/agents`, fetched concurrently.
    /// A 401 on any of them is returned as an error; other failures leave that
    /// part empty and are listed in [`Locations::warnings`].
    pub async fn locations(&self) -> Result<Locations> {
        let (stats, shared, agents) = join3(
            self.stats(&Location::Workspace),
            self.shared_folders(),
            self.agents(),
        )
        .await;
        let mut out = Locations::default();
        let mut warnings = Vec::new();
        match stats {
            Ok(s) => out.workspace = Some(s),
            Err(e) => {
                if e.is_unauthenticated() {
                    return Err(e);
                }
                warnings.push(format!("workspace stats: {e}"));
            }
        }
        match shared {
            Ok(s) => out.shared_folders = s,
            Err(e) => {
                if e.is_unauthenticated() {
                    return Err(e);
                }
                warnings.push(format!("shared folders: {e}"));
            }
        }
        match agents {
            Ok(a) => out.agents = a,
            Err(e) => {
                if e.is_unauthenticated() {
                    return Err(e);
                }
                warnings.push(format!("agents: {e}"));
            }
        }
        out.warnings = warnings;
        Ok(out)
    }

    /// `GET /api/shared-folders`.
    pub async fn shared_folders(&self) -> Result<Vec<SharedFolder>> {
        self.client.json(Req::get("/api/shared-folders")).await
    }

    /// `GET /api/agents`: the agents whose workspaces can be browsed.
    pub async fn agents(&self) -> Result<Vec<AgentInfo>> {
        self.client.json(Req::get("/api/agents")).await
    }

    /// `GET .../stats`: file count and size. Shared folders have no stats.
    pub async fn stats(&self, loc: &Location) -> Result<Stats> {
        loc.require(Op::Stats, "shared folders have no stats route")?;
        self.client
            .json(Req::get(format!("{}/stats", loc.root()?)))
            .await
    }

    /// List a directory.
    ///
    /// Routes: `GET /api/workspace/files`, `GET /api/agents/{a}/workspace/files`,
    /// `GET /api/projects/{s}/files`, `GET /api/shared-folders/{n}/files`
    /// (shared folders have no `path` parameter, so a non-empty `dir` is refused).
    pub async fn list(&self, loc: &Location, dir: &str) -> Result<Vec<FileEntry>> {
        let req = match loc {
            Location::SharedFolder(n) => {
                if !dir.is_empty() {
                    return Err(Error::Unsupported("shared folders have no sub-directories"));
                }
                Req::get(format!("/api/shared-folders/{}/files", encode_segment(n)))
            }
            _ => {
                let r = Req::get(format!("{}/files", loc.root()?));
                if dir.is_empty() {
                    r
                } else {
                    r.query("path", dir)
                }
            }
        };
        self.client.json(req).await
    }

    /// Live directory updates: `GET .../files/watch?path=`. Every item is the
    /// complete listing, which replaces the previous one. An `event: error`
    /// frame (for example the directory vanished) ends the stream with
    /// [`Error::Stream`]. Not available for shared folders.
    pub fn watch(&self, loc: &Location, dir: &str) -> Result<EventStream<Vec<FileEntry>>> {
        loc.require(Op::Watch, "shared folders cannot be watched")?;
        let path = format!("{}/files/watch", loc.root()?);
        let query = if dir.is_empty() {
            Vec::new()
        } else {
            vec![("path".to_string(), dir.to_string())]
        };
        let opts = SseOptions {
            resume: false,
            ..SseOptions::default()
        };
        Ok(EventStream::new(self.client.sse(path, query, opts), |ev| {
            json_frame(&ev)
        }))
    }

    /// Download a file as a byte stream: `GET .../files/{path}`.
    ///
    /// Not available for shared folders (the controller has no such route).
    pub async fn download(&self, loc: &Location, rel: &str) -> Result<Download> {
        loc.require(Op::Read, "shared folders have no file read route")?;
        let path = file_path(&loc.root()?, rel)?;
        let resp = self.client.send(&Req::get(path).streaming("*/*")).await?;
        let header = |name: reqwest::header::HeaderName| {
            resp.headers()
                .get(name)
                .and_then(|v| v.to_str().ok())
                .map(str::to_string)
        };
        let content_type = header(reqwest::header::CONTENT_TYPE);
        let file_name =
            header(reqwest::header::CONTENT_DISPOSITION).and_then(|h| disposition_name(&h));
        let content_length = resp.content_length();
        let stream = resp.bytes_stream().map(|c| c.map_err(Error::Transport));
        Ok(Download {
            content_length,
            content_type,
            file_name,
            stream: Box::pin(stream),
        })
    }

    /// Upload one file as multipart field `file`.
    ///
    /// Routes: `POST /api/workspace/files/upload?path=`,
    /// `POST /api/agents/{a}/workspace/files/upload?path=`,
    /// `POST /api/projects/{s}/files/upload?path=`,
    /// `POST /api/shared-folders/{n}/upload`. `dir` is the target directory
    /// (empty for the root). `on_progress` is called as the body is written;
    /// see [`UploadProgress`].
    pub async fn upload(
        &self,
        loc: &Location,
        dir: &str,
        source: UploadSource,
        on_progress: impl Fn(UploadProgress) + Send + Sync + 'static,
    ) -> Result<Uploaded> {
        let mut req = match loc {
            Location::SharedFolder(n) => {
                if !dir.is_empty() {
                    return Err(Error::Unsupported("shared folders have no sub-directories"));
                }
                Req::post(format!("/api/shared-folders/{}/upload", encode_segment(n)))
            }
            _ => {
                let r = Req::post(format!("{}/files/upload", loc.root()?));
                if dir.is_empty() {
                    r
                } else {
                    r.query("path", dir)
                }
            }
        };
        req.stream = true; // no overall timeout for transfers
        let (file_name, total) = match &source {
            UploadSource::Bytes { file_name, data } => (file_name.clone(), data.len() as u64),
            UploadSource::File(p) => {
                let name = p
                    .file_name()
                    .and_then(|n| n.to_str())
                    .ok_or_else(|| {
                        Error::InvalidInput(format!("`{}` has no file name", p.display()))
                    })?
                    .to_string();
                (name, tokio::fs::metadata(p).await?.len())
            }
        };
        let progress: Arc<dyn Fn(UploadProgress) + Send + Sync> = Arc::new(on_progress);
        let attach = move |rb: RequestBuilder| {
            // Built afresh on each attempt: a CSRF replay needs a new body.
            let chunks: Pin<Box<dyn Stream<Item = std::io::Result<Bytes>> + Send + Sync>> =
                match &source {
                    UploadSource::Bytes { data, .. } => {
                        let parts: Vec<std::io::Result<Bytes>> = data
                            .chunks(64 * 1024)
                            .map(|c| Ok(Bytes::copy_from_slice(c)))
                            .collect();
                        Box::pin(futures_util::stream::iter(parts))
                    }
                    UploadSource::File(p) => match std::fs::File::open(p) {
                        Ok(f) => Box::pin(ReaderStream::with_capacity(
                            tokio::fs::File::from_std(f),
                            64 * 1024,
                        )),
                        Err(e) => Box::pin(futures_util::stream::iter(vec![Err(e)])),
                    },
                };
            let cb = progress.clone();
            let mut sent = 0u64;
            let counted = chunks.map(move |chunk| {
                if let Ok(c) = &chunk {
                    sent += c.len() as u64;
                    cb(UploadProgress { sent, total });
                }
                chunk
            });
            let part = Part::stream_with_length(reqwest::Body::wrap_stream(counted), total)
                .file_name(file_name.clone())
                .mime_str("application/octet-stream")
                .unwrap_or_else(|_| Part::text(""));
            rb.multipart(Form::new().part("file", part))
        };
        let resp = self.client.send_with(&req, &attach).await?;
        let bytes = resp.bytes().await.map_err(Error::Transport)?;
        crate::client::decode(&bytes)
    }

    /// Create a directory: `POST .../mkdir {path}`. Not available for shared folders.
    pub async fn mkdir(&self, loc: &Location, rel: &str) -> Result<FileOpResult> {
        loc.require(Op::Mkdir, "shared folders have no mkdir route")?;
        let body = serde_json::json!({ "path": rel });
        self.client
            .json(Req::post(format!("{}/mkdir", loc.root()?)).json_body(&body)?)
            .await
    }

    /// Rename or move within a location: `POST /api/workspace/rename {src, dst}`.
    /// Workspace only; 409 when the target exists.
    pub async fn rename(&self, loc: &Location, src: &str, dst: &str) -> Result<FileOpResult> {
        loc.require(Op::Rename, "rename exists for the workspace only")?;
        let body = serde_json::json!({ "src": src, "dst": dst });
        self.client
            .json(Req::post(format!("{}/rename", loc.root()?)).json_body(&body)?)
            .await
    }

    /// Copy within a location: `POST /api/workspace/copy {src, dst}`.
    /// Workspace only; 409 when the target exists.
    pub async fn copy(&self, loc: &Location, src: &str, dst: &str) -> Result<FileOpResult> {
        loc.require(Op::Copy, "copy exists for the workspace only")?;
        let body = serde_json::json!({ "src": src, "dst": dst });
        self.client
            .json(Req::post(format!("{}/copy", loc.root()?)).json_body(&body)?)
            .await
    }

    /// Move a file or directory to the recycle bin: `DELETE .../files/{path}`.
    /// Nothing is permanently removed; see [`Files::restore`].
    pub async fn delete(&self, loc: &Location, rel: &str) -> Result<FileOpResult> {
        loc.require(Op::Delete, "shared folders have no delete route")?;
        let path = file_path(&loc.root()?, rel)?;
        self.client.json(Req::delete(path)).await
    }

    /// Items in the trash that [`Files::delete`] fills for this location:
    /// `GET .../trash`.
    ///
    /// For agents this is the host-side workspace trash
    /// (`/api/agents/{a}/workspace/trash`), which is where a delete through
    /// this API lands. The container recycle bin the web UI lists is separate;
    /// see [`Files::agent_recycle`].
    pub async fn trash(&self, loc: &Location) -> Result<Vec<TrashItem>> {
        loc.require(Op::Trash, "shared folders have no trash")?;
        #[derive(Deserialize)]
        struct R {
            #[serde(default, deserialize_with = "lenient_vec")]
            items: Vec<TrashItem>,
        }
        Ok(self
            .client
            .json::<R>(Req::get(format!("{}/trash", loc.root()?)))
            .await?
            .items)
    }

    /// Restore a trashed item to its original path: `POST .../trash/{id}/restore`.
    /// 409 when something already exists there.
    pub async fn restore(&self, loc: &Location, item_id: &str) -> Result<FileOpResult> {
        loc.require(Op::Trash, "shared folders have no trash")?;
        let path = format!("{}/trash/{}/restore", loc.root()?, encode_segment(item_id));
        self.client.json(Req::post(path)).await
    }

    /// Permanently delete one trashed item: `DELETE .../trash/{id}`.
    pub async fn purge(&self, loc: &Location, item_id: &str) -> Result<()> {
        loc.require(Op::Trash, "shared folders have no trash")?;
        let path = format!("{}/trash/{}", loc.root()?, encode_segment(item_id));
        self.client.send(&Req::delete(path)).await?;
        Ok(())
    }

    /// Permanently delete everything in a trash: `DELETE .../trash`. Returns the count.
    pub async fn empty_trash(&self, loc: &Location) -> Result<u64> {
        loc.require(Op::Trash, "shared folders have no trash")?;
        #[derive(Deserialize)]
        struct R {
            #[serde(default)]
            count: u64,
        }
        Ok(self
            .client
            .json::<R>(Req::delete(format!("{}/trash", loc.root()?)))
            .await?
            .count)
    }

    /// The aggregated agent container recycle bins: `GET /api/recycle`.
    pub async fn agent_recycle(&self) -> Result<Vec<ContainerRecycleItem>> {
        #[derive(Deserialize)]
        struct R {
            #[serde(default, deserialize_with = "lenient_vec")]
            items: Vec<ContainerRecycleItem>,
        }
        Ok(self.client.json::<R>(Req::get("/api/recycle")).await?.items)
    }

    /// One agent's container recycle bin with its reachability:
    /// `GET /api/agents/{name}/recycle`.
    pub async fn agent_recycle_for(&self, agent: &str) -> Result<AgentRecycle> {
        self.client
            .json(Req::get(format!(
                "/api/agents/{}/recycle",
                encode_segment(agent)
            )))
            .await
    }

    /// Restore a container recycle item: `POST /api/agents/{a}/recycle/restore {id}`.
    /// 409 when the container is offline.
    pub async fn restore_agent_recycle(&self, agent: &str, item_id: &str) -> Result<()> {
        let body = serde_json::json!({ "id": item_id });
        let path = format!("/api/agents/{}/recycle/restore", encode_segment(agent));
        self.client.send(&Req::post(path).json_body(&body)?).await?;
        Ok(())
    }

    /// Permanently purge a container recycle item: `DELETE /api/agents/{a}/recycle/{id}`.
    pub async fn purge_agent_recycle(&self, agent: &str, item_id: &str) -> Result<()> {
        let path = format!(
            "/api/agents/{}/recycle/{}",
            encode_segment(agent),
            encode_segment(item_id)
        );
        self.client.send(&Req::delete(path)).await?;
        Ok(())
    }
}
