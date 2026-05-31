import { useState, useEffect, useCallback, useRef, type ReactNode } from "react";
import {
  Settings,
  HardDrive,
  Download,
  Upload,
  RefreshCw,
  Code,
  Info,
  Plus,
  Check,
  AlertCircle,
  ChevronLeft,
  Brain,
  Keyboard,
  Accessibility,
  Monitor,
  Users,
  Copy,
  Trash2,
  KeyRound,
  X,
  Palette,
} from "lucide-react";
import {
  Button,
  Card,
  Input,
  Label,
  Switch,
} from "@/components/ui";
import { useShortcuts } from "@/hooks/use-shortcut-registry";
import { useServerPreference } from "@/hooks/use-server-preference";
import { ThemesPanel } from "@/apps/SettingsApp/ThemesPanel";

/* ------------------------------------------------------------------ */
/*  Types                                                              */
/* ------------------------------------------------------------------ */

type Section = "system" | "storage" | "memory" | "backup" | "updates" | "advanced" | "shortcuts" | "accessibility" | "desktop" | "users" | "themes";

interface SectionDef {
  id: Section;
  label: string;
  icon: typeof Settings;
}

interface SystemInfo {
  cpu: string;
  ram: string;
  npu: string;
  gpu: string;
  disk: string;
  os: string;
}

interface StorageItem {
  label: string;
  size: string;
  bytes: number;
  maxBytes: number;
}

/* ------------------------------------------------------------------ */
/*  Constants                                                          */
/* ------------------------------------------------------------------ */

const SECTIONS: SectionDef[] = [
  { id: "system", label: "System Info", icon: Info },
  { id: "storage", label: "Storage", icon: HardDrive },
  { id: "memory", label: "Memory", icon: Brain },
  { id: "backup", label: "Backup & Restore", icon: Download },
  { id: "updates", label: "Updates", icon: RefreshCw },
  { id: "advanced", label: "Advanced", icon: Code },
  { id: "shortcuts", label: "Keyboard Shortcuts", icon: Keyboard },
  { id: "accessibility", label: "Accessibility", icon: Accessibility },
  { id: "desktop", label: "Desktop & Dock", icon: Monitor },
  { id: "users", label: "Users", icon: Users },
  { id: "themes", label: "Themes", icon: Palette },
];

const PLACEHOLDER_SYSTEM: SystemInfo = {
  cpu: "Detecting...",
  ram: "Detecting...",
  npu: "Detecting...",
  gpu: "Detecting...",
  disk: "Detecting...",
  os: "Detecting...",
};

const PLACEHOLDER_STORAGE: StorageItem[] = [
  { label: "Models", size: "--", bytes: 0, maxBytes: 1 },
  { label: "Data", size: "--", bytes: 0, maxBytes: 1 },
  { label: "App Catalog", size: "--", bytes: 0, maxBytes: 1 },
];

/* ------------------------------------------------------------------ */
/*  Helpers                                                            */
/* ------------------------------------------------------------------ */

async function safeFetch<T>(url: string, fallback: T): Promise<T> {
  try {
    const res = await fetch(url, { headers: { Accept: "application/json" } });
    if (!res.ok) return fallback;
    const ct = res.headers.get("content-type") ?? "";
    if (!ct.includes("application/json")) return fallback;
    return (await res.json()) as T;
  } catch {
    return fallback;
  }
}

function ProgressBar({ value, max }: { value: number; max: number }) {
  const pct = max > 0 ? Math.min(100, (value / max) * 100) : 0;
  return (
    <div className="h-2 w-full rounded-full bg-white/5" role="progressbar" aria-valuenow={pct} aria-valuemin={0} aria-valuemax={100}>
      <div
        className="h-full rounded-full bg-sky-500 transition-all"
        style={{ width: `${pct}%` }}
      />
    </div>
  );
}

/* ------------------------------------------------------------------ */
/*  System Info                                                        */
/* ------------------------------------------------------------------ */

