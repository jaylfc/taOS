//! The Activity (dashboard) app's data: system activity, loaded models,
//! scheduler, cluster workers, model activity and the AI-stack restart.
//!
//! Sources: `tinyagentos/routes/{activity,scheduler,models,model_activity,
//! system,cluster}.py`. Every numeric field is read leniently (numbers or
//! numeric strings) and every list tolerates `null`.
//!
//! Poll `activity`, `loaded_models`, `scheduler_stats` and `scheduler_tasks`
//! together every 2 s and `workers` every 10 s with
//! [`PollWhileVisible`](crate::events::PollWhileVisible). The model-activity
//! routes accept only a session cookie (a host local token gets 401), which is
//! what this client uses.

use serde::Deserialize;
use serde_json::Value;

use crate::client::{Client, Req};
use crate::error::Result;
use crate::events::{json_frame, EventStream, SseOptions};
use crate::models::Worker;
use crate::util::{lenient_vec, num, opt_num, string_enum};

/// Hardware CPU block.
#[derive(Debug, Clone, PartialEq, Deserialize, Default)]
pub struct HwCpu {
    /// CPU model string.
    #[serde(default)]
    pub model: Option<String>,
    /// Architecture, for example `aarch64`.
    #[serde(default)]
    pub arch: Option<String>,
    /// SoC name when known.
    #[serde(default)]
    pub soc: Option<String>,
    /// Core count.
    #[serde(default, deserialize_with = "opt_num")]
    pub cores: Option<f64>,
}

/// Hardware GPU block.
#[derive(Debug, Clone, PartialEq, Deserialize, Default)]
pub struct HwGpu {
    /// Vendor type, for example `nvidia`.
    #[serde(default, rename = "type")]
    pub kind: Option<String>,
    /// Model.
    #[serde(default)]
    pub model: Option<String>,
    /// VRAM in MB.
    #[serde(default, deserialize_with = "opt_num")]
    pub vram_mb: Option<f64>,
}

/// Hardware NPU block.
#[derive(Debug, Clone, PartialEq, Deserialize, Default)]
pub struct HwNpu {
    /// Type, for example `rknpu`.
    #[serde(default, rename = "type")]
    pub kind: Option<String>,
    /// Device node.
    #[serde(default)]
    pub device: Option<String>,
    /// Peak TOPS.
    #[serde(default, deserialize_with = "opt_num")]
    pub tops: Option<f64>,
    /// Core count.
    #[serde(default, deserialize_with = "opt_num")]
    pub cores: Option<f64>,
}

/// Static hardware description.
#[derive(Debug, Clone, PartialEq, Deserialize, Default)]
pub struct Hardware {
    /// SoC or CPU model, whichever the controller knows.
    #[serde(default)]
    pub board: Option<String>,
    /// CPU.
    #[serde(default)]
    pub cpu: Option<HwCpu>,
    /// GPU.
    #[serde(default)]
    pub gpu: Option<HwGpu>,
    /// NPU.
    #[serde(default)]
    pub npu: Option<HwNpu>,
    /// Installed RAM in MB.
    #[serde(default, deserialize_with = "opt_num")]
    pub ram_mb: Option<f64>,
}

/// One CPU core.
#[derive(Debug, Clone, PartialEq, Deserialize, Default)]
pub struct CpuCore {
    /// Core index.
    #[serde(default, deserialize_with = "num")]
    pub core: f64,
    /// Load, 0 to 100.
    #[serde(default, deserialize_with = "num")]
    pub load_percent: f64,
    /// Current frequency in kHz.
    #[serde(default, deserialize_with = "opt_num")]
    pub freq_khz: Option<f64>,
    /// Governor.
    #[serde(default)]
    pub governor: Option<String>,
}

