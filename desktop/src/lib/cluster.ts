/**
 * Shared types + helpers for the /api/cluster/workers surface.
 * Used by both the Activity app's Cluster panel and the dedicated
 * Cluster management app.
 */

export interface ClusterHardwareCpu {
  arch?: string;
  model?: string;
  cores?: number;
  soc?: string;
}

export interface ClusterHardwareNpu {
  type?: string;
  device?: string;
  tops?: number;
  cores?: number;
}

export interface ClusterHardwareGpu {
  type?: string;
  model?: string;
  vram_mb?: number;
  vulkan?: boolean;
  cuda?: boolean;
  rocm?: boolean;
  metal?: boolean;
  opencl?: boolean;
}

export interface ClusterHardwareDisk {
  total_gb?: number;
  free_gb?: number;
  type?: string;
}

export interface ClusterHardwareOs {
  distro?: string;
  version?: string;
  kernel?: string;
}

export interface ClusterHardware {
  cpu?: ClusterHardwareCpu;
  ram_mb?: number;
  npu?: ClusterHardwareNpu;
  gpu?: ClusterHardwareGpu;
  disk?: ClusterHardwareDisk;
  os?: ClusterHardwareOs;
  board?: string;
}

export interface ClusterBackendModel {
  name?: string;
  id?: string;
  size_mb?: number;
  [k: string]: unknown;
}

export interface ClusterBackend {
  name?: string;
  type?: string;
  runtime?: string;
  runtime_version?: string;
  capabilities?: string[];
  models?: ClusterBackendModel[];
  [k: string]: unknown;
}

export interface ClusterWorker {
  name: string;
  url: string;
  hardware?: ClusterHardware;
  backends?: ClusterBackend[];
  models?: string[];
  capabilities?: string[];
  status?: string;
  last_heartbeat?: number;
  registered_at?: number;
  load?: number;
  platform?: string;
  /** Catalog hardware tier id, e.g. "x86-cuda-12gb". Present on workers that
   *  have connected to a controller running TAOS v2+. */
  tier_id?: string;
  /** Capabilities the hardware could support if the right models were installed.
   *  Derived from the catalog manifests on the controller — never duplicates
   *  entries that are already in `capabilities`. */
  potential_capabilities?: string[];
  /** KV cache quantization types this worker can serve.  Absent on workers
   *  running old agent code; treat missing as ["fp16"].  A worker running a
   *  TurboQuant-capable backend will list additional entries here.
   *  @deprecated Prefer kv_cache_quant_k_support / kv_cache_quant_v_support
   *  for workers that have upgraded to the split K/V model. */
  kv_cache_quant_support?: string[];
  /** Valid -ctk values this worker can serve (split K cache quant types).
   *  Absent on older workers; fall back to kv_cache_quant_support. */
  kv_cache_quant_k_support?: string[];
  /** Valid -ctv values this worker can serve (split V cache quant types).
   *  Absent on older workers; fall back to kv_cache_quant_support. */
  kv_cache_quant_v_support?: string[];
  /** True when the worker supports the boundary-layer-protect feature that
   *  keeps the first N transformer layers in fp16 regardless of KV quant. */
  kv_cache_quant_boundary_layer_protect?: boolean;
}

export type WorkerStatus = "online" | "stale" | "offline" | "unknown";

/**
 * Compute a client-side status pill:
 *  - online if last_heartbeat is within 60s
 *  - stale if 60s–5min
 *  - offline if >5min
 *  - unknown if missing
 *
 * last_heartbeat from the controller is a unix float (seconds).
 */
export function workerStatus(worker: ClusterWorker, nowSec = Date.now() / 1000): WorkerStatus {
  const hb = worker.last_heartbeat;
  if (!hb || typeof hb !== "number") return "unknown";
  const age = nowSec - hb;
  if (age < 60) return "online";
  if (age < 300) return "stale";
  return "offline";
}

export const STATUS_PILL_CLASS: Record<WorkerStatus, string> = {
  online: "bg-emerald-500/15 text-emerald-300 border-emerald-500/25",
  stale: "bg-amber-500/15 text-amber-300 border-amber-500/25",
  offline: "bg-red-500/15 text-red-300 border-red-500/25",
  unknown: "bg-white/5 text-shell-text-tertiary border-white/10",
};