function SystemInfoSection() {
  const [info, setInfo] = useState<SystemInfo>(PLACEHOLDER_SYSTEM);
  const [loading, setLoading] = useState(false);
  const [showRestartModal, setShowRestartModal] = useState(false);

  const detect = useCallback(async () => {
    setLoading(true);
    interface SysResp {
      hardware?: {
        cpu?: { arch?: string; model?: string; cores?: number; soc?: string };
        ram_mb?: number;
        gpu?: { type?: string; model?: string; vram_mb?: number };
        npu?: { type?: string; tops?: number; cores?: number };
        disk?: { total_gb?: number; type?: string };
        os?: { distro?: string; version?: string; kernel?: string };
      };
      resources?: {
        ram_total_mb?: number;
        disk_total_gb?: number;
      };
    }
    const data = await safeFetch<SysResp | null>("/api/system", null);
    if (data?.hardware || data?.resources) {
      const hw = data.hardware ?? {};
      const rs = data.resources ?? {};
      const ramMb = rs.ram_total_mb ?? hw.ram_mb ?? 0;
      const diskGb = rs.disk_total_gb ?? hw.disk?.total_gb ?? 0;
      const cpuModel = hw.cpu?.model ?? hw.cpu?.soc ?? "Unknown";
      const cpuCores = hw.cpu?.cores ? ` \u00d7 ${hw.cpu.cores}` : "";
      const cpuArch = hw.cpu?.arch ? ` (${hw.cpu.arch})` : "";
      const gpuModel = hw.gpu?.model || hw.gpu?.type || "None";
      const gpuVram =
        hw.gpu?.vram_mb && hw.gpu.vram_mb > 0
          ? ` (${(hw.gpu.vram_mb / 1024).toFixed(1)} GB)`
          : "";
      const npuType =
        hw.npu?.type && hw.npu.type !== "none" ? hw.npu.type : "None";
      const npuTops =
        hw.npu?.tops && hw.npu.tops > 0 ? ` \u00b7 ${hw.npu.tops} TOPS` : "";
      const diskType = hw.disk?.type ? ` ${hw.disk.type}` : "";
      const osParts = [hw.os?.distro, hw.os?.version].filter(Boolean);
      const osStr = osParts.length > 0 ? osParts.join(" ") : "\u2014";
      setInfo({
        cpu: `${cpuModel}${cpuCores}${cpuArch}`,
        ram:
          ramMb >= 1024
            ? `${(ramMb / 1024).toFixed(1)} GB`
            : ramMb > 0
              ? `${ramMb} MB`
              : "\u2014",
        npu: `${npuType}${npuTops}`,
        gpu: `${gpuModel}${gpuVram}`,
        disk: diskGb > 0 ? `${diskGb} GB${diskType}` : "\u2014",
        os: osStr,
      });
    } else {
      setInfo({
        cpu: "Unavailable",
        ram: "Unavailable",
        npu: "Unavailable",
        gpu: "Unavailable",
        disk: "Unavailable",
        os: "Unavailable",
      });
    }
    setLoading(false);
  }, []);

  useEffect(() => { detect(); }, [detect]);

  const rows: [string, string][] = [
    ["CPU", info.cpu],
    ["RAM", info.ram],
    ["NPU", info.npu],
    ["GPU", info.gpu],
    ["Disk", info.disk],
    ["OS", info.os],
  ];

  return (
    <section aria-label="System information">
      <h2 className="text-lg font-semibold mb-5">System Information</h2>
      <div className="rounded-2xl bg-white/[0.04] border border-white/[0.06] overflow-x-auto backdrop-blur-sm">
        <table className="w-full text-sm min-w-[360px]">
          <tbody>
            {rows.map(([label, value]) => (
              <tr key={label} className="border-b border-white/5 last:border-0">
                <td className="px-5 py-3 text-shell-text-secondary font-medium w-32">{label}</td>
                <td className="px-5 py-3">{value}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <div className="mt-3 flex items-center gap-2 flex-wrap">
        <Button
          variant="outline"
          size="sm"
          onClick={detect}
          disabled={loading}
        >
          <RefreshCw size={14} className={loading ? "animate-spin" : ""} />
          Re-detect Hardware
        </Button>
        <Button
          variant="outline"
          size="sm"
          onClick={async () => {
            setShowRestartModal(true);
            try {
              await fetch("/api/system/restart/prepare", { method: "POST" });
            } catch { /* modal polls status */ }
          }}
          aria-label="Restart taOS server"
        >
          <RefreshCw size={14} />
          Restart Server
        </Button>
      </div>
      <p className="mt-2 text-xs text-shell-text-tertiary">
        Restart the server to apply settings changes that require a reload.
      </p>
      {showRestartModal && (
        <RestartProgressModal onClose={() => setShowRestartModal(false)} />
      )}
    </section>
  );
}

/* ------------------------------------------------------------------ */
/*  Storage                                                            */
/* ------------------------------------------------------------------ */

function StorageSection() {
  const [items, setItems] = useState<StorageItem[]>(PLACEHOLDER_STORAGE);

  useEffect(() => {
    safeFetch<StorageItem[] | null>("/api/settings/storage", null).then((data) => {
      if (data && Array.isArray(data)) setItems(data);
      else
        setItems([
          { label: "Models", size: "4.2 GB", bytes: 4200, maxBytes: 32000 },
          { label: "Data", size: "1.8 GB", bytes: 1800, maxBytes: 32000 },
          { label: "App Catalog", size: "320 MB", bytes: 320, maxBytes: 32000 },
        ]);
    });
  }, []);

  return (
    <section aria-label="Storage usage">
      <h2 className="text-lg font-semibold mb-5">Storage Usage</h2>
      <div className="space-y-3">
        {items.map((item) => (
          <Card key={item.label} className="p-4">
            <div className="flex items-center justify-between mb-2">
              <span className="text-sm font-medium">{item.label}</span>
              <span className="text-sm text-shell-text-secondary tabular-nums">{item.size}</span>
            </div>
            <ProgressBar value={item.bytes} max={item.maxBytes} />
          </Card>
        ))}
      </div>
    </section>
  );
}

/* ------------------------------------------------------------------ */
/*  Memory Capture                                                     */
/* ------------------------------------------------------------------ */

interface MemorySettings {
  capture_conversations?: boolean;
  capture_notes?: boolean;
  capture_files?: boolean;
  capture_searches?: boolean;
  [key: string]: boolean | undefined;
}

const MEMORY_TOGGLES: { key: keyof MemorySettings; label: string; desc: string }[] = [
  { key: "capture_conversations", label: "Conversations", desc: "Messages you send to agents in the Message Hub" },
  { key: "capture_notes", label: "Notes", desc: "Notes from the Text Editor app" },
  { key: "capture_files", label: "File activity", desc: "Files you upload or open" },
  { key: "capture_searches", label: "Search queries", desc: "What you search for in global search" },
];

function MemorySection() {
  const [settings, setSettings] = useState<MemorySettings | null>(null);
  const [stats, setStats] = useState<{ total: number; collections: Record<string, number> } | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    fetch("/api/user-memory/settings")
      .then((r) => (r.ok ? r.json() : null))
      .then((data) => {
        if (data) setSettings(data);
        else setSettings({});
      })
      .catch(() => {
        setSettings({});
        setError("Could not load memory settings.");
      });

    fetch("/api/user-memory/stats")
      .then((r) => (r.ok ? r.json() : null))
      .then((data) => {
        if (data) setStats(data);
      })
      .catch(() => {});
  }, []);

  const update = (key: keyof MemorySettings, value: boolean) => {
    const next: MemorySettings = { ...(settings || {}), [key]: value };
    setSettings(next);
    fetch("/api/user-memory/settings", {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ [key]: value }),
    })
      .then((r) => {
        if (!r.ok) setError(`Failed to save setting (${r.status})`);
        else setError(null);
      })
      .catch(() => setError("Could not reach backend."));
  };

  if (!settings) {
    return (
      <section aria-label="Memory capture settings">
        <h2 className="text-lg font-semibold mb-5">Memory Capture</h2>
        <p className="text-sm text-shell-text-tertiary">Loading...</p>
      </section>
    );
  }

  return (
    <section aria-label="Memory capture settings">
      <h2 className="text-lg font-semibold mb-2">Memory Capture</h2>
      <p className="text-sm text-shell-text-tertiary mb-5">
        Choose what activity gets saved to your personal memory index. All data stays on this device.
      </p>

      {error && (
        <p className="mb-3 text-xs text-amber-400 flex items-center gap-1.5">
          <AlertCircle size={12} /> {error}
        </p>
      )}

      <div className="space-y-2">
        {MEMORY_TOGGLES.map((item) => {
          const checked = !!settings[item.key];
          const id = `capture-${String(item.key)}`;
          return (
            <Card key={String(item.key)} className="p-4 flex items-center justify-between gap-3">
              <div className="flex-1 min-w-0">
                <Label htmlFor={id} className="text-sm font-medium text-shell-text">
                  {item.label}
                </Label>
                <p className="text-xs text-shell-text-tertiary mt-0.5">{item.desc}</p>
              </div>
              <Switch
                id={id}
                checked={checked}
                onCheckedChange={(v) => update(item.key, v)}
                aria-label={`Capture ${item.label}`}
              />
            </Card>
          );
        })}
      </div>

      {stats && (
        <Card className="mt-6 p-4">
          <h3 className="text-sm font-medium mb-3">Stored chunks</h3>
          <div className="text-xs text-shell-text-secondary mb-2 tabular-nums">
            Total: {stats.total}
          </div>
          {Object.keys(stats.collections || {}).length > 0 ? (
            <ul className="space-y-1 text-xs text-shell-text-tertiary">
              {Object.entries(stats.collections).map(([name, count]) => (
                <li key={name} className="flex justify-between tabular-nums">
                  <span>{name}</span>
                  <span>{count}</span>
                </li>
              ))}
            </ul>
          ) : (
            <p className="text-xs text-shell-text-tertiary">No memories captured yet.</p>
          )}
        </Card>
      )}
    </section>
  );
}

/* ------------------------------------------------------------------ */
/*  Backup & Restore                                                   */
/* ------------------------------------------------------------------ */