/// CPU block.
#[derive(Debug, Clone, PartialEq, Deserialize, Default)]
pub struct Cpu {
    /// Per-core load.
    #[serde(default, deserialize_with = "lenient_vec")]
    pub cores: Vec<CpuCore>,
    /// 1, 5 and 15 minute load averages.
    #[serde(default, deserialize_with = "lenient_vec")]
    pub load_avg: Vec<f64>,
    /// Overall load, 0 to 100.
    #[serde(default, deserialize_with = "num")]
    pub overall_percent: f64,
}

/// Memory block.
#[derive(Debug, Clone, PartialEq, Deserialize, Default)]
pub struct Memory {
    /// Total MB.
    #[serde(default, deserialize_with = "num")]
    pub total_mb: f64,
    /// Used MB.
    #[serde(default, deserialize_with = "num")]
    pub used_mb: f64,
    /// Available MB.
    #[serde(default, deserialize_with = "num")]
    pub available_mb: f64,
    /// Used percent.
    #[serde(default, deserialize_with = "num")]
    pub percent: f64,
    /// Swap total MB.
    #[serde(default, deserialize_with = "num")]
    pub swap_total_mb: f64,
    /// Swap used MB.
    #[serde(default, deserialize_with = "num")]
    pub swap_used_mb: f64,
    /// Swap used percent.
    #[serde(default, deserialize_with = "num")]
    pub swap_percent: f64,
}

/// One NPU core.
#[derive(Debug, Clone, PartialEq, Deserialize, Default)]
pub struct NpuCore {
    /// Core index.
    #[serde(default, deserialize_with = "num")]
    pub core: f64,
    /// Load, 0 to 100.
    #[serde(default, deserialize_with = "num")]
    pub load_percent: f64,
}

/// NPU block. `cores` is empty when there is no NPU.
#[derive(Debug, Clone, PartialEq, Deserialize, Default)]
pub struct Npu {
    /// Per-core load.
    #[serde(default, deserialize_with = "lenient_vec")]
    pub cores: Vec<NpuCore>,
    /// Frequency in Hz.
    #[serde(default, deserialize_with = "opt_num")]
    pub freq_hz: Option<f64>,
    /// Type.
    #[serde(default, rename = "type")]
    pub kind: Option<String>,
    /// Peak TOPS.
    #[serde(default, deserialize_with = "opt_num")]
    pub tops: Option<f64>,
}

/// GPU load sample.
#[derive(Debug, Clone, PartialEq, Deserialize, Default)]
pub struct GpuLoad {
    /// Load, 0 to 100.
    #[serde(default, deserialize_with = "opt_num")]
    pub load_percent: Option<f64>,
    /// Frequency in Hz.
    #[serde(default, deserialize_with = "opt_num")]
    pub freq_hz: Option<f64>,
}

/// GPU block.
#[derive(Debug, Clone, PartialEq, Deserialize, Default)]
pub struct Gpu {
    /// Load, when readable.
    #[serde(default)]
    pub load: Option<GpuLoad>,
    /// VRAM used percent.
    #[serde(default, deserialize_with = "opt_num")]
    pub vram_percent: Option<f64>,
    /// VRAM used MB.
    #[serde(default, deserialize_with = "opt_num")]
    pub vram_used_mb: Option<f64>,
    /// VRAM total MB.
    #[serde(default, deserialize_with = "opt_num")]
    pub vram_total_mb: Option<f64>,
    /// GPU type.
    #[serde(default, rename = "type")]
    pub kind: Option<String>,
}

/// A thermal zone.
#[derive(Debug, Clone, PartialEq, Deserialize, Default)]
pub struct Thermal {
    /// Zone name.
    #[serde(default)]
    pub name: String,
    /// Temperature in degrees Celsius.
    #[serde(default, deserialize_with = "num")]
    pub temp_c: f64,
}

