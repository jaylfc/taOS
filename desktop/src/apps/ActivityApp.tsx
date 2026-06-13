import { useState, useEffect, useCallback } from "react";
import {
  Activity, Cpu, MemoryStick, Zap, HardDrive, Thermometer, Network, CircuitBoard,
  Gauge, Layers, RefreshCw, Loader2, Server,
} from "lucide-react";
import { Card, CardContent, Button } from "@/components/ui";
import type { ClusterWorker } from "@/lib/cluster";
import { workerStatus, workerHardwareSummary, workerShortIp, normalizeBackendName, STATUS_PILL_CLASS, STATUS_LABEL } from "@/lib/cluster";

interface ActivityData {
  timestamp: number;
  hardware: {
    board?: string;
    cpu?: { model?: string; arch?: string; cores?: number; soc?: string };
    gpu?: { type?: string; model?: string; vram_mb?: number };
    npu?: { type?: string; tops?: number; cores?: number };
    ram_mb?: number;
  };
  cpu: {
    cores: Array<{ core: number; load_percent: number; freq_khz?: number; governor?: string }>;
    load_avg?: number[];
    overall_percent: number;
  };
  memory: {
    total_mb: number;
    used_mb: number;
    available_mb: number;
    percent: number;
    swap_total_mb: number;
    swap_used_mb: number;
    swap_percent: number;
  };
  npu: {
    cores: Array<{ core: number; load_percent: number }> | null;
    freq_hz: number | null;
    type?: string;
    tops?: number;
  };
  gpu: {
    load: { load_percent: number; freq_hz: number | null } | null;
    vram_percent: number | null;
    vram_used_mb: number | null;
    vram_total_mb: number | null;
    type?: string;
  };
  thermal: Array<{ name: string; temp_c: number }>;
  zram: Array<{ device: string; orig_mb: number; compr_mb: number; ratio: number }>;
  disk: {
    io_rate: { read_bps: number; write_bps: number };
    usage_percent: number;
    total_gb: number;
    used_gb: number;
  };
  network: Array<{ name: string; rx_bps: number; tx_bps: number; rx_total: number; tx_total: number }>;
  processes: Array<{ pid: number; name: string; user: string; rss_mb: number; cpu_percent: number }>;
}

interface LoadedModel {
  name: string;
  backend: string;
  purpose: string;
  size_mb?: number | null;
  vram_mb?: number | null;
  ram_mb?: number | null;
  /** Worker that hosts this model. "controller" for the local TAOS host,
   *  worker name for cluster-attached workers. */
  host?: string;
}

interface SchedulerResource {
  name: string;
  platform: string;
  runtime: string;
  runtime_version: string;
  concurrency: number;
  in_flight: number;
  tier: number;
  capabilities: string[];
  potential_capabilities: string[];
}

interface SchedulerStats {
  submitted: number;
  completed: number;
  errors: number;
  rejected: number;
  active: number;
  resources: SchedulerResource[];
}

interface SchedulerTask {
  task_id: string;
  capability: string;
  submitter: string;
  resource: string | null;
  status: string;
  elapsed_seconds: number | null;
  started_at: number | null;
  completed_at: number | null;
}

/**
 * Shorten a raw CPU model string into a clean display name.
 * "Intel(R) Core(TM) i5-10600 CPU @ 3.30GHz" → "Intel Core i5-10600"
 */
function shortCpuName(model: string | undefined | null): string {
  if (!model) return "";
  return model
    .replace(/\(R\)|\(TM\)|\(r\)|\(tm\)/g, "")
    .replace(/\s+CPU\s+@.*$/i, "")
    .replace(/\s+@\s+.*$/, "")
    .replace(/\s+/g, " ")
    .trim();
}

interface HwShape {
  cpu?: { model?: string; arch?: string; cores?: number; soc?: string };
  gpu?: { type?: string; model?: string; vram_mb?: number };
  npu?: { type?: string; device?: string; tops?: number; cores?: number };
}

/** Produce the human label for a hardware category (CPU / GPU / NPU). */
function hardwareLabel(kind: "cpu" | "gpu" | "npu", hw: HwShape | undefined | null): string {
  if (!hw) return "";
  if (kind === "cpu") {
    if (hw.cpu?.soc) return hw.cpu.soc.toUpperCase();
    const m = shortCpuName(hw.cpu?.model);
    if (m) return m;
    if (hw.cpu?.arch === "aarch64") return "ARM64";
    return "CPU";
  }
  if (kind === "gpu") {
    const gpu = hw.gpu;
    if (!gpu || !gpu.type || gpu.type === "none") return "";
    if (gpu.type === "nvidia") {
      const m = (gpu.model || "").replace(/NVIDIA\s*GeForce\s*/i, "").replace(/^NVIDIA\s*/i, "").trim() || "GPU";
      const vram = gpu.vram_mb ? ` ${Math.round(gpu.vram_mb / 1024)}GB` : "";
      return `NVIDIA ${m}${vram}`;
    }
    if (gpu.type === "amd") {
      const vram = gpu.vram_mb ? ` ${Math.round(gpu.vram_mb / 1024)}GB` : "";
      return `AMD ${gpu.model || "GPU"}${vram}`;
    }
    if (gpu.type === "apple") return gpu.model || "Apple Silicon";
    if (gpu.type === "mali") return gpu.model || "Mali GPU";
    return gpu.model || gpu.type.toUpperCase();
  }
  if (kind === "npu") {
    const npu = hw.npu;
    if (!npu || !npu.type || npu.type === "none") return "";
    if (npu.type === "rknpu") {
      const tops = npu.tops ? ` · ${npu.tops} TOPS` : "";
      return `RK3588${tops}`;
    }
    if (npu.device) return npu.device.toUpperCase();
    return npu.type.toUpperCase();
  }
  return "";
}

