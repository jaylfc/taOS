import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { MobileSplitView } from "@/components/mobile/MobileSplitView";
import {
  Network, RefreshCw, ExternalLink, Copy, Check, Trash2, Wand2,
  Cpu, MemoryStick, HardDrive, CircuitBoard, Zap, Server, Monitor,
  X,
} from "lucide-react";
import { Button, Card, CardContent } from "@/components/ui";
import type { ClusterWorker, WorkerStatus } from "@/lib/cluster";
import {
  workerStatus,
  workerHardwareSummary,
  workerShortIp,
  formatRelativeSeconds,
  normalizeBackendName,
  STATUS_PILL_CLASS,
  STATUS_LABEL,
} from "@/lib/cluster";

type SortKey = "name" | "status" | "last_seen";

const STATUS_ORDER: Record<WorkerStatus, number> = {
  online: 0,
  stale: 1,
  offline: 2,
  unknown: 3,
};

function StatusPill({ status }: { status: WorkerStatus }) {
  return (
    <span
      className={`text-[10px] px-1.5 py-0.5 rounded-full font-semibold border ${STATUS_PILL_CLASS[status]}`}
      aria-label={`Status: ${STATUS_LABEL[status]}`}
    >
      {STATUS_LABEL[status]}
    </span>
  );
}

function WorkerListCard({
  worker,
  selected,
  onSelect,
}: {
  worker: ClusterWorker;
  selected: boolean;
  onSelect: () => void;
}) {
  const status = workerStatus(worker);
  const backends = worker.backends ?? [];
  const capabilities = worker.capabilities ?? [];
  const activeSet = new Set(capabilities);
  const latentCaps = worker.tier_id
    ? (worker.potential_capabilities ?? []).filter((c) => !activeSet.has(c))
    : [];
  return (
    <button
      type="button"
      onClick={onSelect}
      aria-pressed={selected}
      aria-label={`Select worker ${worker.name}`}
      className={`w-full text-left p-2.5 rounded-lg border transition-colors ${
        selected
          ? "border-accent/50 bg-accent/10"
          : "border-white/5 bg-white/[0.02] hover:bg-white/[0.04]"
      }`}
    >
      <div className="flex items-center justify-between gap-2 mb-1">
        <div className="flex items-center gap-1.5 min-w-0">
          <span className="text-[12px] font-semibold text-shell-text truncate">
            {worker.name}
          </span>
          <span className="text-[10px] text-shell-text-tertiary">
            {"\u00b7"} {workerShortIp(worker)}
          </span>
        </div>
        <StatusPill status={status} />
      </div>
      <div className="text-[10px] text-shell-text-tertiary truncate">
        {workerHardwareSummary(worker)}
      </div>
      <div className="mt-1.5 flex flex-wrap gap-1">
        {backends.length === 0 ? (
          <span className="text-[9px] text-shell-text-tertiary italic">No backends loaded</span>
        ) : (
          backends.slice(0, 4).map((b, i) => (
            <span
              key={`${worker.name}-lb-${i}`}
              className="text-[9px] px-1.5 py-0.5 rounded-full bg-sky-500/15 text-sky-200 font-medium"
            >
              {normalizeBackendName(b.name ?? b.type ?? "backend")}
            </span>
          ))
        )}
        {capabilities.slice(0, 4).map((c) => (
          <span
            key={`${worker.name}-lc-${c}`}
            className="text-[9px] px-1.5 py-0.5 rounded-full bg-cyan-500/15 text-cyan-200 font-medium"
            aria-label={`Current capability: ${c}`}
          >
            {c}
          </span>
        ))}
        {latentCaps.slice(0, 3).map((c) => (
          <span
            key={`${worker.name}-lp-${c}`}
            className="text-[9px] px-1.5 py-0.5 rounded-full bg-white/[0.03] border border-white/10 text-shell-text-tertiary font-medium"
            aria-label={`Potential capability: ${c}`}
            title="Hardware can support this — install a model with this capability to enable it"
          >
            {c}
          </span>
        ))}
        {latentCaps.length > 3 && (
          <span
            className="text-[9px] px-1.5 py-0.5 rounded-full bg-white/[0.03] border border-white/10 text-shell-text-tertiary font-medium"
            aria-label={`${latentCaps.length - 3} more potential capabilities`}
          >
            +{latentCaps.length - 3} more
          </span>
        )}
      </div>
    </button>
  );
}

