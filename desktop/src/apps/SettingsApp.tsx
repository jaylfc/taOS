import { useState, useEffect, useCallback, type ReactNode } from "react";
import {
  HardDrive,
  Download,
  Upload,
  RefreshCw,
  Code,
  Info,
  AlertCircle,
  ChevronLeft,
  Brain,
  Keyboard,
  Accessibility,
  Monitor,
  Users,
  Palette,
} from "lucide-react";
import {
  Button,
  Card,
  Label,
  Switch,
} from "@/components/ui";
import { useShortcuts } from "@/hooks/use-shortcut-registry";
import { ThemesPanel } from "@/apps/SettingsApp/ThemesPanel";
import { safeFetch, ProgressBar, RestartProgressModal } from "@/apps/SettingsApp/_shared";
import { UpdatesSection } from "@/apps/SettingsApp/UpdatesPanel";
import { UsersSection } from "@/apps/SettingsApp/UsersPanel";

/* ------------------------------------------------------------------ */
/*  Types                                                              */
/* ------------------------------------------------------------------ */

type Section = "system" | "storage" | "memory" | "backup" | "updates" | "advanced" | "shortcuts" | "accessibility" | "desktop" | "users" | "themes";

interface SectionDef {
  id: Section;
  label: string;
  icon: typeof HardDrive;
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
      const cpuCores = hw.cpu?.cores ? ` × ${hw.cpu.cores}` : "";
      const cpuArch = hw.cpu?.arch ? ` (${hw.cpu.arch})` : "";
      const gpuModel = hw.gpu?.model || hw.gpu?.type || "None";
      const gpuVram =
        hw.gpu?.vram_mb && hw.gpu.vram_mb > 0
          ? ` (${(hw.gpu.vram_mb / 1024).toFixed(1)} GB)`
          : "";
      const npuType =
        hw.npu?.type && hw.npu.type !== "none" ? hw.npu.type : "None";
      const npuTops =
        hw.npu?.tops && hw.npu.tops > 0 ? ` · ${hw.npu.tops} TOPS` : "";
      const diskType = hw.disk?.type ? ` ${hw.disk.type}` : "";
      const osParts = [hw.os?.distro, hw.os?.version].filter(Boolean);
      const osStr = osParts.length > 0 ? osParts.join(" ") : "—";
      setInfo({
        cpu: `${cpuModel}${cpuCores}${cpuArch}`,
        ram:
          ramMb >= 1024
            ? `${(ramMb / 1024).toFixed(1)} GB`
            : ramMb > 0
              ? `${ramMb} MB`
              : "—",
        npu: `${npuType}${npuTops}`,
        gpu: `${gpuModel}${gpuVram}`,
        disk: diskGb > 0 ? `${diskGb} GB${diskType}` : "—",
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
/*  Main SettingsApp                                                   */
/* ------------------------------------------------------------------ */

function isSection(value: string | undefined): value is Section {
  return value != null && SECTIONS.some((s) => s.id === value);
}

export function SettingsApp({ windowId: _windowId, section: initialSection }: { windowId: string; section?: string }) {
  const [section, setSection] = useState<Section>(() =>
    isSection(initialSection) ? initialSection : "system"
  );
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