function BackupSection() {
  const [backupStatus, setBackupStatus] = useState<string | null>(null);
  const [creating, setCreating] = useState(false);

  const createBackup = async () => {
    setCreating(true);
    setBackupStatus(null);
    try {
      const res = await fetch("/api/backup", { method: "POST" });
      if (res.ok) {
        setBackupStatus("Backup created successfully.");
      } else {
        setBackupStatus(`Backup failed (${res.status}). API may not be available yet.`);
      }
    } catch {
      setBackupStatus("Could not reach backup endpoint. API not available yet.");
    }
    setCreating(false);
  };

  return (
    <section aria-label="Backup and restore">
      <h2 className="text-lg font-semibold mb-5">Backup & Restore</h2>

      <Card className="p-4 space-y-4">
        <div>
          <h3 className="text-sm font-medium mb-2">Create Backup</h3>
          <p className="text-xs text-shell-text-tertiary mb-3">
            Export all agents, memory, and configuration as a backup archive.
          </p>
          <Button size="sm" onClick={createBackup} disabled={creating}>
            <Download size={14} className={creating ? "animate-bounce" : ""} />
            {creating ? "Creating..." : "Create Backup"}
          </Button>
          {backupStatus && (
            <p className={`mt-2 text-xs ${backupStatus.includes("success") ? "text-emerald-400" : "text-amber-400"}`}>
              {backupStatus}
            </p>
          )}
        </div>

        <hr className="border-white/5" />

        <div>
          <h3 className="text-sm font-medium mb-2">Restore from Backup</h3>
          <p className="text-xs text-shell-text-tertiary mb-3">
            Upload a previously created backup archive to restore.
          </p>
          <label className="flex flex-col items-center gap-2 p-6 rounded-lg border-2 border-dashed border-white/10 hover:border-white/20 transition-colors cursor-pointer">
            <Upload size={24} className="text-shell-text-tertiary" />
            <span className="text-xs text-shell-text-tertiary">Click to select a backup file</span>
            <input type="file" accept=".tar.gz,.zip,.bak" className="hidden" aria-label="Upload backup file" />
          </label>
        </div>
      </Card>
    </section>
  );
}

/* ------------------------------------------------------------------ */
/*  Updates                                                            */
/* ------------------------------------------------------------------ */

interface UpdateInfo {
  has_updates: boolean;
  current_version: string;
  current_commit: string;
  new_commit?: string | null;
}

interface AutoUpdatePrefs {
  check_enabled?: boolean;
}

interface UpdateStatus {
  current_sha: string;
  pending_restart_sha: string | null;
  auto_check: boolean;
}

interface RestartOrchestratorStatus {
  phase: string;
  reason: string;
  started_at: number;
  agents: Record<string, { status: string; duration_s: number; note_path: string | null }>;
}

function RestartProgressModal({
  onClose,
}: {
  onClose: () => void;
}) {
  const [orchStatus, setOrchStatus] = useState<RestartOrchestratorStatus | null>(null);
  const [serverBack, setServerBack] = useState(false);

  useEffect(() => {
    let cancelled = false;
    let pollOrch: ReturnType<typeof setInterval> | null = null;
    let pollServer: ReturnType<typeof setInterval> | null = null;
    let serverPollingStarted = false;

    const startServerPoll = () => {
      if (serverPollingStarted || cancelled) return;
      serverPollingStarted = true;
      if (pollOrch) clearInterval(pollOrch);
      pollServer = setInterval(async () => {
        if (cancelled) return;
        try {
          const r2 = await fetch("/api/settings/update-status");
          if (r2.ok) {
            setServerBack(true);
            if (pollServer) clearInterval(pollServer);
            setTimeout(() => {
              if (!cancelled) window.location.reload();
            }, 500);
          }
        } catch { /* server still restarting */ }
      }, 2000);
    };

    pollOrch = setInterval(async () => {
      if (cancelled) return;
      try {
        const r = await fetch("/api/system/restart/status");
        if (r.ok) {
          const data: RestartOrchestratorStatus = await r.json();
          setOrchStatus(data);
          // Switch to server-up polling once the restart is in flight.
          if (data.phase === "restarting") startServerPoll();
        }
      } catch {
        // Fetch failed — server has gone down. Start polling for it to come back.
        startServerPoll();
      }
    }, 1000);

    return () => {
      cancelled = true;
      if (pollOrch) clearInterval(pollOrch);
      if (pollServer) clearInterval(pollServer);
    };
  }, []);

  const agentEntries = orchStatus ? Object.entries(orchStatus.agents) : [];

  function agentChip(s: string) {
    if (s === "ready") return <span className="text-[10px] px-1.5 py-0.5 rounded bg-emerald-500/20 text-emerald-300">ready</span>;
    if (s === "timeout") return <span className="text-[10px] px-1.5 py-0.5 rounded bg-amber-500/20 text-amber-300">timeout</span>;
    if (s === "error") return <span className="text-[10px] px-1.5 py-0.5 rounded bg-red-500/20 text-red-300">error</span>;
    return (
      <span className="text-[10px] px-1.5 py-0.5 rounded bg-sky-500/20 text-sky-300 flex items-center gap-1">
        <RefreshCw size={10} className="animate-spin" />
        {s}
      </span>
    );
  }

  return (
    <div
      role="dialog"
      aria-modal="true"
      aria-label="Restart progress"
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/60"
    >
      <div className="bg-shell-surface border border-white/10 rounded-xl p-6 w-full max-w-md shadow-xl space-y-4">
        <h3 className="text-base font-semibold">
          {serverBack
            ? "Restarted — reloading…"
            : agentEntries.length > 0
            ? "Preparing agents for restart"
            : "Restarting server…"}
        </h3>

        {agentEntries.length > 0 && (
          <ul className="space-y-1" aria-label="Agent preparation status">
            {agentEntries.map(([name, info]) => (
              <li key={name} className="flex items-center justify-between text-sm">
                <span className="text-shell-text-secondary">{name}</span>
                {agentChip(info.status)}
              </li>
            ))}
          </ul>
        )}

        {orchStatus?.phase === "restarting" && !serverBack && (
          <p className="text-xs text-shell-text-tertiary">Waiting for server to come back…</p>
        )}

        {!orchStatus && (
          <p className="text-xs text-shell-text-tertiary flex items-center gap-1">
            <RefreshCw size={12} className="animate-spin" /> Connecting…
          </p>
        )}

        <div className="flex justify-end">
          <Button variant="outline" size="sm" onClick={onClose} aria-label="Cancel restart progress dialog">
            Cancel
          </Button>
        </div>
      </div>
    </div>
  );
}