function LabelValue({ label, value }: { label: string; value: React.ReactNode }) {
  return (
    <div className="flex flex-col gap-0.5">
      <span className="text-[10px] uppercase tracking-wide text-shell-text-tertiary">{label}</span>
      <span className="text-[12px] text-shell-text">{value ?? "\u2014"}</span>
    </div>
  );
}

function Section({
  title,
  icon,
  children,
}: {
  title: string;
  icon: React.ReactNode;
  children: React.ReactNode;
}) {
  return (
    <Card className="p-4">
      <CardContent className="p-0">
        <div className="flex items-center gap-2 mb-3">
          {icon}
          <h3 className="text-xs font-semibold text-shell-text">{title}</h3>
        </div>
        {children}
      </CardContent>
    </Card>
  );
}

function WorkerDetail({
  worker,
  onRefresh,
  onDeregister,
  onOptimise,
  busy,
}: {
  worker: ClusterWorker;
  onRefresh: () => void;
  onDeregister: (name: string) => Promise<void>;
  onOptimise: () => Promise<void>;
  busy: boolean;
}) {
  const [copied, setCopied] = useState<"name" | "url" | null>(null);
  const status = workerStatus(worker);
  const hw = worker.hardware ?? {};
  const cpu = hw.cpu ?? {};
  const gpu = hw.gpu ?? {};
  const npu = hw.npu ?? {};
  const disk = hw.disk ?? {};
  const os = hw.os ?? {};
  const backends = worker.backends ?? [];
  const capabilities = worker.capabilities ?? [];
  const models = worker.models ?? [];
  const activeSet = new Set(capabilities);
  const latentCaps = worker.tier_id
    ? (worker.potential_capabilities ?? []).filter((c) => !activeSet.has(c))
    : [];

  const copy = useCallback(async (kind: "name" | "url", value: string) => {
    try {
      await navigator.clipboard.writeText(value);
      setCopied(kind);
      window.setTimeout(() => setCopied(null), 1200);
    } catch {
      /* no-op: clipboard may be unavailable */
    }
  }, []);

  const gpuFlags: string[] = [];
  if (gpu.cuda) gpuFlags.push("CUDA");
  if (gpu.rocm) gpuFlags.push("ROCm");
  if (gpu.vulkan) gpuFlags.push("Vulkan");
  if (gpu.metal) gpuFlags.push("Metal");
  if (gpu.opencl) gpuFlags.push("OpenCL");

  return (
    <div className="flex flex-col h-full min-h-0 overflow-hidden">
      {/* Header */}
      <div className="flex flex-wrap items-start justify-between gap-2 px-4 py-3 border-b border-white/5 shrink-0">
        <div className="min-w-0 flex-1">
          <div className="flex items-center gap-2 flex-wrap">
            <h2 className="text-sm font-semibold text-shell-text">{worker.name}</h2>
            <StatusPill status={status} />
            {worker.tier_id && (
              <span
                className="text-[9px] px-1.5 py-0.5 rounded-full bg-white/[0.05] border border-white/10 text-shell-text-tertiary font-mono"
                aria-label={`Hardware tier: ${worker.tier_id}`}
              >
                {worker.tier_id}
              </span>
            )}
          </div>
          <p className="text-[11px] text-shell-text-tertiary mt-0.5 break-all">
            {worker.url}
            {worker.last_heartbeat
              ? `  \u00b7  last seen ${formatRelativeSeconds(worker.last_heartbeat)}`
              : ""}
            {worker.platform ? `  \u00b7  ${worker.platform}` : ""}
          </p>
        </div>
        <a
          href={worker.url}
          target="_blank"
          rel="noopener noreferrer"
          className="shrink-0 inline-flex items-center gap-1.5 text-[11px] px-2.5 py-1.5 rounded-md bg-white/5 border border-white/10 text-shell-text-secondary hover:bg-white/10 transition-colors min-h-[44px]"
          aria-label={`Open worker ${worker.name} UI in a new tab`}
        >
          <ExternalLink size={12} />
          Open worker UI
        </a>
      </div>

      {/* Body */}
      <div className="flex-1 overflow-y-auto p-4 space-y-3">
        {/* Hardware */}
        <Section title="Hardware" icon={<Cpu size={14} className="text-blue-400" />}>
          <div className="grid grid-cols-2 md:grid-cols-3 gap-3">
            <LabelValue label="CPU Model" value={cpu.model || "\u2014"} />
            <LabelValue label="Arch" value={cpu.arch || "\u2014"} />
            <LabelValue label="Cores" value={cpu.cores ?? "\u2014"} />
            {cpu.soc && <LabelValue label="SoC" value={cpu.soc} />}
            <LabelValue
              label="RAM"
              value={hw.ram_mb ? `${(hw.ram_mb / 1024).toFixed(1)} GB` : "\u2014"}
            />
            {hw.board && <LabelValue label="Board" value={hw.board} />}
          </div>
        </Section>

        {/* GPU */}
        <Section title="GPU" icon={<CircuitBoard size={14} className="text-cyan-400" />}>
          {gpu.type && gpu.type !== "none" ? (
            <div className="space-y-2">
              <div className="grid grid-cols-2 md:grid-cols-3 gap-3">
                <LabelValue label="Type" value={gpu.type} />
                <LabelValue label="Model" value={gpu.model || "\u2014"} />
                <LabelValue
                  label="VRAM"
                  value={gpu.vram_mb ? `${(gpu.vram_mb / 1024).toFixed(1)} GB` : "\u2014"}
                />
              </div>
              {gpuFlags.length > 0 && (
                <div className="flex flex-wrap gap-1 pt-1">
                  {gpuFlags.map((f) => (
                    <span
                      key={`gpu-flag-${f}`}
                      className="text-[9px] px-1.5 py-0.5 rounded-full bg-cyan-500/15 text-cyan-200 font-medium"
                    >
                      {f}
                    </span>
                  ))}
                </div>
              )}
            </div>
          ) : (
            <p className="text-[11px] text-shell-text-tertiary italic">No discrete GPU</p>
          )}
        </Section>

        {/* NPU */}
        <Section title="NPU" icon={<Zap size={14} className="text-slate-400" />}>
          {npu.type && npu.type !== "none" ? (
            <div className="grid grid-cols-2 md:grid-cols-3 gap-3">
              <LabelValue label="Type" value={npu.type} />
              <LabelValue label="Device" value={npu.device || "\u2014"} />
              <LabelValue label="TOPS" value={npu.tops ?? "\u2014"} />
              <LabelValue label="Cores" value={npu.cores ?? "\u2014"} />
            </div>
          ) : (
            <p className="text-[11px] text-shell-text-tertiary italic">No NPU</p>
          )}
        </Section>

        {/* Disk */}
        <Section title="Disk" icon={<HardDrive size={14} className="text-amber-400" />}>
          <div className="grid grid-cols-2 md:grid-cols-3 gap-3">
            <LabelValue label="Total" value={disk.total_gb ? `${disk.total_gb} GB` : "\u2014"} />
            <LabelValue label="Free" value={disk.free_gb ? `${disk.free_gb} GB` : "\u2014"} />
            <LabelValue label="Type" value={disk.type || "\u2014"} />
          </div>
        </Section>

        {/* OS */}
        <Section title="Operating System" icon={<Monitor size={14} className="text-emerald-400" />}>
          <div className="grid grid-cols-2 md:grid-cols-3 gap-3">
            <LabelValue label="Distro" value={os.distro || "\u2014"} />
            <LabelValue label="Version" value={os.version || "\u2014"} />
            <LabelValue label="Kernel" value={os.kernel || "\u2014"} />
          </div>
        </Section>

        {/* Backends */}
        <Section title={`Backends (${backends.length})`} icon={<Server size={14} className="text-sky-400" />}>
          {backends.length === 0 ? (
            <p className="text-[11px] text-shell-text-tertiary italic">No backends reported</p>
          ) : (
            <div className="space-y-2">
              {backends.map((b, i) => (
                <div
                  key={`detail-b-${i}`}
                  className="p-2 rounded-md bg-white/[0.02] border border-white/5"
                >
                  <div className="flex items-center justify-between gap-2">
                    <span className="text-[12px] font-medium text-shell-text truncate">
                      {normalizeBackendName(b.name ?? b.type ?? "backend")}
                    </span>
                    {b.type && (
                      <span className="text-[10px] text-shell-text-tertiary">{b.type}</span>
                    )}
                  </div>
                  {(b.runtime || b.runtime_version) && (
                    <div className="text-[10px] text-shell-text-tertiary mt-0.5">
                      {b.runtime} {b.runtime_version}
                    </div>
                  )}
                  {Array.isArray(b.capabilities) && b.capabilities.length > 0 && (
                    <div className="mt-1 flex flex-wrap gap-1">
                      {b.capabilities.map((c) => (
                        <span
                          key={`b-${i}-cap-${c}`}
                          className="text-[9px] px-1.5 py-0.5 rounded-full bg-cyan-500/15 text-cyan-200 font-medium"
                        >
                          {c}
                        </span>
                      ))}
                    </div>
                  )}
                  {Array.isArray(b.models) && b.models.length > 0 && (
                    <div className="mt-1.5 text-[10px] text-shell-text-tertiary">
                      {b.models.length} model{b.models.length === 1 ? "" : "s"}:{" "}
                      {b.models
                        .map((m) => m.name ?? m.id ?? "")
                        .filter(Boolean)
                        .slice(0, 4)
                        .join(", ")}
                      {b.models.length > 4 ? "\u2026" : ""}
                    </div>
                  )}
                </div>
              ))}
            </div>
          )}
        </Section>

        {/* Models */}
        <Section
          title={`Models (${models.length})`}
          icon={<MemoryStick size={14} className="text-pink-400" />}
        >
          {models.length === 0 ? (
            <p className="text-[11px] text-shell-text-tertiary italic">No models loaded</p>
          ) : (
            <div className="flex flex-wrap gap-1">
              {models.map((m) => (
                <span
                  key={`detail-m-${m}`}
                  className="text-[10px] px-2 py-0.5 rounded-full bg-pink-500/15 text-pink-200 font-medium"
                >
                  {m}
                </span>
              ))}
            </div>
          )}
        </Section>

        {/* Capabilities */}
        <Section
          title={`Capabilities (${capabilities.length} active${latentCaps.length > 0 ? ` · ${latentCaps.length} potential` : ""})`}
          icon={<Zap size={14} className="text-cyan-400" />}
        >
          {capabilities.length === 0 && latentCaps.length === 0 ? (
            <p className="text-[11px] text-shell-text-tertiary italic">No capabilities reported</p>
          ) : (
            <div className="space-y-2.5">
              {capabilities.length > 0 && (
                <div>
                  <p className="text-[10px] uppercase tracking-wide text-shell-text-tertiary mb-1.5">
                    Active capabilities
                  </p>
                  <div className="flex flex-wrap gap-1">
                    {capabilities.map((c) => (
                      <span
                        key={`detail-cap-${c}`}
                        className="text-[10px] px-2 py-0.5 rounded-full bg-cyan-500/15 text-cyan-200 font-medium"
                        aria-label={`Current capability: ${c}`}
                      >
                        {c}
                      </span>
                    ))}
                  </div>
                </div>
              )}
              {latentCaps.length > 0 && (
                <div>
                  <p className="text-[10px] uppercase tracking-wide text-shell-text-tertiary mb-1.5">
                    Hardware can support
                  </p>
                  <div className="flex flex-wrap gap-1">
                    {latentCaps.map((c) => (
                      <span
                        key={`detail-pot-${c}`}
                        className="text-[10px] px-2 py-0.5 rounded-full bg-white/[0.03] border border-white/10 text-shell-text-tertiary font-medium"
                        aria-label={`Potential capability: ${c}`}
                        title="Hardware can support this — install a model with this capability to enable it"
                      >
                        {c}
                      </span>
                    ))}
                  </div>
                </div>
              )}
            </div>
          )}
        </Section>

        {/* Actions */}
        <Section title="Actions" icon={<Wand2 size={14} className="text-white/70" />}>
          <div className="flex flex-wrap gap-2">
            <Button size="sm" variant="outline" onClick={onRefresh} aria-label="Refresh cluster workers">
              <RefreshCw size={13} />
              Refresh
            </Button>
            <a
              href={worker.url}
              target="_blank"
              rel="noopener noreferrer"
              className="inline-flex items-center gap-1.5 text-xs px-3 h-8 rounded-lg border border-white/10 bg-white/5 text-shell-text-secondary hover:bg-white/10 transition-colors"
              aria-label={`Open worker ${worker.name} URL`}
            >
              <ExternalLink size={13} />
              Open worker URL
            </a>
            <Button
              size="sm"
              variant="outline"
              onClick={() => copy("name", worker.name)}
              aria-label="Copy worker name"
            >
              {copied === "name" ? <Check size={13} /> : <Copy size={13} />}
              {copied === "name" ? "Copied" : "Copy name"}
            </Button>
            <Button
              size="sm"
              variant="outline"
              onClick={() => copy("url", worker.url)}
              aria-label="Copy worker URL"
            >
              {copied === "url" ? <Check size={13} /> : <Copy size={13} />}
              {copied === "url" ? "Copied" : "Copy URL"}
            </Button>
            <Button
              size="sm"
              variant="outline"
              onClick={onOptimise}
              disabled={busy}
              aria-label="Run cluster optimiser"
              title="Ask the controller to analyse the mesh and suggest rebalancing"
            >
              <Wand2 size={13} />
              Optimise cluster
            </Button>
            <Button
              size="sm"
              variant="outline"
              disabled
              aria-label="Drain worker (coming soon)"
              title="Coming soon \u2014 no backend endpoint yet"
            >
              <X size={13} />
              Drain
            </Button>
            <Button
              size="sm"
              variant="outline"
              disabled
              aria-label="Restart worker (coming soon)"
              title="Coming soon \u2014 no backend endpoint yet"
            >
              <RefreshCw size={13} />
              Restart
            </Button>
            <Button
              size="sm"
              variant="outline"
              onClick={() => onDeregister(worker.name)}
              disabled={busy}
              className="hover:bg-red-500/15 hover:text-red-300"
              aria-label={`Deregister worker ${worker.name}`}
              title="Remove this worker from the controller registry"
            >
              <Trash2 size={13} />
              Deregister
            </Button>
          </div>
        </Section>
      </div>
    </div>
  );
}

