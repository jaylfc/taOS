import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { MobileSplitView } from "@/components/mobile/MobileSplitView";
import {
  Network, RefreshCw, ExternalLink, Copy, Check, Trash2, Wand2,
  Cpu, MemoryStick, HardDrive, CircuitBoard, Zap, Server, Monitor,
  X, Plus, Shield, ShieldOff, LogOut,
} from "lucide-react";
import { Button, Card, CardContent } from "@/components/ui";
import { ConfirmDialog } from "@/components/ConfirmDialog";
import type { ClusterWorker, ClusterDevice, WorkerStatus } from "@/lib/cluster";
import {
  workerStatus,
  workerHardwareSummary,
  workerShortIp,
  formatRelativeSeconds,
  normalizeBackendName,
  STATUS_PILL_CLASS,
  STATUS_LABEL,
} from "@/lib/cluster";
import { useRefreshOnFocus } from "@/hooks/use-refresh-on-focus";

type SortKey = "name" | "status" | "last_seen";
type Tab = "nodes" | "devices";

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

function DeviceListCard({
  device,
  selected,
  onSelect,
  onRevoke,
  onBlock,
  onUnblock,
}: {
  device: ClusterDevice;
  selected: boolean;
  onSelect: () => void;
  onRevoke: (d: ClusterDevice) => void;
  onBlock: (d: ClusterDevice) => void;
  onUnblock: (d: ClusterDevice) => void;
}) {
  const live = device.live_token;
  return (
    <button
      type="button"
      onClick={onSelect}
      aria-pressed={selected}
      aria-label={`Select device ${device.display_name || device.device_id}`}
      className={`w-full text-left p-2.5 rounded-lg border transition-colors ${
        selected
          ? "border-accent/50 bg-accent/10"
          : "border-white/5 bg-white/[0.02] hover:bg-white/[0.04]"
      }`}
    >
      <div className="flex items-center justify-between gap-2 mb-1">
        <div className="flex items-center gap-1.5 min-w-0">
          <span className="text-[12px] font-semibold text-shell-text truncate">
            {device.display_name || device.device_id}
          </span>
          <span className="text-[10px] text-shell-text-tertiary">
            {"\u00b7"} {device.platform}
          </span>
        </div>
        <span
          className={`text-[10px] px-1.5 py-0.5 rounded-full font-semibold border ${
            live
              ? "bg-emerald-500/15 text-emerald-300 border-emerald-500/25"
              : "bg-red-500/15 text-red-300 border-red-500/25"
          }`}
          aria-label={live ? "Token live" : "Token dead"}
        >
          {live ? "live" : "dead"}
        </span>
      </div>
      <div className="text-[10px] text-shell-text-tertiary truncate">
        last seen {formatRelativeSeconds(device.last_seen || device.registered_at)}
      </div>
      <div className="mt-1.5 flex gap-1">
         {device.live_token && (
          <Button
            size="sm"
            variant="outline"
            onClick={(e) => {
              e.stopPropagation();
              onRevoke(device);
            }}
            aria-label="Revoke device"
            className="hover:bg-red-500/15 hover:text-red-300"
            title="Sign this device out. Revoking cancels nothing already approved."
          >
            <LogOut size={13} />
            Revoke
          </Button>
        )}
        {device.blocked ? (
          <Button
            size="sm"
            variant="outline"
            onClick={(e) => {
              e.stopPropagation();
              onUnblock(device);
            }}
            aria-label="Unblock device"
            title="Allow this device to re-pair"
          >
            <ShieldOff size={13} />
            Unblock
          </Button>
        ) : (
          <Button
            size="sm"
            variant="outline"
            onClick={(e) => {
              e.stopPropagation();
              onBlock(device);
            }}
            aria-label="Block device"
            title="Block this device. It stays signed out and cannot re-pair until unblocked."
          >
            <Shield size={13} />
            Block
          </Button>
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
  onNodeRevoke,
  onNodeBlock,
  onNodeUnblock,
  busy,
}: {
  worker: ClusterWorker;
  onRefresh: () => void;
  onDeregister: (name: string) => Promise<void>;
  onOptimise: () => Promise<void>;
  onNodeRevoke: (name: string) => Promise<void>;
  onNodeBlock: (name: string) => Promise<void>;
  onNodeUnblock: (name: string) => Promise<void>;
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
              title="Coming soon -- no backend endpoint yet"
            >
              <RefreshCw size={13} />
              Restart
            </Button>
            {worker.blocked ? (
              <Button
                size="sm"
                variant="outline"
                onClick={() => onNodeUnblock(worker.name)}
                disabled={busy}
                aria-label={`Unblock node ${worker.name}`}
                title="Clear the block. The old signing key stays dead; the node must re-pair."
              >
                <ShieldOff size={13} />
                Unblock
              </Button>
            ) : (
              <>
                {worker.live_token !== false && (
                  <Button
                    size="sm"
                    variant="outline"
                    onClick={() => onNodeRevoke(worker.name)}
                    disabled={busy}
                    className="hover:bg-red-500/15 hover:text-red-300"
                    aria-label={`Revoke node ${worker.name}`}
                    title="Revoke this node's signing key. The node is signed out; nothing already approved is cancelled."
                  >
                    <LogOut size={13} />
                    Revoke
                  </Button>
                )}
                <Button
                  size="sm"
                  variant="outline"
                  onClick={() => onNodeBlock(worker.name)}
                  disabled={busy}
                  aria-label={`Block node ${worker.name}`}
                  title="Block this node. It stays signed out and cannot re-pair until unblocked."
                >
                  <Shield size={13} />
                  Block
                </Button>
              </>
            )}
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
          <p className="text-[10px] text-shell-text-tertiary mt-2">
            Revoking or blocking signs the node out -- it cannot authenticate
            again. This cancels nothing already approved: in-flight tasks and
            completed work are unaffected. Unblock (not re-pair) restores
            pairing if the node was blocked.
          </p>
        </Section>
      </div>
    </div>
  );
}

function DeviceDetail({
  device,
  onRevoke,
  onBlock,
  onUnblock,
  busy,
}: {
  device: ClusterDevice;
  onRevoke: (d: ClusterDevice) => void;
  onBlock: (d: ClusterDevice) => Promise<void>;
  onUnblock: (d: ClusterDevice) => Promise<void>;
  busy: boolean;
}) {
  const status = device.live_token ? "online" : "offline";
  return (
    <div className="flex flex-col h-full min-h-0 overflow-hidden">
      <div className="flex flex-wrap items-start justify-between gap-2 px-4 py-3 border-b border-white/5 shrink-0">
        <div className="min-w-0 flex-1">
          <div className="flex items-center gap-2 flex-wrap">
            <h2 className="text-sm font-semibold text-shell-text">
              {device.display_name || device.device_id}
            </h2>
            <StatusPill status={status} />
          </div>
          <p className="text-[11px] text-shell-text-tertiary mt-0.5 break-all">
            {device.platform}
            {device.last_seen !== undefined
              ? `  \u00b7  last seen ${formatRelativeSeconds(device.last_seen || device.registered_at)}`
              : ""}
          </p>
        </div>
      </div>
      <div className="flex-1 overflow-y-auto p-4 space-y-3">
        <Section title="Identity" icon={<Monitor size={14} className="text-emerald-400" />}>
          <div className="grid grid-cols-2 md:grid-cols-3 gap-3">
            <LabelValue label="Device ID" value={device.device_id} />
            <LabelValue label="Platform" value={device.platform} />
            <LabelValue
              label="Token"
              value={device.live_token ? "live" : "signed out"}
            />
            <LabelValue
              label="Blocked"
              value={device.blocked ? "yes" : "no"}
            />
            <LabelValue
              label="Last seen"
              value={formatRelativeSeconds(device.last_seen || device.registered_at)}
            />
            <LabelValue
              label="Registered"
              value={formatRelativeSeconds(device.registered_at)}
            />
          </div>
        </Section>
        <Section title="Actions" icon={<Wand2 size={14} className="text-white/70" />}>
          <div className="flex flex-wrap gap-2">
            {device.blocked ? (
              <Button
                size="sm"
                variant="outline"
                onClick={() => onUnblock(device)}
                disabled={busy}
                aria-label={`Unblock device ${device.device_id}`}
                title="Clear the block. The old token stays dead; the device must re-pair."
              >
                <ShieldOff size={13} />
                Unblock
              </Button>
            ) : (
              <>
                {device.live_token && (
                  <Button
                    size="sm"
                    variant="outline"
                    onClick={() => onRevoke(device)}
                    disabled={busy}
                    className="hover:bg-red-500/15 hover:text-red-300"
                    aria-label={`Revoke device ${device.device_id}`}
                    title="Revoke this device's token. The device is signed out; nothing already approved is cancelled."
                  >
                    <LogOut size={13} />
                    Revoke
                  </Button>
                )}
                <Button
                  size="sm"
                  variant="outline"
                  onClick={() => onBlock(device)}
                  disabled={busy}
                  aria-label={`Block device ${device.device_id}`}
                  title="Block this device. It stays signed out and cannot re-pair until unblocked."
                >
                  <Shield size={13} />
                  Block
                </Button>
              </>
            )}
          </div>
          <p className="text-[10px] text-shell-text-tertiary mt-2">
            Revoking or blocking signs the device out -- it cannot authenticate
            again. This cancels nothing already approved: active sessions and
            completed work are unaffected. A blocked device can re-pair once
            unblocked; a revoked-only device can re-pair freely.
          </p>
        </Section>
      </div>
    </div>
  );
}

export function ClusterApp({ windowId: _windowId }: { windowId: string }) {
  const [workers, setWorkers] = useState<ClusterWorker[]>([]);
  const [selected, setSelected] = useState<string | null>(null);
  const [devices, setDevices] = useState<ClusterDevice[]>([]);
  const [selectedDevice, setSelectedDevice] = useState<string | null>(null);
  const [activeTab, setActiveTab] = useState<Tab>("nodes");
  const [sortKey, setSortKey] = useState<SortKey>("name");
  const [loading, setLoading] = useState(true);
  const [devicesLoading, setDevicesLoading] = useState(false);
  const [busy, setBusy] = useState(false);
  const [toast, setToast] = useState<string | null>(null);
  const [confirmDialog, setConfirmDialog] = useState<{
    title: string;
    message: string;
    onConfirm: () => void;
  } | null>(null);
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

  const fetchDevices = useCallback(async () => {
    setDevicesLoading(true);
    try {
      const res = await fetch("/api/devices", { headers: { Accept: "application/json" } });
      if (res.ok) {
        const json = await res.json();
        if (Array.isArray(json.items)) {
          setDevices(json.items as ClusterDevice[]);
          setSelectedDevice((cur) => {
            if (cur && json.items.some((d: ClusterDevice) => d.device_id === cur))
              return cur;
            return json.items.length > 0 ? json.items[0].device_id : null;
          });
        }
      }
    } catch {
      /* ignore */
    }
    setDevicesLoading(false);
  }, []);

  useEffect(() => {
    if (activeTab === "devices") {
      fetchDevices();
    } else {
      fetchWorkers();
    }
  }, [activeTab, fetchDevices, fetchWorkers]);

  useRefreshOnFocus(
    activeTab === "devices" ? fetchDevices : fetchWorkers,
    1000,
  );

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

  // Manual (free-tier) Add worker: the worker shows a PIN, the user enters its
  // IP + PIN here, and the controller authorises that code so the worker's poll
  // can claim its key. No discovery -- the automated path is taOSgo.
  const [addOpen, setAddOpen] = useState(false);
  const [addIp, setAddIp] = useState("");
  const [addPin, setAddPin] = useState("");
  const [addBusy, setAddBusy] = useState(false);

  const submitAddWorker = useCallback(async () => {
    const ip = addIp.trim();
    const pin = addPin.trim();
    if (!ip || !pin) {
      showToast("Enter the worker IP and PIN");
      return;
    }
    setAddBusy(true);
    try {
      const res = await fetch("/api/cluster/pairing/manual", {
        method: "POST",
        headers: { "Content-Type": "application/json", Accept: "application/json" },
        body: JSON.stringify({ url: ip, code: pin }),
      });
      if (res.ok) {
        showToast("Worker authorised. It will connect shortly.");
        setAddOpen(false);
        setAddIp("");
        setAddPin("");
        fetchWorkers();
      } else {
        const j = await res.json().catch(() => ({}));
        showToast(j.error ? `Add worker failed: ${j.error}` : `Add worker failed (${res.status})`);
      }
    } catch (e) {
      showToast(e instanceof Error ? e.message : "Network error");
    }
    setAddBusy(false);
  }, [addIp, addPin, showToast, fetchWorkers]);

  const handleNodeRevoke = useCallback(
    async (name: string) => {
      setBusy(true);
      try {
        const res = await fetch(`/api/cluster/workers/${encodeURIComponent(name)}/revoke`, {
          method: "POST",
        });
        if (res.ok) {
          showToast(`Node "${name}" revoked`);
          fetchWorkers();
        } else {
          const j = await res.json().catch(() => ({}));
          showToast(j.error ? `Revoke failed: ${j.error}` : `Revoke failed (${res.status})`);
        }
      } catch (e) {
        showToast(e instanceof Error ? e.message : "Network error");
      }
      setBusy(false);
    },
    [fetchWorkers, showToast],
  );

  const handleNodeBlock = useCallback(
    async (name: string) => {
      setBusy(true);
      try {
        const res = await fetch(`/api/cluster/workers/${encodeURIComponent(name)}/block`, {
          method: "POST",
        });
        if (res.ok) {
          showToast(`Node "${name}" blocked`);
          fetchWorkers();
        } else {
          const j = await res.json().catch(() => ({}));
          showToast(j.error ? `Block failed: ${j.error}` : `Block failed (${res.status})`);
        }
      } catch (e) {
        showToast(e instanceof Error ? e.message : "Network error");
      }
      setBusy(false);
    },
    [fetchWorkers, showToast],
  );

  const handleNodeUnblock = useCallback(
    async (name: string) => {
      setBusy(true);
      try {
        const res = await fetch(`/api/cluster/workers/${encodeURIComponent(name)}/unblock`, {
          method: "POST",
        });
        if (res.ok) {
          showToast(`Node "${name}" unblocked`);
          fetchWorkers();
        } else {
          const j = await res.json().catch(() => ({}));
          showToast(j.error ? `Unblock failed: ${j.error}` : `Unblock failed (${res.status})`);
        }
      } catch (e) {
        showToast(e instanceof Error ? e.message : "Network error");
      }
      setBusy(false);
    },
    [fetchWorkers, showToast],
  );

  const handleDeviceRevoke = useCallback(
    (device: ClusterDevice) => {
      setConfirmDialog({
        title: `Revoke ${device.display_name || device.device_id}?`,
        message: "This signs the device out. Nothing already approved is cancelled.",
        onConfirm: async () => {
          setBusy(true);
          try {
            const res = await fetch(
              `/api/devices/${encodeURIComponent(device.device_id)}`,
              { method: "DELETE" },
            );
            if (res.ok) {
              showToast("Device revoked");
              fetchDevices();
            } else {
              const j = await res.json().catch(() => ({}));
              showToast(j.error ? `Revoke failed: ${j.error}` : `Revoke failed (${res.status})`);
            }
          } catch (e) {
            showToast(e instanceof Error ? e.message : "Network error");
          }
          setBusy(false);
          setConfirmDialog(null);
        },
      });
    },
    [fetchDevices, showToast],
  );

  const handleDeviceBlock = useCallback(
    async (device: ClusterDevice) => {
      setBusy(true);
      try {
        const res = await fetch(
          `/api/devices/${encodeURIComponent(device.device_id)}/block`,
          { method: "POST" },
        );
        if (res.ok) {
          showToast("Device blocked");
          fetchDevices();
        } else {
          const j = await res.json().catch(() => ({}));
          showToast(j.error ? `Block failed: ${j.error}` : `Block failed (${res.status})`);
        }
      } catch (e) {
        showToast(e instanceof Error ? e.message : "Network error");
      }
      setBusy(false);
    },
    [fetchDevices, showToast],
  );

  const handleDeviceUnblock = useCallback(
    async (device: ClusterDevice) => {
      setBusy(true);
      try {
        const res = await fetch(
          `/api/devices/${encodeURIComponent(device.device_id)}/unblock`,
          { method: "POST" },
        );
        if (res.ok) {
          showToast("Device unblocked");
          fetchDevices();
        } else {
          const j = await res.json().catch(() => ({}));
          showToast(j.error ? `Unblock failed: ${j.error}` : `Unblock failed (${res.status})`);
        }
      } catch (e) {
        showToast(e instanceof Error ? e.message : "Network error");
      }
      setBusy(false);
    },
    [fetchDevices, showToast],
  );

  return (
    <div className="flex flex-col h-full min-h-0 overflow-hidden bg-shell-bg text-shell-text select-none">
       {/* Toolbar */}
      <div className="flex items-center justify-between gap-2 px-4 py-3 border-b border-white/5 shrink-0">
        <div className="flex items-center gap-2 min-w-0">
          <Network size={18} className="text-accent shrink-0" />
          <h1 className="text-sm font-semibold shrink-0">Cluster</h1>
          <div className="flex items-center gap-0.5 text-xs bg-white/[0.04] rounded-md border border-white/10 p-0.5">
            <button
              type="button"
              onClick={() => {
                setActiveTab("nodes");
                setSelected(null);
                setSelectedDevice(null);
              }}
              className={`px-2 py-1 rounded text-xs font-medium transition-colors ${
                activeTab === "nodes"
                  ? "bg-accent text-white"
                  : "text-shell-text-tertiary hover:text-shell-text hover:bg-white/[0.06]"
              }`}
              aria-label="Show nodes"
            >
              Nodes
            </button>
            <button
              type="button"
              onClick={() => {
                setActiveTab("devices");
                setSelected(null);
                setSelectedDevice(null);
              }}
              className={`px-2 py-1 rounded text-xs font-medium transition-colors ${
                activeTab === "devices"
                  ? "bg-accent text-white"
                  : "text-shell-text-tertiary hover:text-shell-text hover:bg-white/[0.06]"
              }`}
              aria-label="Show devices"
            >
              Devices
            </button>
          </div>
          {activeTab === "nodes" && (
            <span className="text-xs text-shell-text-tertiary truncate">
              {workers.length} worker{workers.length === 1 ? "" : "s"}
            </span>
          )}
          {activeTab === "devices" && (
            <span className="text-xs text-shell-text-tertiary truncate">
              {devices.length} device{devices.length === 1 ? "" : "s"}
            </span>
          )}
        </div>
        <div className="flex items-center gap-1.5 shrink-0">
          {activeTab === "nodes" && (
            <Button
              variant="ghost"
              size="sm"
              onClick={() => setAddOpen(true)}
              aria-label="Add a worker"
              className="gap-1.5"
            >
              <Plus size={14} />
              Add worker
            </Button>
          )}
          {activeTab === "nodes" && (
            <>
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
            </>
          )}
          <Button
            variant="ghost"
            size="icon"
            onClick={activeTab === "devices" ? fetchDevices : fetchWorkers}
            aria-label={activeTab === "devices" ? "Refresh device list" : "Refresh worker list"}
          >
            <RefreshCw size={14} />
          </Button>
        </div>
      </div>

      {/* Master-detail */}
      <div className="flex-1 min-h-0 overflow-hidden">
        <MobileSplitView
          listTitle={activeTab === "devices" ? "Cluster devices" : "Cluster"}
          detailTitle={activeTab === "devices" ? selectedDevice ?? "" : selectedWorker?.name ?? ""}
          listWidth={288}
          selectedId={activeTab === "devices" ? selectedDevice : selected}
          onBack={() => {
            userNavigatedBack.current = true;
            if (activeTab === "devices") setSelectedDevice(null);
            else setSelected(null);
          }}
          list={
            activeTab === "devices" ? (
              <div className="p-3 space-y-2" aria-label="Cluster device list">
                {devicesLoading ? (
                  <div className="text-[11px] text-shell-text-tertiary px-2 py-6 text-center">
                    Loading devices...
                  </div>
                ) : devices.length === 0 ? (
                  <div className="flex flex-col items-center gap-2 py-6 text-center">
                    <p className="text-[11px] text-shell-text-tertiary">No devices paired yet.</p>
                  </div>
                ) : (
                  devices
                    .slice()
                    .sort((a, b) => (a.display_name || a.device_id).localeCompare(b.display_name || b.device_id))
                    .map((d) => (
                      <DeviceListCard
                        key={d.device_id}
                        device={d}
                        selected={selectedDevice === d.device_id}
                        onSelect={() => {
                          userNavigatedBack.current = false;
                          setSelectedDevice(d.device_id);
                        }}
                        onRevoke={handleDeviceRevoke}
                        onBlock={handleDeviceBlock}
                        onUnblock={handleDeviceUnblock}
                      />
                    ))
                )}
              </div>
            ) : (
              <div className="p-3 space-y-2" aria-label="Cluster worker list">
                {loading ? (
                  <div className="text-[11px] text-shell-text-tertiary px-2 py-6 text-center">
                    Loading workers...
                  </div>
                ) : sortedWorkers.length === 0 ? (
                  <div className="flex flex-col items-center gap-2 py-6 text-center">
                    <p className="text-[11px] text-shell-text-tertiary">No workers registered yet.</p>
                    <Button
                      variant="secondary"
                      size="sm"
                      onClick={() => setAddOpen(true)}
                      className="gap-1.5"
                      aria-label="Add a worker"
                    >
                      <Plus size={14} />
                      Add worker
                    </Button>
                    <a
                      href="https://github.com/jaylfc/tinyagentos#distributed-compute-cluster"
                      target="_blank"
                      rel="noopener noreferrer"
                      className="text-[10px] text-shell-text-tertiary hover:text-shell-text-secondary underline underline-offset-2"
                      aria-label="How to add a worker (opens docs in new tab)"
                    >
                      or read the docs
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
            )
          }
          detail={
            activeTab === "devices" ? (
              selectedDevice ? (
                <DeviceDetail
                  device={devices.find((d) => d.device_id === selectedDevice) as ClusterDevice}
                  onRevoke={handleDeviceRevoke}
                  onBlock={handleDeviceBlock}
                  onUnblock={handleDeviceUnblock}
                  busy={busy}
                />
              ) : (
                <div className="flex items-center justify-center h-full text-shell-text-tertiary text-sm">
                  {devicesLoading ? "Loading..." : "No device selected"}
                </div>
              )
            ) : selectedWorker ? (
              <WorkerDetail
                worker={selectedWorker}
                onRefresh={fetchWorkers}
                onDeregister={handleDeregister}
                onOptimise={handleOptimise}
                onNodeRevoke={handleNodeRevoke}
                onNodeBlock={handleNodeBlock}
                onNodeUnblock={handleNodeUnblock}
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

      {/* Add worker (manual / free-tier) modal */}
      {addOpen && (
        <div
          className="absolute inset-0 z-50 flex items-center justify-center bg-black/50 p-4"
          role="dialog"
          aria-modal="true"
          aria-label="Add a worker"
          onClick={() => !addBusy && setAddOpen(false)}
        >
          <div
            className="w-full max-w-sm rounded-xl border border-white/10 bg-shell-bg-deep p-5 shadow-2xl"
            onClick={(e) => e.stopPropagation()}
          >
            <div className="flex items-center justify-between mb-3">
              <h3 className="text-sm font-semibold text-shell-text">Add a worker</h3>
              <Button variant="ghost" size="icon" onClick={() => setAddOpen(false)} aria-label="Close">
                <X size={14} />
              </Button>
            </div>
            <p className="text-[11px] text-shell-text-tertiary mb-3 leading-relaxed">
              Install the worker on the other machine. It shows a PIN. Enter that machine's
              IP and the PIN below, then it joins the cluster. For one-tap setup from anywhere,
              taOSgo handles this automatically.
            </p>
            <label className="block text-[10px] uppercase tracking-wide text-shell-text-tertiary mb-1" htmlFor="add-worker-ip">
              Worker IP address
            </label>
            <input
              id="add-worker-ip"
              value={addIp}
              onChange={(e) => setAddIp(e.target.value)}
              placeholder="192.168.1.50"
              autoComplete="off"
              className="w-full h-9 mb-3 rounded-md border border-white/10 bg-shell-bg px-2.5 text-sm text-shell-text focus-visible:outline-none focus-visible:border-accent/40 focus-visible:ring-2 focus-visible:ring-accent/20"
            />
            <label className="block text-[10px] uppercase tracking-wide text-shell-text-tertiary mb-1" htmlFor="add-worker-pin">
              Pairing PIN
            </label>
            <input
              id="add-worker-pin"
              value={addPin}
              onChange={(e) => setAddPin(e.target.value)}
              placeholder="shown on the worker"
              autoComplete="off"
              onKeyDown={(e) => { if (e.key === "Enter") submitAddWorker(); }}
              className="w-full h-9 mb-4 rounded-md border border-white/10 bg-shell-bg px-2.5 text-sm font-mono tracking-widest text-shell-text focus-visible:outline-none focus-visible:border-accent/40 focus-visible:ring-2 focus-visible:ring-accent/20"
            />
            <div className="flex justify-end gap-2">
              <Button variant="ghost" size="sm" onClick={() => setAddOpen(false)} disabled={addBusy}>
                Cancel
              </Button>
              <Button variant="default" size="sm" onClick={submitAddWorker} disabled={addBusy}>
                {addBusy ? "Authorising..." : "Add worker"}
              </Button>
            </div>
          </div>
        </div>
      )}

      {confirmDialog && (
        <ConfirmDialog
          open={true}
          title={confirmDialog.title}
          message={confirmDialog.message}
          confirmLabel="Revoke"
          danger={true}
          onConfirm={confirmDialog.onConfirm}
          onCancel={() => setConfirmDialog(null)}
        />
      )}

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