function UpdatesSection() {
  const [checking, setChecking] = useState(false);
  const [applying, setApplying] = useState(false);
  const [rebuilding, setRebuilding] = useState(false);
  const [info, setInfo] = useState<UpdateInfo | null>(null);
  const [status, setStatus] = useState<string | null>(null);
  const [prefs, setPrefs] = useState<AutoUpdatePrefs>({ check_enabled: true });
  const [updateStatus, setUpdateStatus] = useState<UpdateStatus | null>(null);
  const [showRestartModal, setShowRestartModal] = useState(false);

  // Load current prefs + info on mount
  useEffect(() => {
    (async () => {
      try {
        const r = await fetch("/api/preferences/auto-update");
        if (r.ok) {
          const data = await r.json();
          if (data && typeof data === "object") {
            setPrefs({
              check_enabled: data.check_enabled ?? true,
            });
          }
        }
      } catch { /* ignore */ }
      try {
        const r2 = await fetch("/api/settings/update-check");
        if (r2.ok) setInfo(await r2.json());
      } catch { /* ignore */ }
      try {
        const r3 = await fetch("/api/settings/update-status");
        if (r3.ok) setUpdateStatus(await r3.json());
      } catch { /* ignore */ }
    })();
  }, []);

  const savePrefs = useCallback(async (next: AutoUpdatePrefs) => {
    setPrefs(next);
    try {
      await fetch("/api/preferences/auto-update", {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(next),
      });
    } catch { /* ignore network */ }
  }, []);

  const checkUpdates = async () => {
    setChecking(true);
    setStatus(null);
    try {
      const res = await fetch("/api/settings/update-check");
      if (res.ok) {
        const data = (await res.json()) as UpdateInfo;
        setInfo(data);
        setStatus(data.has_updates ? "A new version is available." : "You are up to date.");
      } else {
        setStatus("Update check not available.");
      }
    } catch {
      setStatus("Could not reach update server.");
    }
    setChecking(false);
  };

  const applyUpdate = async () => {
    setApplying(true);
    setStatus(null);
    try {
      const res = await fetch("/api/settings/update", { method: "POST" });
      if (res.ok) {
        // Server always restarts after a successful install — show the modal.
        setShowRestartModal(true);
      } else {
        const err = await res.json().catch(() => ({}));
        setStatus((err as { error?: string }).error ?? "Update failed.");
      }
    } catch {
      setStatus("Could not apply update.");
    }
    setApplying(false);
  };

  const rebuildFrontend = async () => {
    setRebuilding(true);
    setStatus(null);
    try {
      const res = await fetch("/api/settings/rebuild-frontend", { method: "POST" });
      const data = (await res.json().catch(() => ({}))) as { message?: string; error?: string };
      if (res.ok) {
        setStatus(data.message ?? "Frontend rebuilt — hard-refresh the browser to see changes.");
      } else {
        setStatus(data.error ?? "Frontend rebuild failed.");
      }
    } catch {
      setStatus("Could not reach rebuild endpoint.");
    }
    setRebuilding(false);
  };

  const triggerRestart = async () => {
    setShowRestartModal(true);
    try {
      await fetch("/api/system/restart/prepare", { method: "POST" });
    } catch { /* ignore — modal polls status */ }
  };

  const hasPendingRestart = !!updateStatus?.pending_restart_sha;

  return (
    <section aria-label="System updates">
      <h2 className="text-lg font-semibold mb-5">Updates</h2>

      {hasPendingRestart && (
        <div className="mb-4 flex items-center justify-between gap-3 rounded-lg border border-amber-500/30 bg-amber-500/10 px-4 py-3">
          <div className="flex items-center gap-2 text-sm text-amber-200">
            <AlertCircle size={16} className="shrink-0" />
            <span>Update pulled — restart to finish applying ({updateStatus!.pending_restart_sha!.slice(0, 7)})</span>
          </div>
          <Button size="sm" onClick={triggerRestart} aria-label="Restart server to apply update">
            Restart now
          </Button>
        </div>
      )}

      <Card className="p-4 space-y-4">
        <div className="flex items-center gap-3">
          <div className="p-2 rounded-lg bg-white/5 text-sky-400">
            <Settings size={20} />
          </div>
          <div className="flex-1 min-w-0">
            <p className="text-sm font-medium">taOS</p>
            {info?.has_updates && info.new_commit ? (
              <div className="flex flex-col gap-0.5">
                <p className="text-xs text-shell-text-tertiary tabular-nums">
                  <span className="text-white/40">installed </span>{info.current_commit}
                </p>
                <p className="text-xs text-amber-300/90 tabular-nums">
                  <span className="text-amber-300/50">available </span>{info.new_commit}
                </p>
              </div>
            ) : (
              <p className="text-xs text-shell-text-tertiary tabular-nums">
                {info?.current_commit ?? "v0.1.0-dev"}
              </p>
            )}
          </div>
          {info?.has_updates && (
            <span className="text-[10px] px-2 py-1 rounded-full font-semibold bg-amber-500/20 text-amber-300">
              Update available
            </span>
          )}
        </div>

        <div className="flex gap-2 flex-wrap">
          <Button variant="outline" size="sm" onClick={checkUpdates} disabled={checking}>
            <RefreshCw size={14} className={checking ? "animate-spin" : ""} />
            {checking ? "Checking..." : "Check Now"}
          </Button>
          {info?.has_updates ? (
            <Button size="sm" onClick={applyUpdate} disabled={applying}>
              <RefreshCw size={14} className={applying ? "animate-spin" : ""} />
              {applying ? "Installing..." : "Install Update"}
            </Button>
          ) : null}
          <Button
            variant="outline"
            size="sm"
            onClick={rebuildFrontend}
            disabled={rebuilding}
            aria-label="Force rebuild of the desktop frontend bundle"
            title="Rebuild the desktop bundle from source. Use after a source-only update or when the UI looks stale."
          >
            <RefreshCw size={14} className={rebuilding ? "animate-spin" : ""} />
            {rebuilding ? "Rebuilding..." : "Rebuild Frontend"}
          </Button>
        </div>

        {status && (
          <div className="flex items-start gap-2 text-xs">
            {status.includes("up to date") || status.includes("applied") ? (
              <Check size={14} className="text-emerald-400 shrink-0 mt-0.5" />
            ) : (
              <AlertCircle size={14} className="text-amber-400 shrink-0 mt-0.5" />
            )}
            <span className="text-shell-text-secondary">{status}</span>
          </div>
        )}

        <div className="border-t border-white/5 pt-4 space-y-3">
          <div className="flex items-center justify-between gap-3">
            <div className="flex-1 min-w-0">
              <Label className="text-sm">Check for updates automatically</Label>
              <p className="text-[11px] text-shell-text-tertiary mt-0.5">
                Polls GitHub hourly and notifies when a new version is available.
              </p>
            </div>
            <Switch
              checked={prefs.check_enabled ?? true}
              onCheckedChange={(v) => savePrefs({ ...prefs, check_enabled: v })}
            />
          </div>

        </div>
      </Card>

      {showRestartModal && (
        <RestartProgressModal onClose={() => setShowRestartModal(false)} />
      )}
    </section>
  );
}

/* ------------------------------------------------------------------ */
/*  Advanced                                                           */
/* ------------------------------------------------------------------ */

function AdvancedSection() {
  return (
    <section aria-label="Advanced configuration">
      <h2 className="text-lg font-semibold mb-5">Advanced Configuration</h2>
      <Card className="p-4 space-y-4 text-sm">
        <div>
          <h3 className="font-medium mb-1">Providers (LLMs, embeddings, NPU backends)</h3>
          <p className="text-muted-foreground">
            Configure model providers in the <span className="font-medium">Providers</span> app
            (Launchpad → Providers). That's where API keys, base URLs, and per-provider
            settings live.
          </p>
        </div>
        <div>
          <h3 className="font-medium mb-1">Raw server config</h3>
          <p className="text-muted-foreground">
            Advanced settings are stored in <code className="font-mono text-xs px-1 py-0.5 rounded bg-muted">data/config.yaml</code> on
            the server. Edit the file directly and restart taOS to apply changes — there is
            no in-app YAML editor.
          </p>
        </div>
      </Card>
    </section>
  );
}

/* ------------------------------------------------------------------ */
/*  Keyboard Shortcuts                                                 */
/* ------------------------------------------------------------------ */