/// A ZRAM device.
#[derive(Debug, Clone, PartialEq, Deserialize, Default)]
pub struct Zram {
    /// Device name.
    #[serde(default)]
    pub device: String,
    /// Original (uncompressed) MB.
    #[serde(default, deserialize_with = "num")]
    pub orig_mb: f64,
    /// Compressed MB.
    #[serde(default, deserialize_with = "num")]
    pub compr_mb: f64,
    /// Compression ratio.
    #[serde(default, deserialize_with = "num")]
    pub ratio: f64,
}

/// Disk throughput.
#[derive(Debug, Clone, PartialEq, Deserialize, Default)]
pub struct DiskIo {
    /// Read bytes per second.
    #[serde(default, deserialize_with = "num")]
    pub read_bps: f64,
    /// Write bytes per second.
    #[serde(default, deserialize_with = "num")]
    pub write_bps: f64,
}

/// Disk block.
#[derive(Debug, Clone, PartialEq, Deserialize, Default)]
pub struct Disk {
    /// Throughput.
    #[serde(default)]
    pub io_rate: DiskIo,
    /// Used percent of the root filesystem.
    #[serde(default, deserialize_with = "num")]
    pub usage_percent: f64,
    /// Total GB.
    #[serde(default, deserialize_with = "num")]
    pub total_gb: f64,
    /// Used GB.
    #[serde(default, deserialize_with = "num")]
    pub used_gb: f64,
}

/// A network interface.
#[derive(Debug, Clone, PartialEq, Deserialize, Default)]
pub struct NetIface {
    /// Interface name.
    #[serde(default)]
    pub name: String,
    /// Receive bytes per second.
    #[serde(default, deserialize_with = "num")]
    pub rx_bps: f64,
    /// Transmit bytes per second.
    #[serde(default, deserialize_with = "num")]
    pub tx_bps: f64,
    /// Total bytes received.
    #[serde(default, deserialize_with = "num")]
    pub rx_total: f64,
    /// Total bytes sent.
    #[serde(default, deserialize_with = "num")]
    pub tx_total: f64,
}

/// A top process.
#[derive(Debug, Clone, PartialEq, Deserialize, Default)]
pub struct Process {
    /// Process id.
    #[serde(default, deserialize_with = "num")]
    pub pid: f64,
    /// Name.
    #[serde(default)]
    pub name: String,
    /// User.
    #[serde(default)]
    pub user: String,
    /// Resident memory in MB.
    #[serde(default, deserialize_with = "num")]
    pub rss_mb: f64,
    /// CPU percent.
    #[serde(default, deserialize_with = "num")]
    pub cpu_percent: f64,
}

/// `GET /api/activity`.
#[derive(Debug, Clone, PartialEq, Deserialize, Default)]
pub struct Activity {
    /// Server time, epoch seconds.
    #[serde(default, deserialize_with = "opt_num")]
    pub timestamp: Option<f64>,
    /// Hardware description.
    #[serde(default)]
    pub hardware: Hardware,
    /// CPU.
    #[serde(default)]
    pub cpu: Cpu,
    /// Memory.
    #[serde(default)]
    pub memory: Memory,
    /// NPU.
    #[serde(default)]
    pub npu: Npu,
    /// GPU.
    #[serde(default)]
    pub gpu: Gpu,
    /// Thermal zones.
    #[serde(default, deserialize_with = "lenient_vec")]
    pub thermal: Vec<Thermal>,
    /// ZRAM devices. The server sends `{}` instead of a list on an error path;
    /// that reads as empty.
    #[serde(default, deserialize_with = "lenient_vec")]
    pub zram: Vec<Zram>,
    /// Disk.
    #[serde(default)]
    pub disk: Disk,
    /// Network interfaces (same `{}` error-path behaviour as `zram`).
    #[serde(default, deserialize_with = "lenient_vec")]
    pub network: Vec<NetIface>,
    /// Top processes by memory.
    #[serde(default, deserialize_with = "lenient_vec")]
    pub processes: Vec<Process>,
}