function formatBytes(bps: number): string {
  if (bps < 1024) return `${bps} B/s`;
  if (bps < 1024 * 1024) return `${(bps / 1024).toFixed(1)} KB/s`;
  if (bps < 1024 * 1024 * 1024) return `${(bps / (1024 * 1024)).toFixed(1)} MB/s`;
  return `${(bps / (1024 * 1024 * 1024)).toFixed(2)} GB/s`;
}

function formatMb(mb: number | null | undefined): string {
  if (mb === null || mb === undefined) return "\u2014";
  if (mb >= 1024) return `${(mb / 1024).toFixed(1)} GB`;
  return `${mb} MB`;
}

function colourForLoad(pct: number): string {
  if (pct < 50) return "#43e97b";
  if (pct < 80) return "#febc2e";
  return "#ff5f57";
}

function LoadBar({ value, label, unit = "%" }: { value: number; label?: string; unit?: string }) {
  return (
    <div className="flex items-center gap-2">
      {label && <span className="text-[10px] text-shell-text-tertiary w-12 shrink-0">{label}</span>}
      <div className="flex-1 h-1.5 rounded-full bg-white/5 overflow-hidden">
        <div
          className="h-full transition-all rounded-full"
          style={{ width: `${Math.min(100, Math.max(0, value))}%`, backgroundColor: colourForLoad(value) }}
        />
      </div>
      <span className="text-[10px] text-shell-text-secondary w-10 text-right tabular-nums">
        {value.toFixed(0)}{unit}
      </span>
    </div>
  );
}