function KeyboardShortcutsSection() {
  const { getAll, keyboardLockActive } = useShortcuts();
  const shortcuts = getAll();

  return (
    <section aria-label="Keyboard shortcuts">
      <h2 className="text-lg font-semibold mb-1">Keyboard Shortcuts</h2>
      <p className="text-sm text-shell-text-tertiary mb-5">View and manage keyboard shortcuts</p>

      <div className="rounded-2xl bg-white/[0.04] border border-white/[0.06] overflow-x-auto backdrop-blur-sm">
        <table className="w-full text-sm min-w-[360px]">
          <thead>
            <tr className="border-b border-white/[0.08]">
              <th className="px-5 py-3 text-left text-xs font-semibold text-shell-text-secondary uppercase tracking-wider">Shortcut</th>
              <th className="px-5 py-3 text-left text-xs font-semibold text-shell-text-secondary uppercase tracking-wider">Action</th>
              <th className="px-5 py-3 text-left text-xs font-semibold text-shell-text-secondary uppercase tracking-wider">Scope</th>
            </tr>
          </thead>
          <tbody>
            {shortcuts.length === 0 ? (
              <tr>
                <td colSpan={3} className="px-5 py-4 text-sm text-shell-text-tertiary">No shortcuts registered.</td>
              </tr>
            ) : (
              shortcuts.map((s, i) => (
                <tr key={i} className="border-b border-white/5 last:border-0">
                  <td className="px-5 py-3 font-mono text-xs text-sky-300">{s.combo}</td>
                  <td className="px-5 py-3">{s.label}</td>
                  <td className="px-5 py-3 text-shell-text-tertiary capitalize">{s.scope}</td>
                </tr>
              ))
            )}
          </tbody>
        </table>
      </div>

      <p className={`mt-4 text-sm font-medium ${keyboardLockActive ? "text-emerald-400" : "text-shell-text-tertiary"}`}>
        Keyboard lock: {keyboardLockActive ? "Active" : "Inactive"}
      </p>
      <p className="mt-1 text-xs text-shell-text-tertiary">
        Full keyboard capture requires fullscreen mode in Chrome or Edge
      </p>
    </section>
  );
}

/* ------------------------------------------------------------------ */
/*  Accessibility                                                      */
/* ------------------------------------------------------------------ */

function AccessibilitySection() {
  const [reduceMotion, setReduceMotion] = useState(
    () => localStorage.getItem("taos-reduce-motion") === "true"
  );
  const [highContrast, setHighContrast] = useState(
    () => localStorage.getItem("taos-high-contrast") === "true"
  );
  const [fontSize, setFontSize] = useState(
    () => localStorage.getItem("taos-font-size") ?? "medium"
  );
  const [focusMode, setFocusMode] = useState(
    () => localStorage.getItem("taos-focus-mode") ?? "keyboard"
  );

  const toggleReduceMotion = () => {
    const next = !reduceMotion;
    setReduceMotion(next);
    localStorage.setItem("taos-reduce-motion", String(next));
    document.documentElement.classList.toggle("reduce-motion", next);
  };

  const toggleHighContrast = () => {
    const next = !highContrast;
    setHighContrast(next);
    localStorage.setItem("taos-high-contrast", String(next));
    document.documentElement.classList.toggle("high-contrast", next);
  };

  const applyFontSize = (size: string) => {
    setFontSize(size);
    localStorage.setItem("taos-font-size", size);
    const sizeMap: Record<string, string> = { small: "14px", medium: "16px", large: "18px" };
    document.documentElement.style.fontSize = sizeMap[size] ?? "16px";
  };

  const applyFocusMode = (mode: string) => {
    setFocusMode(mode);
    localStorage.setItem("taos-focus-mode", mode);
    document.documentElement.classList.toggle("focus-always", mode === "always");
  };

  return (
    <section aria-label="Accessibility settings">
      <h2 className="text-lg font-semibold mb-5">Accessibility</h2>

      <div className="space-y-3">
        <Card className="p-4 flex items-center justify-between gap-3">
          <div className="flex-1 min-w-0">
            <Label htmlFor="reduce-motion" className="text-sm font-medium text-shell-text">
              Reduce motion
            </Label>
            <p className="text-xs text-shell-text-tertiary mt-0.5">Minimize animations and transitions</p>
          </div>
          <Switch
            id="reduce-motion"
            checked={reduceMotion}
            onCheckedChange={toggleReduceMotion}
            aria-label="Reduce motion"
          />
        </Card>

        <Card className="p-4 flex items-center justify-between gap-3">
          <div className="flex-1 min-w-0">
            <Label htmlFor="high-contrast" className="text-sm font-medium text-shell-text">
              High contrast
            </Label>
            <p className="text-xs text-shell-text-tertiary mt-0.5">Increase contrast for better visibility</p>
          </div>
          <Switch
            id="high-contrast"
            checked={highContrast}
            onCheckedChange={toggleHighContrast}
            aria-label="High contrast"
          />
        </Card>

        <Card className="p-4">
          <p className="text-sm font-medium mb-3">Font size</p>
          <div className="flex gap-2" role="group" aria-label="Font size">
            {(["small", "medium", "large"] as const).map((size) => (
              <Button
                key={size}
                variant={fontSize === size ? "secondary" : "outline"}
                size="sm"
                onClick={() => applyFontSize(size)}
                aria-pressed={fontSize === size}
              >
                {size.charAt(0).toUpperCase() + size.slice(1)}
              </Button>
            ))}
          </div>
        </Card>

        <Card className="p-4">
          <p className="text-sm font-medium mb-3">Focus indicators</p>
          <div className="flex gap-2" role="group" aria-label="Focus indicators">
            {[
              { value: "always", label: "Always visible" },
              { value: "keyboard", label: "Keyboard only" },
            ].map((opt) => (
              <Button
                key={opt.value}
                variant={focusMode === opt.value ? "secondary" : "outline"}
                size="sm"
                onClick={() => applyFocusMode(opt.value)}
                aria-pressed={focusMode === opt.value}
              >
                {opt.label}
              </Button>
            ))}
          </div>
        </Card>
      </div>
    </section>
  );
}

/* ------------------------------------------------------------------ */
/*  Desktop & Dock                                                     */
/* ------------------------------------------------------------------ */

function DesktopDockSection() {
  const [dockSize, setDockSize] = useState(
    () => localStorage.getItem("taos-dock-size") ?? "medium"
  );
  const [dockPosition, setDockPosition] = useState(
    () => localStorage.getItem("taos-dock-position") ?? "bottom"
  );

  const applyDockSize = (size: string) => {
    setDockSize(size);
    localStorage.setItem("taos-dock-size", size);
  };

  const applyDockPosition = (position: string) => {
    setDockPosition(position);
    localStorage.setItem("taos-dock-position", position);
  };

  return (
    <section aria-label="Desktop and dock settings">
      <h2 className="text-lg font-semibold mb-5">Desktop & Dock</h2>

      <div className="space-y-3">
        <Card className="p-4">
          <p className="text-sm font-medium mb-3">Dock icon size</p>
          <div className="flex gap-2" role="group" aria-label="Dock icon size">
            {(["small", "medium", "large"] as const).map((size) => (
              <Button
                key={size}
                variant={dockSize === size ? "secondary" : "outline"}
                size="sm"
                onClick={() => applyDockSize(size)}
                aria-pressed={dockSize === size}
              >
                {size.charAt(0).toUpperCase() + size.slice(1)}
              </Button>
            ))}
          </div>
        </Card>

        <Card className="p-4">
          <p className="text-sm font-medium mb-3">Dock position</p>
          <div className="flex gap-2" role="group" aria-label="Dock position">
            {[
              { value: "bottom", label: "Bottom" },
              { value: "left", label: "Left" },
            ].map((opt) => (
              <Button
                key={opt.value}
                variant={dockPosition === opt.value ? "secondary" : "outline"}
                size="sm"
                onClick={() => applyDockPosition(opt.value)}
                aria-pressed={dockPosition === opt.value}
              >
                {opt.label}
              </Button>
            ))}
          </div>
        </Card>
      </div>
    </section>
  );
}