/// A model resident in memory (`GET /api/models/loaded`).
#[derive(Debug, Clone, PartialEq, Deserialize, Default)]
pub struct LoadedModel {
    /// Model name.
    #[serde(default)]
    pub name: String,
    /// Backend name.
    #[serde(default)]
    pub backend: String,
    /// Backend type.
    #[serde(default)]
    pub backend_type: Option<String>,
    /// Inferred purpose, for example `chat` or `embedding`.
    #[serde(default)]
    pub purpose: Option<String>,
    /// Size in MB.
    #[serde(default, deserialize_with = "opt_num")]
    pub size_mb: Option<f64>,
    /// VRAM in MB.
    #[serde(default, deserialize_with = "opt_num")]
    pub vram_mb: Option<f64>,
    /// RAM in MB.
    #[serde(default, deserialize_with = "opt_num")]
    pub ram_mb: Option<f64>,
    /// Host label when the server sets one (the web UI shows `controller` otherwise).
    #[serde(default)]
    pub host: Option<String>,
}

/// A scheduler resource.
#[derive(Debug, Clone, PartialEq, Deserialize, Default)]
pub struct SchedulerResource {
    /// Resource name, for example `npu-rk3588`.
    #[serde(default)]
    pub name: String,
    /// Platform.
    #[serde(default)]
    pub platform: Option<String>,
    /// Runtime.
    #[serde(default)]
    pub runtime: Option<String>,
    /// Runtime version.
    #[serde(default)]
    pub runtime_version: Option<String>,
    /// Concurrency limit.
    #[serde(default, deserialize_with = "num")]
    pub concurrency: f64,
    /// Tasks running now.
    #[serde(default, deserialize_with = "num")]
    pub in_flight: f64,
    /// Tier.
    #[serde(default)]
    pub tier: Option<String>,
    /// Capabilities now.
    #[serde(default, deserialize_with = "lenient_vec")]
    pub capabilities: Vec<String>,
    /// Latent capabilities.
    #[serde(default, deserialize_with = "lenient_vec")]
    pub potential_capabilities: Vec<String>,
}

/// `GET /api/scheduler/stats`. The route answers 503 until the scheduler is
/// initialised, which arrives as [`Error::Server`](crate::Error::Server).
#[derive(Debug, Clone, PartialEq, Deserialize, Default)]
pub struct SchedulerStats {
    /// Tasks submitted.
    #[serde(default, deserialize_with = "num")]
    pub submitted: f64,
    /// Tasks completed.
    #[serde(default, deserialize_with = "num")]
    pub completed: f64,
    /// Tasks failed.
    #[serde(default, deserialize_with = "num")]
    pub errors: f64,
    /// Tasks rejected.
    #[serde(default, deserialize_with = "num")]
    pub rejected: f64,
    /// Tasks active now.
    #[serde(default, deserialize_with = "num")]
    pub active: f64,
    /// Resources.
    #[serde(default, deserialize_with = "lenient_vec")]
    pub resources: Vec<SchedulerResource>,
}

string_enum! {
    /// Scheduler task state.
    pub enum TaskStatus {
        /// Waiting.
        Queued => "queued",
        /// Running.
        Running => "running",
        /// Done.
        Complete => "complete",
        /// Failed.
        Error => "error",
        /// Refused.
        Rejected => "rejected",
        /// Cancelled.
        Cancelled => "cancelled",
    }
}

/// A recent scheduler task.
#[derive(Debug, Clone, PartialEq, Deserialize, Default)]
pub struct SchedulerTask {
    /// Task id.
    #[serde(default)]
    pub task_id: String,
    /// Capability requested.
    #[serde(default)]
    pub capability: String,
    /// Who submitted it.
    #[serde(default)]
    pub submitter: Option<String>,
    /// Resource it ran on.
    #[serde(default)]
    pub resource: Option<String>,
    /// State.
    #[serde(default)]
    pub status: TaskStatus,
    /// Elapsed seconds.
    #[serde(default, deserialize_with = "opt_num")]
    pub elapsed_seconds: Option<f64>,
    /// Start time.
    #[serde(default, deserialize_with = "opt_num")]
    pub started_at: Option<f64>,
    /// Completion time.
    #[serde(default, deserialize_with = "opt_num")]
    pub completed_at: Option<f64>,
    /// Failure text.
    #[serde(default)]
    pub error: Option<String>,
}