export function ClusterApp({ windowId: _windowId }: { windowId: string }) {
  const [workers, setWorkers] = useState<ClusterWorker[]>([]);
  const [selected, setSelected] = useState<string | null>(null);
  const [sortKey, setSortKey] = useState<SortKey>("name");
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(false);
  const [toast, setToast] = useState<string | null>(null);
  // True after user explicitly hits "back"; suppresses auto-select on refresh.
  const userNavigatedBack = useRef(false);

  const showToast = useCallback((msg: string) => {
    setToast(msg);
    window.setTimeout(() => setToast(null), 3000);
  }, []);

  const fetchWorkers = useCallback(async () => {
    try {
      const res = await fetch("/api/cluster/workers", { headers: { Accept: "application/json" } });
      if (res.ok) {
        const json = await res.json();
        if (Array.isArray(json)) {
          setWorkers(json as ClusterWorker[]);
          setSelected((cur) => {
            if (cur && json.some((w: ClusterWorker) => w.name === cur)) return cur;
            if (userNavigatedBack.current) return null;
            return json.length > 0 ? (json[0] as ClusterWorker).name : null;
          });
        }
      }
    } catch {
      /* ignore */
    }
    setLoading(false);
  }, []);

  useEffect(() => {
    fetchWorkers();
    const interval = setInterval(fetchWorkers, 10_000);
    return () => clearInterval(interval);
  }, [fetchWorkers]);

  const sortedWorkers = useMemo(() => {
    const list = [...workers];
    list.sort((a, b) => {
      if (sortKey === "name") return a.name.localeCompare(b.name);
      if (sortKey === "status") {
        return STATUS_ORDER[workerStatus(a)] - STATUS_ORDER[workerStatus(b)];
      }
      // last_seen: newer first
      const ah = a.last_heartbeat ?? 0;
      const bh = b.last_heartbeat ?? 0;
      return bh - ah;
    });
    return list;
  }, [workers, sortKey]);

  const selectedWorker = useMemo(
    () => sortedWorkers.find((w) => w.name === selected) ?? null,
    [sortedWorkers, selected],
  );

  const handleDeregister = useCallback(
    async (name: string) => {
      if (!window.confirm(`Deregister worker "${name}"? The worker can re-register via heartbeat.`)) {
        return;
      }
      setBusy(true);
      try {
        const res = await fetch(`/api/cluster/workers/${encodeURIComponent(name)}`, {
          method: "DELETE",
          headers: { Accept: "application/json" },
        });
        if (!res.ok) {
          let msg = `Deregister failed (${res.status})`;
          try {
            const err = await res.json();
            if (err?.error) msg = String(err.error);
          } catch {
            /* ignore */
          }
          showToast(msg);
        } else {
          showToast(`Worker "${name}" deregistered`);
          await fetchWorkers();
        }
      } catch (e) {
        showToast(e instanceof Error ? e.message : "Network error");
      }
      setBusy(false);
    },
    [fetchWorkers, showToast],
  );

  const handleOptimise = useCallback(async () => {
    setBusy(true);
    try {
      const res = await fetch("/api/cluster/optimise", { headers: { Accept: "application/json" } });
      if (res.ok) {
        showToast("Optimiser run complete");
      } else {
        showToast(`Optimiser failed (${res.status})`);
      }
    } catch (e) {
      showToast(e instanceof Error ? e.message : "Network error");
    }
    setBusy(false);
  }, [showToast]);

  return (
    <div className="flex flex-col h-full min-h-0 overflow-hidden bg-shell-bg text-shell-text select-none">
      {/* Toolbar */}
      <div className="flex items-center justify-between gap-2 px-4 py-3 border-b border-white/5 shrink-0">
        <div className="flex items-center gap-2 min-w-0">
          <Network size={18} className="text-accent shrink-0" />
          <h1 className="text-sm font-semibold shrink-0">Cluster</h1>
          <span className="text-xs text-shell-text-tertiary truncate">
            {workers.length} worker{workers.length === 1 ? "" : "s"}
          </span>
        </div>
        <div className="flex items-center gap-1.5 shrink-0">
          <label htmlFor="cluster-sort" className="sr-only">
            Sort by
          </label>
          <select
            id="cluster-sort"
            value={sortKey}
            onChange={(e) => setSortKey(e.target.value as SortKey)}
            className="h-8 rounded-md border border-white/10 bg-shell-bg-deep px-2 text-xs text-shell-text focus-visible:outline-none focus-visible:border-accent/40 focus-visible:ring-2 focus-visible:ring-accent/20 transition-colors"
            aria-label="Sort workers"
          >
            <option value="name">Name</option>
            <option value="status">Status</option>
            <option value="last_seen">Last seen</option>
          </select>
          <Button
            variant="ghost"
            size="icon"
            onClick={fetchWorkers}
            aria-label="Refresh worker list"
          >
            <RefreshCw size={14} />
          </Button>
        </div>
      </div>

      {/* Master-detail */}
      <div className="flex-1 min-h-0 overflow-hidden">
        <MobileSplitView
          listTitle="Cluster"
          detailTitle={selectedWorker?.name ?? ""}
          listWidth={288}
          selectedId={selected}
          onBack={() => { userNavigatedBack.current = true; setSelected(null); }}
          list={
            <div className="p-3 space-y-2" aria-label="Cluster worker list">
              {loading ? (
                <div className="text-[11px] text-shell-text-tertiary px-2 py-6 text-center">
                  Loading workers...
                </div>
              ) : sortedWorkers.length === 0 ? (
                <div className="flex flex-col items-center gap-2 py-6 text-center">
                  <p className="text-[11px] text-shell-text-tertiary">No workers registered yet.</p>
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
                sortedWorkers.map((w) => (
                  <WorkerListCard
                    key={w.name}
                    worker={w}
                    selected={selected === w.name}
                    onSelect={() => { userNavigatedBack.current = false; setSelected(w.name); }}
                  />
                ))
              )}
            </div>
          }
          detail={
            selectedWorker ? (
              <WorkerDetail
                worker={selectedWorker}
                onRefresh={fetchWorkers}
                onDeregister={handleDeregister}
                onOptimise={handleOptimise}
                busy={busy}
              />
            ) : (
              <div className="flex items-center justify-center h-full text-shell-text-tertiary text-sm">
                {loading ? "Loading..." : "No worker selected"}
              </div>
            )
          }
        />
      </div>

      {/* Toast */}
      {toast && (
        <div
          role="status"
          aria-live="polite"
          className="absolute bottom-4 left-1/2 -translate-x-1/2 px-3 py-2 rounded-lg bg-shell-surface border border-white/10 text-xs text-shell-text shadow-2xl"
        >
          {toast}
        </div>
      )}
    </div>
  );
}