/* ------------------------------------------------------------------ */
/*  Users                                                              */
/* ------------------------------------------------------------------ */

interface UserRecord {
  id: string;
  username: string;
  full_name: string;
  email: string;
  is_admin: boolean;
  pending: boolean;
  invite_code?: string;
  last_login_at?: number | null;
  created_at?: number | null;
}

function CopyButton({ text, label }: { text: string; label?: string }) {
  const [copied, setCopied] = useState(false);
  const copy = async () => {
    await navigator.clipboard.writeText(text).catch(() => {});
    setCopied(true);
    setTimeout(() => setCopied(false), 1500);
  };
  return (
    <button
      onClick={copy}
      className="inline-flex items-center gap-1 text-xs text-shell-text-secondary hover:text-shell-text transition-colors"
      aria-label={label ?? `Copy ${text}`}
    >
      {copied ? <Check size={12} className="text-emerald-400" /> : <Copy size={12} />}
      {copied ? "Copied" : "Copy"}
    </button>
  );
}

function Modal({
  title,
  onClose,
  children,
}: {
  title: string;
  onClose: () => void;
  children: ReactNode;
}) {
  const ref = useRef<HTMLDivElement>(null);
  useEffect(() => {
    const handleKey = (e: KeyboardEvent) => { if (e.key === "Escape") onClose(); };
    window.addEventListener("keydown", handleKey);
    return () => window.removeEventListener("keydown", handleKey);
  }, [onClose]);

  return (
    <div
      role="dialog"
      aria-modal="true"
      aria-label={title}
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/60"
      onClick={(e) => { if (e.target === e.currentTarget) onClose(); }}
    >
      <div ref={ref} className="bg-shell-surface border border-white/10 rounded-xl p-6 w-full max-w-sm shadow-xl">
        <div className="flex items-center justify-between mb-4">
          <h3 className="text-base font-semibold">{title}</h3>
          <button onClick={onClose} className="text-shell-text-tertiary hover:text-shell-text" aria-label="Close">
            <X size={16} />
          </button>
        </div>
        {children}
      </div>
    </div>
  );
}

function AddUserModal({ onClose, onAdded }: { onClose: () => void; onAdded: () => void }) {
  const [username, setUsername] = useState("");
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(false);
  const [code, setCode] = useState<string | null>(null);

  const submit = async () => {
    if (!username.trim()) return;
    setLoading(true);
    setError("");
    const resp = await fetch("/auth/users", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      credentials: "include",
      body: JSON.stringify({ username: username.trim() }),
    });
    setLoading(false);
    if (!resp.ok) {
      const d = await resp.json().catch(() => ({}));
      setError((d as { error?: string }).error ?? "Failed to add user");
      return;
    }
    const d = await resp.json();
    setCode(d.invite_code);
    onAdded();
  };

  return (
    <Modal title="Add user" onClose={onClose}>
      {code ? (
        <div className="space-y-3">
          <p className="text-sm text-shell-text-secondary">Share this invite code with the user. It is shown only once.</p>
          <div className="flex items-center justify-between rounded-lg bg-shell-bg-deep border border-white/10 px-4 py-3">
            <span className="font-mono text-lg tracking-widest text-shell-text">{code}</span>
            <CopyButton text={code} label="Copy invite code" />
          </div>
          <Button className="w-full" onClick={onClose}>Done</Button>
        </div>
      ) : (
        <div className="space-y-3">
          <div>
            <Label htmlFor="new-username">Username</Label>
            <Input
              id="new-username"
              autoFocus
              value={username}
              onChange={(e) => setUsername(e.target.value.replace(/\s+/g, "").toLowerCase())}
              placeholder="alice"
              className="mt-1"
              onKeyDown={(e) => { if (e.key === "Enter") submit(); }}
            />
          </div>
          {error && <p className="text-xs text-red-400">{error}</p>}
          <div className="flex gap-2 justify-end">
            <Button variant="outline" size="sm" onClick={onClose}>Cancel</Button>
            <Button size="sm" onClick={submit} disabled={loading || !username.trim()}>
              {loading ? "Adding..." : "Add"}
            </Button>
          </div>
        </div>
      )}
    </Modal>
  );
}

function ResetPasswordModal({ username, onClose, onReset }: { username: string; onClose: () => void; onReset: () => void }) {
  const [loading, setLoading] = useState(false);
  const [code, setCode] = useState<string | null>(null);
  const [error, setError] = useState("");

  const doReset = async () => {
    setLoading(true);
    const resp = await fetch(`/auth/users/${encodeURIComponent(username)}/reset`, {
      method: "POST",
      credentials: "include",
    });
    setLoading(false);
    if (!resp.ok) {
      const d = await resp.json().catch(() => ({}));
      setError((d as { error?: string }).error ?? "Failed to reset");
      return;
    }
    const d = await resp.json();
    setCode(d.invite_code);
    onReset();
  };

  return (
    <Modal title={`Reset password for ${username}`} onClose={onClose}>
      {code ? (
        <div className="space-y-3">
          <p className="text-sm text-shell-text-secondary">New invite code for {username}:</p>
          <div className="flex items-center justify-between rounded-lg bg-shell-bg-deep border border-white/10 px-4 py-3">
            <span className="font-mono text-lg tracking-widest text-shell-text">{code}</span>
            <CopyButton text={code} label="Copy reset code" />
          </div>
          <Button className="w-full" onClick={onClose}>Done</Button>
        </div>
      ) : (
        <div className="space-y-3">
          <p className="text-sm text-shell-text-secondary">
            This will revoke {username}'s current password and sessions. A new invite code will be generated.
          </p>
          {error && <p className="text-xs text-red-400">{error}</p>}
          <div className="flex gap-2 justify-end">
            <Button variant="outline" size="sm" onClick={onClose}>Cancel</Button>
            <Button size="sm" onClick={doReset} disabled={loading}>
              {loading ? "Resetting..." : "Reset password"}
            </Button>
          </div>
        </div>
      )}
    </Modal>
  );
}