/// One model-activity event (a history row or a live frame).
#[derive(Debug, Clone, PartialEq, Deserialize, Default)]
pub struct ModelActivityEvent {
    /// Monotonic sequence number; merge live frames into history by this.
    #[serde(default)]
    pub seq: u64,
    /// Time, epoch seconds.
    #[serde(default, deserialize_with = "num")]
    pub ts: f64,
    /// Event type: `model.load`, `model.unload`, `model.evict`, `model.shrink`,
    /// `model.route`, `request.start` or `request.finish` (unknown values are kept).
    #[serde(default)]
    pub event: String,
    /// Model.
    #[serde(default)]
    pub model: Option<String>,
    /// Worker, `controller` for local.
    #[serde(default)]
    pub worker: Option<String>,
    /// Backend.
    #[serde(default)]
    pub backend: Option<String>,
    /// Duration in milliseconds.
    #[serde(default, deserialize_with = "opt_num")]
    pub duration_ms: Option<f64>,
    /// Prompt tokens.
    #[serde(default, deserialize_with = "opt_num")]
    pub tokens_in: Option<f64>,
    /// Completion tokens.
    #[serde(default, deserialize_with = "opt_num")]
    pub tokens_out: Option<f64>,
    /// Tokens per second.
    #[serde(default, deserialize_with = "opt_num")]
    pub token_rate: Option<f64>,
    /// Why (evictions and shrinks).
    #[serde(default)]
    pub reason: Option<String>,
    /// Extra data.
    #[serde(default)]
    pub detail: Value,
}

/// `GET /api/activity/models`.
#[derive(Debug, Clone, PartialEq, Deserialize, Default)]
pub struct ModelActivityPage {
    /// Events, newest first.
    #[serde(default, deserialize_with = "lenient_vec")]
    pub events: Vec<ModelActivityEvent>,
    /// Number of events returned.
    #[serde(default, deserialize_with = "num")]
    pub count: f64,
    /// Every event type the server knows, for the filter menu.
    #[serde(default, deserialize_with = "lenient_vec")]
    pub event_types: Vec<String>,
}

/// Filters for model activity.
#[derive(Debug, Clone, Default)]
pub struct ModelActivityFilter {
    /// Only this worker.
    pub worker: Option<String>,
    /// Only this model.
    pub model: Option<String>,
    /// Only this event type (an unknown one is a 400).
    pub event: Option<String>,
    /// History: 1 to 500 (default 100). Stream: 0 to 500 replayed events
    /// (default 50); 0 means live only.
    pub limit: Option<u32>,
}

/// `POST /api/system/ai-stack/restart` reply.
#[derive(Debug, Clone, PartialEq, Deserialize, Default)]
pub struct AiStackRestart {
    /// `ok`, `partial`, `failed` or `noop`.
    #[serde(default)]
    pub status: String,
    /// Units restarted.
    #[serde(default)]
    pub restarted: Vec<String>,
    /// Units that failed, with their results.
    #[serde(default, deserialize_with = "lenient_vec")]
    pub failed: Vec<UnitResult>,
    /// Every unit result.
    #[serde(default, deserialize_with = "lenient_vec")]
    pub results: Vec<UnitResult>,
    /// Explanation for `noop`.
    #[serde(default)]
    pub detail: Option<String>,
}