export const STATUS_LABEL: Record<WorkerStatus, string> = {
  online: "online",
  stale: "stale",
  offline: "offline",
  unknown: "unknown",
};

/** Extract a short IP/host from a url like "http://10.228.114.35". */
export function workerShortIp(worker: ClusterWorker): string {
  try {
    const u = new URL(worker.url);
    return u.host;
  } catch {
    return worker.url || "";
  }
}

/**
 * Compact a verbose NVIDIA model string into "{vram}GB NVIDIA {short}".
 *
 * Examples:
 *   "NVIDIA GeForce RTX 3060 Lite Hash Rate" + 12288 → "12GB NVIDIA 3060"
 *   "NVIDIA GeForce RTX 4090"                + 24576 → "24GB NVIDIA 4090"
 *   "NVIDIA RTX A6000"                       + 49152 → "48GB NVIDIA A6000"
 *   "NVIDIA A100-SXM4-80GB"                  + 81920 → "80GB NVIDIA A100"
 *   unknown card with no VRAM                        → "NVIDIA <name>"
 *
 * Rules:
 *  - drop "GeForce", "RTX", "GTX", "Quadro", "Tesla"
 *  - drop marketing suffixes like "Lite Hash Rate" / "(rev a1)" / "OEM"
 *  - drop the "NVIDIA " prefix from the inner model name (we re-add it
 *    in the canonical position)
 *  - VRAM rounded to whole GB so "11264 MB" displays as "11GB", not "11.0GB"
 */
function formatNvidiaGpu(modelRaw: string, vramMb: number): string {
  if (!modelRaw) return vramMb ? `${Math.round(vramMb / 1024)}GB NVIDIA GPU` : "NVIDIA GPU";

  let m = modelRaw;
  m = m.replace(/\(rev [^)]+\)/gi, "");
  m = m.replace(/Lite Hash Rate/gi, "");
  m = m.replace(/\b(GeForce|RTX|GTX|Quadro|Tesla|OEM|SUPER)\b/gi, " ");
  m = m.replace(/^NVIDIA\s+/i, "");
  m = m.replace(/\s+/g, " ").trim();

  // Trim trailing variant suffixes that aren't part of the canonical name.
  m = m.replace(/^([A-Za-z0-9 ]+?)\s+(?:[A-Z]+\d*-)?\d+GB$/i, "$1");
  m = m.replace(/-(SXM\d?|PCIE)?-?\d+GB.*$/i, "");

  if (!m) m = "GPU";

  if (vramMb && vramMb > 0) {
    return `${Math.round(vramMb / 1024)}GB NVIDIA ${m}`;
  }
  return `NVIDIA ${m}`;
}

/**
 * One-line hardware summary string:
 *   "{cpu_model_short}  ·  {ram_gb} GB RAM  ·  {gpu_or_npu_summary}  ·  {os.distro} {os.version}"
 *
 * RAM gets the explicit "RAM" suffix so it doesn't get confused with VRAM
 * which is now embedded in the GPU summary.
 */
export function workerHardwareSummary(worker: ClusterWorker): string {
  const hw = worker.hardware ?? {};
  const cpuModel = hw.cpu?.model ?? "";
  const cpuShort = (cpuModel.split("@")[0] ?? "").trim() || "Unknown CPU";

  const ramGb = hw.ram_mb ? `${Math.round(hw.ram_mb / 1024)}GB RAM` : "? RAM";

  let accel: string;
  const gpu = hw.gpu;
  const npu = hw.npu;
  if (gpu && gpu.type && gpu.type !== "none" && gpu.type !== "") {
    if (gpu.type === "nvidia") {
      accel = formatNvidiaGpu(gpu.model ?? "", gpu.vram_mb ?? 0);
    } else if (gpu.type === "amd") {
      const vramGb = gpu.vram_mb ? `${Math.round(gpu.vram_mb / 1024)}GB ` : "";
      accel = `${vramGb}AMD ${gpu.model ?? "GPU"}`.trim();
    } else if (gpu.type === "apple") {
      accel = gpu.model ?? "Apple Silicon";
    } else {
      const vramGb = gpu.vram_mb ? ` (${(gpu.vram_mb / 1024).toFixed(1)} GB)` : "";
      accel = `${gpu.model || gpu.type}${vramGb}`;
    }
  } else if (npu && npu.type && npu.type !== "none" && npu.type !== "") {
    const tops = npu.tops ? ` (${npu.tops} TOPS)` : "";
    accel = `${npu.device || npu.type}${tops}`;
  } else {
    accel = "CPU only";
  }

  const os = hw.os;
  const osStr = os && (os.distro || os.version)
    ? `${os.distro ?? ""} ${os.version ?? ""}`.trim()
    : "";

  const parts = [cpuShort, ramGb, accel];
  if (osStr) parts.push(osStr);
  return parts.join("  \u00b7  ");
}