function ChangePasswordModal({ username, onClose }: { username: string; onClose: () => void }) {
  const [current, setCurrent] = useState("");
  const [next, setNext] = useState("");
  const [confirm, setConfirm] = useState("");
  const [error, setError] = useState("");
  const [done, setDone] = useState(false);
  const [loading, setLoading] = useState(false);

  const matches = next.length > 0 && next === confirm;
  const valid = current.length > 0 && next.length >= 4 && matches;

  const submit = async () => {
    if (!valid) return;
    setLoading(true);
    setError("");
    const resp = await fetch(`/auth/users/${encodeURIComponent(username)}/password`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      credentials: "include",
      body: JSON.stringify({ current, new: next }),
    });
    setLoading(false);
    if (!resp.ok) {
      const d = await resp.json().catch(() => ({}));
      setError((d as { error?: string }).error ?? "Failed to change password");
      return;
    }
    setDone(true);
  };

  return (
    <Modal title="Change password" onClose={onClose}>
      {done ? (
        <div className="space-y-3">
          <p className="text-sm text-emerald-400 flex items-center gap-2"><Check size={14} /> Password changed.</p>
          <Button className="w-full" onClick={onClose}>Done</Button>
        </div>
      ) : (
        <div className="space-y-3">
          <div>
            <Label htmlFor="pw-current">Current password</Label>
            <Input id="pw-current" type="password" value={current} onChange={(e) => setCurrent(e.target.value)} className="mt-1" autoFocus />
          </div>
          <div>
            <Label htmlFor="pw-new">New password</Label>
            <Input id="pw-new" type="password" value={next} onChange={(e) => setNext(e.target.value)} className="mt-1" />
          </div>
          <div>
            <Label htmlFor="pw-confirm">Confirm</Label>
            <Input
              id="pw-confirm"
              type="password"
              value={confirm}
              onChange={(e) => setConfirm(e.target.value)}
              className="mt-1"
              aria-invalid={confirm.length > 0 && !matches}
            />
            {confirm.length > 0 && !matches && <p className="text-[11px] text-red-400 mt-1">Passwords don't match.</p>}
          </div>
          {error && <p className="text-xs text-red-400">{error}</p>}
          <div className="flex gap-2 justify-end">
            <Button variant="outline" size="sm" onClick={onClose}>Cancel</Button>
            <Button size="sm" disabled={!valid || loading} onClick={submit}>
              {loading ? "Saving..." : "Change password"}
            </Button>
          </div>
        </div>
      )}
    </Modal>
  );
}

function DeleteUserModal({ username, onClose, onDeleted }: { username: string; onClose: () => void; onDeleted: () => void }) {
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");

  const doDelete = async () => {
    setLoading(true);
    const resp = await fetch(`/auth/users/${encodeURIComponent(username)}`, {
      method: "DELETE",
      credentials: "include",
    });
    setLoading(false);
    if (!resp.ok) {
      const d = await resp.json().catch(() => ({}));
      setError((d as { error?: string }).error ?? "Failed to remove user");
      return;
    }
    onDeleted();
    onClose();
  };

  return (
    <Modal title={`Remove ${username}`} onClose={onClose}>
      <div className="space-y-3">
        <p className="text-sm text-shell-text-secondary">
          This will remove {username} and revoke all their sessions. This cannot be undone.
        </p>
        {error && <p className="text-xs text-red-400">{error}</p>}
        <div className="flex gap-2 justify-end">
          <Button variant="outline" size="sm" onClick={onClose}>Cancel</Button>
          <Button size="sm" onClick={doDelete} disabled={loading}
            className="bg-red-500/20 text-red-300 hover:bg-red-500/30 border-red-500/30">
            {loading ? "Removing..." : "Remove user"}
          </Button>
        </div>
      </div>
    </Modal>
  );
}