export function ActivityApp({ windowId: _windowId }: { windowId: string }) {
  const [data, setData] = useState<ActivityData | null>(null);
  const [loadedModels, setLoadedModels] = useState<LoadedModel[]>([]);
  const [schedulerStats, setSchedulerStats] = useState<SchedulerStats | null>(null);
  const [schedulerTasks, setSchedulerTasks] = useState<SchedulerTask[]>([]);
  const [clusterWorkers, setClusterWorkers] = useState<ClusterWorker[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const fetchData = useCallback(async () => {
    try {
      const [actRes, modRes, schedStatsRes, schedTasksRes] = await Promise.all([
        fetch("/api/activity", { headers: { Accept: "application/json" } }),
        fetch("/api/models/loaded", { headers: { Accept: "application/json" } }).catch(() => null),
        fetch("/api/scheduler/stats", { headers: { Accept: "application/json" } }).catch(() => null),
        fetch("/api/scheduler/tasks?limit=8", { headers: { Accept: "application/json" } }).catch(() => null),
      ]);
      if (actRes.ok) {
        const json = await actRes.json();
        setData(json);
        setError(null);
      }
      if (modRes && modRes.ok) {
        const json = await modRes.json();
        setLoadedModels(json.loaded ?? []);
      }
      if (schedStatsRes && schedStatsRes.ok) {
        setSchedulerStats(await schedStatsRes.json());
      }
      if (schedTasksRes && schedTasksRes.ok) {
        const json = await schedTasksRes.json();
        setSchedulerTasks(json.tasks ?? []);
      }
    } catch (e) {
      setError(e instanceof Error ? e.message : "Failed to load");
    }
    setLoading(false);
  }, []);

  const fetchCluster = useCallback(async () => {
    try {
      const res = await fetch("/api/cluster/workers", { headers: { Accept: "application/json" } });
      if (res.ok) {
        const json = await res.json();
        if (Array.isArray(json)) setClusterWorkers(json as ClusterWorker[]);
      }
    } catch {
      /* ignore — cluster is best-effort */
    }
  }, []);

  useEffect(() => {
    fetchData();
    const interval = setInterval(fetchData, 2000);
    return () => clearInterval(interval);
  }, [fetchData]);

  useEffect(() => {
    fetchCluster();
    const interval = setInterval(fetchCluster, 10_000);
    return () => clearInterval(interval);
  }, [fetchCluster]);

  // Union the controller's locally loaded models with whatever the
  // cluster workers report as *in memory right now* in their heartbeats.
  // Each entry is tagged with `host` so the widget can show "on
  // fedora-lxc-test" / "on controller". We prefer `loaded_models` (the
  // subset currently resident in NPU/GPU/CPU memory) over `models` (the
  // full catalog of pulled-but-idle models). If a worker's version
  // doesn't populate `loaded_models` yet, we show nothing for it rather
  // than falsely listing every downloaded model as loaded.
  const mergedLoadedModels: LoadedModel[] = [
    ...loadedModels.map((m) => ({ ...m, host: m.host ?? "controller" })),
    ...clusterWorkers.flatMap((w) => {
      const out: LoadedModel[] = [];
      for (const b of w.backends ?? []) {
        const backendName = normalizeBackendName(b.name ?? b.type ?? "backend");
        const activeList = (b as { loaded_models?: unknown }).loaded_models;
        const models = Array.isArray(activeList) ? activeList : [];
        for (const model of models) {
          // Worker backend models can be plain strings or objects.
          // Normalise both shapes.
          if (typeof model === "string") {
            out.push({
              name: model,
              backend: backendName,
              purpose: "",
              host: w.name,
            });
          } else if (model && typeof model === "object") {
            const m = model as Record<string, unknown>;
            out.push({
              name: String(m["name"] ?? m["id"] ?? "model"),
              backend: backendName,
              purpose: String(m["purpose"] ?? m["capability"] ?? ""),
              size_mb: typeof m["size_mb"] === "number" ? (m["size_mb"] as number) : null,
              vram_mb: typeof m["vram_mb"] === "number" ? (m["vram_mb"] as number) : null,
              ram_mb: typeof m["ram_mb"] === "number" ? (m["ram_mb"] as number) : null,
              host: w.name,
            });
          }
        }
      }
      return out;
    }),
  ];

  if (loading && !data) {
    return (
      <div className="flex items-center justify-center h-full">
        <Loader2 size={24} className="animate-spin text-shell-text-tertiary" />
      </div>
    );
  }

  if (!data) {
    return (
      <div className="flex flex-col items-center justify-center h-full gap-2 text-shell-text-tertiary">
        <Activity size={32} />
        <p className="text-sm">Activity data unavailable</p>
        {error && <p className="text-xs">{error}</p>}
      </div>
    );
  }

  const { hardware, cpu, memory, npu, gpu, thermal, zram, disk, network, processes } = data;

  return (
    <div className="flex flex-col h-full bg-shell-bg-deep overflow-hidden">
      {/* Header */}
      <div className="flex items-center justify-between px-5 py-3 border-b border-white/5">
        <div>
          <div className="flex items-center gap-2">
            <Activity size={16} className="text-accent" />
            <h1 className="text-base font-semibold text-shell-text">Activity</h1>
          </div>
          <p className="text-xs text-shell-text-tertiary mt-0.5">
            {hardware.board ?? hardware.cpu?.model ?? "System"}
            {hardware.cpu?.arch && ` \u00b7 ${hardware.cpu.arch}`}
            {hardware.ram_mb && ` \u00b7 ${(hardware.ram_mb / 1024).toFixed(0)} GB RAM`}
          </p>
        </div>
        <Button variant="ghost" size="icon" onClick={fetchData} aria-label="Refresh">
          <RefreshCw size={14} />
        </Button>
      </div>

      {/* Grid */}
      <div className="flex-1 overflow-y-auto p-4 grid grid-cols-12 gap-3 auto-rows-min">
        {/* CPU */}
        <Card className="col-span-12 md:col-span-6 p-4">
          <CardContent className="p-0">
            <div className="flex items-center justify-between mb-3">
              <div className="flex items-center gap-2">
                <Cpu size={14} className="text-blue-400" />
                <h3 className="text-xs font-semibold text-shell-text">CPU</h3>
              </div>
              <span className="text-[10px] text-shell-text-tertiary">
                {cpu.overall_percent.toFixed(0)}% overall
                {cpu.load_avg && ` \u00b7 ${cpu.load_avg.map((v) => v.toFixed(2)).join(" ")}`}
              </span>
            </div>
            <div className="space-y-1.5">
              {cpu.cores.map((core) => (
                <LoadBar
                  key={core.core}
                  label={`C${core.core}${core.freq_khz ? ` ${(core.freq_khz / 1000000).toFixed(1)}G` : ""}`}
                  value={core.load_percent}
                />
              ))}
            </div>
          </CardContent>
        </Card>

        {/* NPU */}
        {(npu.cores || npu.type) && (
          <Card className="col-span-12 md:col-span-6 p-4">
            <CardContent className="p-0">
              <div className="flex items-center justify-between mb-3">
                <div className="flex items-center gap-2">
                  <Zap size={14} className="text-slate-400" />
                  <h3 className="text-xs font-semibold text-shell-text">NPU</h3>
                </div>
                <span className="text-[10px] text-shell-text-tertiary">
                  {npu.type ?? "Unknown"}
                  {npu.tops && ` \u00b7 ${npu.tops} TOPS`}
                  {npu.freq_hz && ` \u00b7 ${(npu.freq_hz / 1e9).toFixed(2)} GHz`}
                </span>
              </div>
              {npu.cores && npu.cores.length > 0 ? (
                <div className="space-y-1.5">
                  {npu.cores.map((core) => (
                    <LoadBar key={core.core} label={`Core ${core.core}`} value={core.load_percent} />
                  ))}
                </div>
              ) : (
                <p className="text-xs text-shell-text-tertiary">
                  Per-core stats require debugfs access.{" "}
                  <span className="text-shell-text-secondary">Run as root or grant cap_dac_read_search.</span>
                </p>
              )}
            </CardContent>
          </Card>
        )}

        {/* Memory */}
        <Card className="col-span-12 md:col-span-6 lg:col-span-4 p-4">
          <CardContent className="p-0">
            <div className="flex items-center justify-between mb-3">
              <div className="flex items-center gap-2">
                <MemoryStick size={14} className="text-emerald-400" />
                <h3 className="text-xs font-semibold text-shell-text">Memory</h3>
              </div>
            </div>
            <LoadBar label="RAM" value={memory.percent} />
            <p className="text-[10px] text-shell-text-tertiary mt-1">
              {formatMb(memory.used_mb)} / {formatMb(memory.total_mb)}
            </p>
            {memory.swap_total_mb > 0 && (
              <>
                <div className="mt-3">
                  <LoadBar label="Swap" value={memory.swap_percent} />
                </div>
                <p className="text-[10px] text-shell-text-tertiary mt-1">
                  {formatMb(memory.swap_used_mb)} / {formatMb(memory.swap_total_mb)}
                </p>
              </>
            )}
            {zram.length > 0 && (
              <div className="mt-3 pt-2 border-t border-white/5">
                <p className="text-[10px] text-shell-text-tertiary mb-1">ZRAM compression</p>
                {zram.map((z) => (
                  <div key={z.device} className="flex items-center justify-between text-[10px]">
                    <span className="text-shell-text-secondary">{z.device}</span>
                    <span className="text-shell-text-tertiary">
                      {formatMb(z.compr_mb)} ← {formatMb(z.orig_mb)} ({z.ratio.toFixed(1)}×)
                    </span>
                  </div>
                ))}
              </div>
            )}
          </CardContent>
        </Card>

        {/* GPU */}
        {gpu.type && gpu.type !== "none" && (
          <Card className="col-span-12 md:col-span-6 lg:col-span-4 p-4">
            <CardContent className="p-0">
              <div className="flex items-center justify-between mb-3">
                <div className="flex items-center gap-2">
                  <CircuitBoard size={14} className="text-cyan-400" />
                  <h3 className="text-xs font-semibold text-shell-text">GPU</h3>
                </div>
                <span className="text-[10px] text-shell-text-tertiary">{gpu.type}</span>
              </div>
              {gpu.load && (
                <LoadBar label="Load" value={gpu.load.load_percent} />
              )}
              {gpu.vram_percent !== null && (
                <div className="mt-2">
                  <LoadBar label="VRAM" value={gpu.vram_percent} />
                  <p className="text-[10px] text-shell-text-tertiary mt-1">
                    {formatMb(gpu.vram_used_mb)} / {formatMb(gpu.vram_total_mb)}
                  </p>
                </div>
              )}
              {!gpu.load && gpu.vram_percent === null && (
                <p className="text-xs text-shell-text-tertiary">Stats unavailable</p>
              )}
            </CardContent>
          </Card>
        )}

        {/* Disk */}
        <Card className="col-span-12 md:col-span-6 lg:col-span-4 p-4">
          <CardContent className="p-0">
            <div className="flex items-center justify-between mb-3">
              <div className="flex items-center gap-2">
                <HardDrive size={14} className="text-amber-400" />
                <h3 className="text-xs font-semibold text-shell-text">Disk</h3>
              </div>
            </div>
            <LoadBar label="Used" value={disk.usage_percent} />
            <p className="text-[10px] text-shell-text-tertiary mt-1">
              {disk.used_gb} / {disk.total_gb} GB
            </p>
            <div className="mt-3 pt-2 border-t border-white/5 space-y-0.5 text-[10px]">
              <div className="flex justify-between">
                <span className="text-shell-text-tertiary">Read</span>
                <span className="text-shell-text-secondary tabular-nums">{formatBytes(disk.io_rate.read_bps)}</span>
              </div>
              <div className="flex justify-between">
                <span className="text-shell-text-tertiary">Write</span>
                <span className="text-shell-text-secondary tabular-nums">{formatBytes(disk.io_rate.write_bps)}</span>
              </div>
            </div>
          </CardContent>
        </Card>

        {/* Loaded models — full-width row so multi-worker model lists have
            room to breathe. */}
        <Card className="col-span-12 p-4">
          <CardContent className="p-0">
            <div className="flex items-center justify-between mb-3">
              <div className="flex items-center gap-2">
                <Layers size={14} className="text-pink-400" />
                <h3 className="text-xs font-semibold text-shell-text">Loaded Models ({mergedLoadedModels.length})</h3>
              </div>
            </div>
            {mergedLoadedModels.length === 0 ? (
              <p className="text-[11px] text-shell-text-tertiary italic">
                No models currently loaded — backends report here when they hold a model in memory, on this host or any cluster worker.
              </p>
            ) : (
              <div className="space-y-1.5">
                {mergedLoadedModels.map((m, i) => (
                  <div key={`${m.host ?? "controller"}-${m.name}-${i}`} className="flex items-center gap-2 p-2 rounded-lg bg-white/[0.02] border border-white/5">
                    <div className="w-1.5 h-1.5 rounded-full bg-emerald-400 animate-pulse shrink-0" />
                    <div className="flex-1 min-w-0">
                      <div className="flex items-center gap-1.5 min-w-0">
                        <span className="text-[11px] font-medium text-shell-text truncate">{m.name}</span>
                        {m.host && m.host !== "controller" && (
                          <span className="text-[9px] px-1.5 py-0.5 rounded-full bg-teal-500/15 text-teal-200 font-semibold whitespace-nowrap">
                            {m.host}
                          </span>
                        )}
                      </div>
                      <div className="flex items-center gap-2 text-[10px] text-shell-text-tertiary flex-wrap">
                        {m.purpose && <span>{m.purpose}</span>}
                        {m.purpose && <span>·</span>}
                        <span>{m.backend}</span>
                        {m.ram_mb != null && m.ram_mb > 0 && <><span>·</span><span>{formatMb(m.ram_mb)}</span></>}
                        {m.vram_mb != null && m.vram_mb > 0 && <><span>·</span><span className="text-blue-400">VRAM {formatMb(m.vram_mb)}</span></>}
                      </div>
                    </div>
                  </div>
                ))}
              </div>
            )}
          </CardContent>
        </Card>

        {/* Cluster workers */}
        <Card className="col-span-12 p-4">
          <CardContent className="p-0">
            <div className="flex items-center justify-between mb-3">
              <div className="flex items-center gap-2">
                <Server size={14} className="text-teal-400" />
                <h3 className="text-xs font-semibold text-shell-text">
                  Cluster ({clusterWorkers.length} worker{clusterWorkers.length === 1 ? "" : "s"})
                </h3>
              </div>
              <Button variant="ghost" size="icon" onClick={fetchCluster} aria-label="Refresh cluster workers">
                <RefreshCw size={12} />
              </Button>
            </div>
            {clusterWorkers.length === 0 ? (
              <div className="flex flex-col items-center justify-center gap-2 py-6 text-center">
                <p className="text-[11px] text-shell-text-tertiary">
                  No workers registered yet.
                </p>
                <a
                  href="https://github.com/jaylfc/tinyagentos#distributed-compute-cluster"
                  target="_blank"
                  rel="noopener noreferrer"
                  className="text-[11px] px-3 py-1.5 rounded-md bg-white/5 border border-white/10 text-shell-text-secondary hover:bg-white/10 transition-colors"
                  aria-label="How to add a worker (opens docs in new tab)"
                >
                  How to add a worker
                </a>
              </div>
            ) : (
              <div
                className="grid grid-cols-1 md:grid-cols-2 gap-2"
                role="list"
                aria-label="Cluster workers"
              >
                {clusterWorkers.map((w) => {
                  const status = workerStatus(w);
                  const backends = w.backends ?? [];
                  const capabilities = w.capabilities ?? [];
                  const activeSet = new Set(capabilities);
                  const latentCaps = w.tier_id
                    ? (w.potential_capabilities ?? []).filter((c) => !activeSet.has(c))
                    : [];
                  return (
                    <div
                      key={w.name}
                      role="listitem"
                      className="p-2.5 rounded-lg bg-white/[0.02] border border-white/5"
                    >
                      <div className="flex items-center justify-between gap-2 mb-1">
                        <div className="flex items-center gap-1.5 min-w-0">
                          <span className="text-[11px] font-semibold text-shell-text truncate">
                            {w.name}
                          </span>
                          <span className="text-[10px] text-shell-text-tertiary">
                            {"\u00b7"} {workerShortIp(w)}
                          </span>
                          {w.tier_id && (
                            <span
                              className="text-[9px] px-1.5 py-0.5 rounded-full bg-white/[0.05] border border-white/10 text-shell-text-tertiary font-mono"
                              aria-label={`Hardware tier: ${w.tier_id}`}
                            >
                              {w.tier_id}
                            </span>
                          )}
                        </div>
                        <span
                          className={`text-[9px] px-1.5 py-0.5 rounded-full font-semibold border ${STATUS_PILL_CLASS[status]}`}
                          aria-label={`Status: ${STATUS_LABEL[status]}`}
                        >
                          {STATUS_LABEL[status]}
                        </span>
                      </div>
                      <div className="text-[10px] text-shell-text-tertiary truncate">
                        {workerHardwareSummary(w)}
                      </div>
                      <div className="mt-1.5 flex flex-wrap gap-1">
                        {backends.length === 0 ? (
                          <span className="text-[9px] text-shell-text-tertiary italic">
                            No backends loaded
                          </span>
                        ) : (
                          backends.map((b, i) => (
                            <span
                              key={`${w.name}-b-${i}`}
                              className="text-[9px] px-1.5 py-0.5 rounded-full bg-sky-500/15 text-sky-200 font-medium"
                              title={b.type ?? b.name ?? ""}
                            >
                              {normalizeBackendName(b.name ?? b.type ?? "backend")}
                            </span>
                          ))
                        )}
                      </div>
                      <div className="mt-1 flex flex-wrap gap-1">
                        {capabilities.length === 0 && latentCaps.length === 0 ? (
                          <span className="text-[9px] text-shell-text-tertiary italic">
                            No capabilities yet
                          </span>
                        ) : (
                          <>
                            {capabilities.map((c) => (
                              <span
                                key={`${w.name}-c-${c}`}
                                className="text-[9px] px-1.5 py-0.5 rounded-full bg-violet-500/15 text-violet-200 font-medium"
                                aria-label={`Current capability: ${c}`}
                              >
                                {c}
                              </span>
                            ))}
                            {latentCaps.map((c) => (
                              <span
                                key={`${w.name}-p-${c}`}
                                className="text-[9px] px-1.5 py-0.5 rounded-full bg-white/[0.03] border border-white/10 text-shell-text-tertiary font-medium"
                                aria-label={`Potential capability: ${c}`}
                                title="Hardware can support this — install a model with this capability to enable it"
                              >
                                {c}
                              </span>
                            ))}
                          </>
                        )}
                      </div>
                    </div>
                  );
                })}
              </div>
            )}
          </CardContent>
        </Card>

        {/* Thermal */}
        {thermal.length > 0 && (
          <Card className="col-span-12 md:col-span-6 p-4">
            <CardContent className="p-0">
              <div className="flex items-center justify-between mb-3">
                <div className="flex items-center gap-2">
                  <Thermometer size={14} className="text-red-400" />
                  <h3 className="text-xs font-semibold text-shell-text">Thermal</h3>
                </div>
              </div>
              <div className="grid grid-cols-2 gap-x-3 gap-y-1.5 text-[11px]">
                {thermal.map((z) => {
                  const hot = z.temp_c > 70;
                  const warm = z.temp_c > 55;
                  const colour = hot ? "text-red-400" : warm ? "text-amber-400" : "text-emerald-400";
                  return (
                    <div key={z.name} className="flex items-center justify-between">
                      <span className="text-shell-text-tertiary truncate">{z.name}</span>
                      <span className={`${colour} tabular-nums`}>{z.temp_c.toFixed(1)}°C</span>
                    </div>
                  );
                })}
              </div>
            </CardContent>
          </Card>
        )}

        {/* Network */}
        {network.length > 0 && (
          <Card className="col-span-12 md:col-span-6 p-4">
            <CardContent className="p-0">
              <div className="flex items-center justify-between mb-3">
                <div className="flex items-center gap-2">
                  <Network size={14} className="text-indigo-400" />
                  <h3 className="text-xs font-semibold text-shell-text">Network</h3>
                </div>
              </div>
              <div className="space-y-2">
                {network.map((iface) => (
                  <div key={iface.name} className="text-[11px]">
                    <div className="flex justify-between">
                      <span className="text-shell-text-secondary">{iface.name}</span>
                      <span className="text-shell-text-tertiary tabular-nums">
                        ↓ {formatBytes(iface.rx_bps)} · ↑ {formatBytes(iface.tx_bps)}
                      </span>
                    </div>
                  </div>
                ))}
              </div>
            </CardContent>
          </Card>
        )}


        {/* Resource scheduler */}
        {schedulerStats && (
          <Card className="col-span-12 p-4">
            <CardContent className="p-0">
              <div className="flex items-center justify-between mb-3">
                <div className="flex items-center gap-2">
                  <Activity size={14} className="text-violet-400" />
                  <h3 className="text-xs font-semibold text-shell-text">Scheduler</h3>
                </div>
                <div className="flex items-center gap-3 text-[10px] text-shell-text-tertiary tabular-nums">
                  <span>submitted {schedulerStats.submitted}</span>
                  <span>done {schedulerStats.completed}</span>
                  {schedulerStats.errors > 0 && (
                    <span className="text-red-400">err {schedulerStats.errors}</span>
                  )}
                  {schedulerStats.rejected > 0 && (
                    <span className="text-amber-400">rejected {schedulerStats.rejected}</span>
                  )}
                  <span className="text-emerald-400">active {schedulerStats.active}</span>
                </div>
              </div>
              <div className="grid grid-cols-1 md:grid-cols-2 gap-2 mb-3">
                {/* Controller's local scheduler resources */}
                {schedulerStats.resources.map((r) => {
                  const tierLabel = ["GPU", "NPU", "CPU", "CLUSTER"][r.tier] ?? "?";
                  const tierColor = [
                    "text-emerald-400 bg-emerald-500/10",
                    "text-violet-400 bg-violet-500/10",
                    "text-sky-400 bg-sky-500/10",
                    "text-amber-400 bg-amber-500/10",
                  ][r.tier] ?? "text-shell-text-tertiary bg-white/5";
                  const ready = new Set(r.capabilities);
                  const latent = r.potential_capabilities.filter((c) => !ready.has(c));
                  // Prefer hardware model name over generic resource name
                  const controllerHw = data?.hardware as HwShape | undefined;
                  let hwLabel = "";
                  if (r.tier === 0) hwLabel = hardwareLabel("gpu", controllerHw);
                  else if (r.tier === 1) hwLabel = hardwareLabel("npu", controllerHw);
                  else if (r.tier === 2) hwLabel = hardwareLabel("cpu", controllerHw);
                  const primaryLabel = hwLabel || r.name;
                  return (
                    <div
                      key={r.name}
                      className="p-2 rounded-lg bg-white/[0.02] border border-white/5"
                    >
                      <div className="flex items-center justify-between mb-1 gap-2">
                        <div className="flex items-center gap-1.5 min-w-0">
                          <span
                            className={`text-[9px] px-1.5 py-0.5 rounded-full font-semibold ${tierColor}`}
                          >
                            {tierLabel}
                          </span>
                          <span className="text-[11px] font-medium text-shell-text truncate">
                            {primaryLabel}
                          </span>
                        </div>
                        <span
                          className="text-[10px] text-shell-text-tertiary tabular-nums shrink-0"
                          title={`${r.in_flight} tasks running, ${r.concurrency - r.in_flight} slots free`}
                        >
                          {r.in_flight} active · {r.concurrency} slots
                        </span>
                      </div>
                      <div className="text-[10px] text-shell-text-tertiary truncate">
                        <span className="text-shell-text/60">controller</span> · {r.platform} · {r.runtime}
                        {r.runtime_version && ` ${r.runtime_version}`}
                      </div>
                      <div className="mt-1 flex flex-wrap gap-1">
                        {r.capabilities.map((c) => (
                          <span
                            key={`ready-${c}`}
                            className="text-[9px] px-1.5 py-0.5 rounded-full bg-violet-500/20 text-violet-200 font-medium"
                            title="Ready now — backend is loaded"
                          >
                            {c}
                          </span>
                        ))}
                        {latent.map((c) => (
                          <span
                            key={`latent-${c}`}
                            className="text-[9px] px-1.5 py-0.5 rounded-full bg-white/[0.03] text-shell-text-tertiary border border-white/5"
                            title="Latent — supported by this hardware class but no backend loaded"
                          >
                            {c}
                          </span>
                        ))}
                      </div>
                    </div>
                  );
                })}

                {/* Cluster workers — each hardware unit gets its own card */}
                {clusterWorkers.flatMap((w) => {
                  const hw = w.hardware as HwShape | undefined;
                  const online = w.status === "online";
                  type WorkerCard = { kind: "cpu" | "gpu" | "npu"; label: string; tierIdx: number };
                  const cards: WorkerCard[] = [];
                  const gpuLabel = hardwareLabel("gpu", hw);
                  if (gpuLabel) cards.push({ kind: "gpu", label: gpuLabel, tierIdx: 0 });
                  const npuLabel = hardwareLabel("npu", hw);
                  if (npuLabel) cards.push({ kind: "npu", label: npuLabel, tierIdx: 1 });
                  const cpuLabel = hardwareLabel("cpu", hw);
                  if (cpuLabel) cards.push({ kind: "cpu", label: cpuLabel, tierIdx: 2 });

                  // The controller self-registers as a worker named "local"
                  // (loopback url). Its NPU/CPU are already shown above as the
                  // controller's own scheduler resources, so for the local node
                  // only surface devices the controller does not already list
                  // (e.g. an integrated GPU it does not schedule on). This
                  // avoids the same physical hardware appearing twice.
                  const isSelf =
                    w.name === "local" ||
                    /\/\/(127\.0\.0\.1|localhost|\[::1\])(:|\/|$)/.test(w.url || "");
                  const controllerTiers = new Set(schedulerStats.resources.map((res) => res.tier));
                  const visibleCards = isSelf
                    ? cards.filter((c) => !controllerTiers.has(c.tierIdx))
                    : cards;

                  const tierLabels = ["GPU", "NPU", "CPU"];
                  const tierColors = [
                    "text-emerald-400 bg-emerald-500/10",
                    "text-violet-400 bg-violet-500/10",
                    "text-sky-400 bg-sky-500/10",
                  ];

                  return visibleCards.map((card) => (
                    <div
                      key={`${w.name}-${card.kind}`}
                      className={`p-2 rounded-lg border ${online ? "bg-white/[0.02] border-white/5" : "bg-white/[0.01] border-white/5 opacity-60"}`}
                    >
                      <div className="flex items-center justify-between mb-1 gap-2">
                        <div className="flex items-center gap-1.5 min-w-0">
                          <span className={`text-[9px] px-1.5 py-0.5 rounded-full font-semibold ${tierColors[card.tierIdx]}`}>
                            {tierLabels[card.tierIdx]}
                          </span>
                          <span className="text-[11px] font-medium text-shell-text truncate">
                            {card.label}
                          </span>
                        </div>
                        <span className="text-[10px] text-shell-text-tertiary shrink-0">
                          {online ? "online" : w.status}
                        </span>
                      </div>
                      <div className="text-[10px] text-shell-text-tertiary truncate">
                        <span className="text-blue-400/80">worker</span> · {w.name}
                        {hw?.cpu?.cores && card.kind === "cpu" && ` · ${hw.cpu.cores} cores`}
                        {hw?.npu?.cores && card.kind === "npu" && ` · ${hw.npu.cores} cores`}
                      </div>
                      <div className="mt-1 flex flex-wrap gap-1">
                        {(() => {
                          const ready = new Set(w.capabilities || []);
                          const potential = (w.potential_capabilities || []) as string[];
                          const latent = potential.filter((c) => !ready.has(c));
                          return [
                            ...(w.capabilities || []).map((c) => (
                              <span
                                key={`worker-ready-${c}`}
                                className="text-[9px] px-1.5 py-0.5 rounded-full bg-violet-500/20 text-violet-200 font-medium"
                                title="Ready now — backend is loaded"
                              >
                                {c}
                              </span>
                            )),
                            ...latent.map((c) => (
                              <span
                                key={`worker-latent-${c}`}
                                className="text-[9px] px-1.5 py-0.5 rounded-full bg-white/[0.03] text-shell-text-tertiary border border-white/5"
                                title="Latent — supported by this hardware but no backend loaded yet"
                              >
                                {c}
                              </span>
                            )),
                          ];
                        })()}
                      </div>
                    </div>
                  ));
                })}
              </div>
              {schedulerTasks.length > 0 && (
                <div className="mt-2 space-y-0.5">
                  <div className="text-[10px] text-shell-text-tertiary mb-1">Recent tasks</div>
                  {schedulerTasks.slice(0, 8).map((t) => {
                    const statusColor =
                      t.status === "complete"
                        ? "text-emerald-400"
                        : t.status === "running"
                        ? "text-violet-400"
                        : t.status === "error"
                        ? "text-red-400"
                        : t.status === "rejected"
                        ? "text-amber-400"
                        : "text-shell-text-tertiary";
                    return (
                      <div
                        key={t.task_id}
                        className="flex items-center gap-2 text-[10px] py-0.5 tabular-nums"
                      >
                        <span className={`${statusColor} w-16`}>{t.status}</span>
                        <span className="text-shell-text-secondary w-28 truncate">{t.capability}</span>
                        <span className="text-shell-text w-28 truncate">{t.resource ?? "-"}</span>
                        <span className="text-shell-text-tertiary flex-1 truncate">{t.submitter}</span>
                        <span className="text-shell-text-tertiary w-14 text-right">
                          {t.elapsed_seconds != null ? `${t.elapsed_seconds.toFixed(1)}s` : ""}
                        </span>
                      </div>
                    );
                  })}
                </div>
              )}
            </CardContent>
          </Card>
        )}

        {/* Top processes */}
        <Card className="col-span-12 p-4">
          <CardContent className="p-0">
            <div className="flex items-center justify-between mb-3">
              <div className="flex items-center gap-2">
                <Gauge size={14} className="text-white/70" />
                <h3 className="text-xs font-semibold text-shell-text">Top Processes</h3>
              </div>
            </div>
            <div className="space-y-1">
              {processes.map((p) => (
                <div key={p.pid} className="flex items-center gap-2 text-[11px] py-0.5">
                  <span className="text-shell-text-tertiary w-12 tabular-nums">{p.pid}</span>
                  <span className="text-shell-text-secondary w-16 truncate">{p.user}</span>
                  <span className="text-shell-text flex-1 truncate">{p.name}</span>
                  <span className="text-shell-text-tertiary w-16 text-right tabular-nums">
                    {p.cpu_percent.toFixed(0)}%
                  </span>
                  <span className="text-shell-text-secondary w-20 text-right tabular-nums">
                    {formatMb(p.rss_mb)}
                  </span>
                </div>
              ))}
            </div>
          </CardContent>
        </Card>
      </div>
    </div>
  );
}