/**
 * Return the set-union of KV cache quant types across the provided workers,
 * with "fp16" always present as the baseline.
 *
 * The deploy wizard calls this (or fetches /api/cluster/kv-quant-options
 * directly) to decide whether to render a KV quant dropdown.  If the
 * returned array has length === 1, no control should be shown at all.
 */
export function availableKvQuantTypes(workers: ClusterWorker[]): string[] {
  const types = new Set<string>(["fp16"]);
  for (const w of workers) {
    const support = w.kv_cache_quant_support;
    if (Array.isArray(support)) {
      for (const t of support) types.add(t);
    }
  }
  return Array.from(types).sort();
}

export interface KvQuantOptions {
  /** Union of all online workers' valid -ctk values; always at least ["fp16"]. */
  k: string[];
  /** Union of all online workers' valid -ctv values; always at least ["fp16"]. */
  v: string[];
  /** True if any online worker supports the boundary-layer-protect feature. */
  boundary: boolean;
  /** Legacy flat union for back-compat; union of k + v deduplicated. */
  flat: string[];
}

/**
 * Compute cluster-wide KV quant options from online workers.
 *
 * Workers that advertise kv_cache_quant_k_support / kv_cache_quant_v_support
 * are handled natively.  Older workers that only have kv_cache_quant_support
 * have that list applied to both K and V for back-compat.
 *
 * The deploy wizard uses the k / v / boundary fields to decide which controls
 * to render.  A dropdown is shown only when its list has more than one entry
 * (i.e. something beyond just "fp16" is available).
 */
export function availableKvQuantOptions(workers: ClusterWorker[]): KvQuantOptions {
  const kSet = new Set<string>(["fp16"]);
  const vSet = new Set<string>(["fp16"]);
  let boundary = false;

  for (const w of workers) {
    if (w.kv_cache_quant_k_support) {
      for (const t of w.kv_cache_quant_k_support) kSet.add(t);
    } else if (w.kv_cache_quant_support) {
      // Legacy worker: apply flat list to both K and V
      for (const t of w.kv_cache_quant_support) {
        kSet.add(t);
        vSet.add(t);
      }
    }

    if (w.kv_cache_quant_v_support) {
      for (const t of w.kv_cache_quant_v_support) vSet.add(t);
    }

    if (w.kv_cache_quant_boundary_layer_protect) {
      boundary = true;
    }
  }

  const k = Array.from(kSet).sort();
  const v = Array.from(vSet).sort();
  const flatSet = new Set([...k, ...v]);
  const flat = Array.from(flatSet).sort();

  return { k, v, boundary, flat };
}

/**
 * Normalise a backend name for display.
 *
 * Workers updated before this fix emitted names in the shape
 * `type@http://localhost:PORT` or `type@http://127.0.0.1:PORT`.
 * Updated workers emit `type:PORT`.  This helper rewrites legacy names
 * so older workers display consistently in the Activity and Cluster views.
 * Any other shape (already-new format, custom names) is returned unchanged.
 */
export function normalizeBackendName(name: string): string {
  const m = name.match(/^([^@]+)@https?:\/\/(?:localhost|127\.0\.0\.1|\[::1\]|0\.0\.0\.0):(\d+)$/);
  if (m) return `${m[1]}:${m[2]}`;
  return name;
}

/** Format a unix-seconds timestamp as a short relative string like "3s ago" / "2m ago". */
export function formatRelativeSeconds(hb: number | undefined, nowSec = Date.now() / 1000): string {
  if (!hb || typeof hb !== "number") return "never";
  const age = Math.max(0, nowSec - hb);
  if (age < 60) return `${age.toFixed(0)}s ago`;
  if (age < 3600) return `${(age / 60).toFixed(0)}m ago`;
  if (age < 86400) return `${(age / 3600).toFixed(1)}h ago`;
  return `${(age / 86400).toFixed(1)}d ago`;
}