function UsersSection() {
  const [users, setUsers] = useState<UserRecord[]>([]);
  const [currentUser, setCurrentUser] = useState<UserRecord | null>(null);
  const [isAdmin, setIsAdmin] = useState(false);
  const [multiUser, setMultiUser] = useState(false);
  const [editFullName, setEditFullName] = useState("");
  const [editEmail, setEditEmail] = useState("");
  const [profileSaved, setProfileSaved] = useState(false);
  const [profileError, setProfileError] = useState("");
  const [showChangePassword, setShowChangePassword] = useState(false);
  const [showAddUser, setShowAddUser] = useState(false);
  const [resetTarget, setResetTarget] = useState<string | null>(null);
  const [deleteTarget, setDeleteTarget] = useState<string | null>(null);

  const [autoLoginDefault, setAutoLoginDefault] = useServerPreference<boolean>(
    "auto-login",
    true,
    (blob) => typeof blob.value === "boolean" ? blob.value : true,
    (v) => ({ value: v }),
  );

  const loadData = useCallback(async () => {
    try {
      const statusResp = await fetch("/auth/status", { credentials: "include" });
      if (statusResp.ok) {
        const s = await statusResp.json();
        setMultiUser(!!s.multi_user);
        if (s.user) {
          const u = s.user as UserRecord;
          setCurrentUser(u);
          setIsAdmin(!!u.is_admin);
          setEditFullName(u.full_name ?? "");
          setEditEmail(u.email ?? "");
        }
      }
    } catch { /* ignore */ }
    try {
      const usersResp = await fetch("/auth/users", { credentials: "include" });
      if (usersResp.ok) {
        const d = await usersResp.json();
        setUsers(d.users ?? []);
      }
    } catch { /* ignore */ }
  }, []);

  useEffect(() => { loadData(); }, [loadData]);

  const saveProfile = async () => {
    if (!currentUser) return;
    setProfileError("");
    const resp = await fetch(`/auth/users/${encodeURIComponent(currentUser.username)}/profile`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      credentials: "include",
      body: JSON.stringify({ full_name: editFullName, email: editEmail }),
    });
    if (resp.ok) {
      setProfileSaved(true);
      setTimeout(() => setProfileSaved(false), 2000);
      loadData();
    } else {
      const d = await resp.json().catch(() => ({}));
      setProfileError((d as { error?: string }).error ?? "Save failed");
    }
  };

  const formatDate = (ts?: number | null) =>
    ts ? new Date(ts * 1000).toLocaleDateString() : "—";

  return (
    <section aria-label="Users and account settings">
      <h2 className="text-lg font-semibold mb-5">Users</h2>

      {/* My Account card */}
      <Card className="p-5 mb-4 space-y-4">
        <h3 className="text-sm font-semibold">My Account</h3>

        <div>
          <Label htmlFor="acct-username" className="text-xs text-shell-text-tertiary mb-1 block">Username</Label>
          <div className="flex items-center gap-2">
            <Input
              id="acct-username"
              value={currentUser?.username ?? ""}
              readOnly
              className="opacity-60 cursor-not-allowed"
              aria-readonly="true"
            />
          </div>
          <p className="text-[10px] text-shell-text-tertiary mt-1">Username cannot be changed.</p>
        </div>

        <div>
          <Label htmlFor="acct-fullname" className="text-xs text-shell-text-tertiary mb-1 block">Full name</Label>
          <Input
            id="acct-fullname"
            value={editFullName}
            onChange={(e) => { setEditFullName(e.target.value); setProfileSaved(false); }}
            placeholder="Your name"
          />
        </div>

        <div>
          <Label htmlFor="acct-email" className="text-xs text-shell-text-tertiary mb-1 block">Email</Label>
          <Input
            id="acct-email"
            type="email"
            value={editEmail}
            onChange={(e) => { setEditEmail(e.target.value); setProfileSaved(false); }}
            placeholder="you@example.com"
          />
        </div>

        {profileError && (
          <p className="text-xs text-red-400 flex items-center gap-1.5"><AlertCircle size={12} /> {profileError}</p>
        )}

        <div className="flex items-center gap-2">
          <Button size="sm" onClick={saveProfile}>
            {profileSaved ? <><Check size={12} /> Saved</> : "Save changes"}
          </Button>
          <Button variant="outline" size="sm" onClick={() => setShowChangePassword(true)}>
            <KeyRound size={14} /> Change password
          </Button>
        </div>

        <div className="border-t border-white/5 pt-3">
          <div className="flex items-center justify-between gap-3">
            <div className="flex-1 min-w-0">
              <Label htmlFor="acct-autologin" className="text-sm">Stay signed in by default on this device</Label>
              <p className="text-[11px] text-shell-text-tertiary mt-0.5">
                When on, the login form's "Stay signed in" checkbox starts checked.
              </p>
            </div>
            <Switch
              id="acct-autologin"
              checked={autoLoginDefault}
              onCheckedChange={setAutoLoginDefault}
              aria-label="Stay signed in by default"
            />
          </div>
        </div>
      </Card>

      {/* Team Members card — admin only */}
      {isAdmin && (
        <Card className="p-5 space-y-4">
          <div className="flex items-center justify-between">
            <h3 className="text-sm font-semibold">Team Members</h3>
            <Button size="sm" onClick={() => setShowAddUser(true)}>
              <Plus size={14} /> Add user
            </Button>
          </div>

          {multiUser && (
            <p className="text-xs text-shell-text-tertiary">
              Auto-login is disabled by default for new sessions while multiple users exist.
            </p>
          )}

          <div className="overflow-x-auto">
            <table className="w-full text-sm min-w-[500px]">
              <thead>
                <tr className="border-b border-white/[0.08]">
                  <th className="py-2 px-3 text-left text-xs font-medium text-shell-text-secondary">Username</th>
                  <th className="py-2 px-3 text-left text-xs font-medium text-shell-text-secondary">Full name</th>
                  <th className="py-2 px-3 text-left text-xs font-medium text-shell-text-secondary">Email</th>
                  <th className="py-2 px-3 text-left text-xs font-medium text-shell-text-secondary">Last login</th>
                  <th className="py-2 px-3 text-left text-xs font-medium text-shell-text-secondary">Status</th>
                  <th className="py-2 px-3 text-left text-xs font-medium text-shell-text-secondary">Actions</th>
                </tr>
              </thead>
              <tbody>
                {users.map((u) => (
                  <tr key={u.username} className="border-b border-white/5 last:border-0">
                    <td className="py-2.5 px-3 font-medium">{u.username}</td>
                    <td className="py-2.5 px-3 text-shell-text-secondary">{u.full_name || "—"}</td>
                    <td className="py-2.5 px-3 text-shell-text-secondary truncate max-w-[140px]">{u.email || "—"}</td>
                    <td className="py-2.5 px-3 text-shell-text-tertiary tabular-nums">{formatDate(u.last_login_at)}</td>
                    <td className="py-2.5 px-3">
                      {u.pending ? (
                        <div className="flex items-center gap-2">
                          <span className="text-[10px] px-2 py-0.5 rounded-full bg-amber-500/20 text-amber-300 font-medium">pending</span>
                          {u.invite_code && (
                            <div className="flex items-center gap-1">
                              <span className="font-mono text-xs text-shell-text-secondary">{u.invite_code}</span>
                              <CopyButton text={u.invite_code} label={`Copy invite code for ${u.username}`} />
                            </div>
                          )}
                        </div>
                      ) : (
                        <span className="text-[10px] px-2 py-0.5 rounded-full bg-emerald-500/20 text-emerald-300 font-medium">active</span>
                      )}
                    </td>
                    <td className="py-2.5 px-3">
                      <div className="flex items-center gap-1.5">
                        {u.username !== currentUser?.username && (
                          <>
                            <button
                              onClick={() => setResetTarget(u.username)}
                              className="p-1 rounded hover:bg-white/10 text-shell-text-tertiary hover:text-shell-text transition-colors"
                              aria-label={`Reset password for ${u.username}`}
                              title="Reset password"
                            >
                              <KeyRound size={13} />
                            </button>
                            <button
                              onClick={() => setDeleteTarget(u.username)}
                              className="p-1 rounded hover:bg-red-500/20 text-shell-text-tertiary hover:text-red-400 transition-colors"
                              aria-label={`Remove ${u.username}`}
                              title="Remove user"
                            >
                              <Trash2 size={13} />
                            </button>
                          </>
                        )}
                      </div>
                    </td>
                  </tr>
                ))}
                {users.length === 0 && (
                  <tr>
                    <td colSpan={6} className="py-4 px-3 text-sm text-shell-text-tertiary">No users yet.</td>
                  </tr>
                )}
              </tbody>
            </table>
          </div>
        </Card>
      )}

      {showChangePassword && currentUser && (
        <ChangePasswordModal
          username={currentUser.username}
          onClose={() => setShowChangePassword(false)}
        />
      )}
      {showAddUser && (
        <AddUserModal
          onClose={() => setShowAddUser(false)}
          onAdded={loadData}
        />
      )}
      {resetTarget && (
        <ResetPasswordModal
          username={resetTarget}
          onClose={() => setResetTarget(null)}
          onReset={loadData}
        />
      )}
      {deleteTarget && (
        <DeleteUserModal
          username={deleteTarget}
          onClose={() => setDeleteTarget(null)}
          onDeleted={loadData}
        />
      )}
    </section>
  );
}

/* ------------------------------------------------------------------ */
/*  Main SettingsApp                                                   */
/* ------------------------------------------------------------------ */

export function SettingsApp({ windowId: _windowId }: { windowId: string }) {
  const [section, setSection] = useState<Section>("system");
  const [mobileShowSection, setMobileShowSection] = useState(false);

  const isMobile = typeof window !== "undefined" && window.innerWidth < 640;

  const content: Record<Section, ReactNode> = {
    system: <SystemInfoSection />,
    storage: <StorageSection />,
    memory: <MemorySection />,
    backup: <BackupSection />,
    updates: <UpdatesSection />,
    advanced: <AdvancedSection />,
    shortcuts: <KeyboardShortcutsSection />,
    accessibility: <AccessibilitySection />,
    desktop: <DesktopDockSection />,
    users: <UsersSection />,
    themes: <ThemesPanel />,
  };

  const handleSelectSection = (id: Section) => {
    setSection(id);
    setMobileShowSection(true);
  };

  const sidebarUI = (
    <nav
      className={isMobile ? "w-full overflow-y-auto" : "w-52 shrink-0 border-r border-white/5 bg-shell-surface/30 overflow-y-auto"}
      aria-label="Settings sections"
    >
      <div className="p-3 space-y-1">
        {SECTIONS.map((s) => {
          const active = section === s.id;
          const Icon = s.icon;
          return (
            <Button
              key={s.id}
              variant={active ? "secondary" : "ghost"}
              onClick={() => handleSelectSection(s.id)}
              className="w-full justify-start gap-3 h-auto py-2.5"
              aria-current={active ? "page" : undefined}
            >
              <div className={`p-1.5 rounded-lg transition-colors ${active ? "bg-sky-500/20 text-sky-400" : "bg-white/5"}`}>
                <Icon size={16} />
              </div>
              {s.label}
            </Button>
          );
        })}
      </div>
    </nav>
  );

  const contentUI = (
    <main className="flex-1 overflow-y-auto p-6">
      {isMobile && (
        <Button variant="ghost" size="sm" onClick={() => setMobileShowSection(false)} className="mb-3">
          <ChevronLeft size={14} /> Back
        </Button>
      )}
      {content[section]}
    </main>
  );

  return (
    <div className="flex h-full bg-shell-bg-deep text-shell-text select-none">
      {isMobile ? (
        mobileShowSection ? contentUI : sidebarUI
      ) : (
        <>
          {sidebarUI}
          {contentUI}
        </>
      )}
    </div>
  );
}