/// Result of restarting one service unit.
#[derive(Debug, Clone, PartialEq, Deserialize, Default)]
pub struct UnitResult {
    /// Unit name.
    #[serde(default)]
    pub unit: String,
    /// Whether it restarted.
    #[serde(default)]
    pub ok: bool,
    /// `system` or `user`.
    #[serde(default)]
    pub scope: Option<String>,
    /// Detail text.
    #[serde(default)]
    pub detail: Option<String>,
}

/// Dashboard calls. Obtain with [`Client::dashboard`].
#[derive(Debug, Clone)]
pub struct Dashboard {
    client: Client,
}

impl Client {
    /// Activity (dashboard) calls.
    pub fn dashboard(&self) -> Dashboard {
        Dashboard {
            client: self.clone(),
        }
    }
}

fn activity_query(mut req: Req, f: &ModelActivityFilter) -> Req {
    req = req
        .opt_query("worker", f.worker.as_deref())
        .opt_query("model", f.model.as_deref())
        .opt_query("event", f.event.as_deref());
    req.opt_query("limit", f.limit)
}

impl Dashboard {
    /// `GET /api/activity`: the system snapshot. Poll every 2 s.
    pub async fn activity(&self) -> Result<Activity> {
        self.client.json(Req::get("/api/activity")).await
    }

    /// `GET /api/models/loaded`: models resident on the controller's backends.
    /// The route wraps them as `{"loaded": [...]}`.
    pub async fn loaded_models(&self) -> Result<Vec<LoadedModel>> {
        #[derive(Deserialize)]
        struct R {
            #[serde(default, deserialize_with = "lenient_vec")]
            loaded: Vec<LoadedModel>,
        }
        Ok(self
            .client
            .json::<R>(Req::get("/api/models/loaded"))
            .await?
            .loaded)
    }

    /// `GET /api/scheduler/stats`.
    pub async fn scheduler_stats(&self) -> Result<SchedulerStats> {
        self.client.json(Req::get("/api/scheduler/stats")).await
    }

    /// `GET /api/scheduler/tasks?limit=`: recent tasks, newest first (the app
    /// shows 8). The route wraps them as `{"tasks": [...]}`.
    pub async fn scheduler_tasks(&self, limit: u32) -> Result<Vec<SchedulerTask>> {
        #[derive(Deserialize)]
        struct R {
            #[serde(default, deserialize_with = "lenient_vec")]
            tasks: Vec<SchedulerTask>,
        }
        Ok(self
            .client
            .json::<R>(Req::get("/api/scheduler/tasks").query("limit", limit))
            .await?
            .tasks)
    }

    /// `GET /api/cluster/workers`. Poll every 10 s.
    pub async fn workers(&self) -> Result<Vec<Worker>> {
        crate::models::workers(&self.client).await
    }

    /// `GET /api/activity/models`: model-activity history, newest first.
    pub async fn model_activity(&self, filter: &ModelActivityFilter) -> Result<ModelActivityPage> {
        self.client
            .json(activity_query(Req::get("/api/activity/models"), filter))
            .await
    }

    /// `GET /api/activity/models/stream`: live model events, one per frame.
    ///
    /// Frames carry `id: <seq>` and the server honours `Last-Event-ID`, so a
    /// reconnect replays what was missed. Merge items into the history list by
    /// [`ModelActivityEvent::seq`]. Items may repeat across a reconnect.
    /// Must be called inside a Tokio runtime.
    pub fn model_activity_stream(
        &self,
        filter: &ModelActivityFilter,
    ) -> EventStream<ModelActivityEvent> {
        let req = activity_query(Req::get("/api/activity/models/stream"), filter);
        EventStream::new(
            self.client.sse(req.path, req.query, SseOptions::default()),
            |ev| json_frame(&ev),
        )
    }

    /// `POST /api/system/ai-stack/restart`. Admin only; interrupts running inference.
    pub async fn restart_ai_stack(&self) -> Result<AiStackRestart> {
        self.client
            .json(Req::post("/api/system/ai-stack/restart"))
            .await
    }
}
