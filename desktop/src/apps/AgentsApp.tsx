import { useState, useEffect, useRef, useCallback, type ReactNode } from "react";
import { useIsMobile } from "@/hooks/use-is-mobile";
import { Bot, Box, Plus, Trash2, ScrollText, Play, Server, X, ChevronRight, ChevronLeft, Check, Wrench, MessageSquare, PauseCircle, RotateCcw, Archive, HardDrive } from "lucide-react";
import { fetchLatestFrameworks, LatestVersion } from "@/lib/framework-api";
import { AgentSkillsPanel } from "./AgentSkillsPanel";
import { AgentMessagesPanel } from "./AgentMessagesPanel";
import { PersonaTab } from "@/components/agent-settings/PersonaTab";
import { MemoryTab } from "@/components/agent-settings/MemoryTab";
import { FrameworkTab } from "@/components/agent-settings/FrameworkTab";
import {
  fetchClusterWorkers,
  workersToAggregated,
  HOST_BADGE_CLASS,
  CLOUD_PROVIDER_TYPES,
} from "@/lib/models";
import { availableKvQuantOptions, type KvQuantOptions } from "@/lib/cluster";
import { resolveAgentEmoji } from "@/lib/agent-emoji";
import { EmojiPickerField } from "@/components/EmojiPicker";
import {
  Button,
  Card,
  Input,
  Label,
  Tabs,
  TabsContent,
  TabsList,
  TabsTrigger,
} from "@/components/ui";
import { ModelPickerFlow, type AgentModel } from "@/components/ModelPickerFlow";
import { ModelPickerModal } from "@/components/ModelPickerModal";
import { PersonaPicker } from "@/components/persona-picker/PersonaPicker";
import type { PersonaSelection } from "@/components/persona-picker/types";
import { slugifyClient, isValidSlug, SLUG_REGEX } from "@/lib/slug";
import { MigrationBanner } from "@/components/MigrationBanner";
import { AgentShortcutRow } from "@/components/AgentShortcutRow";
import type { AgentShortcut } from "@/hooks/use-agent-shortcuts";
import { useProcessStore } from "@/stores/process-store";
import { getApp } from "@/registry/app-registry";

/* ------------------------------------------------------------------ */
/*  Types                                                              */
/* ------------------------------------------------------------------ */

interface Agent {
  name: string;
  display_name?: string;
  host: string;
  color: string;
  emoji?: string;
  status: "running" | "stopped" | "error" | "deploying";
  vectors: number;
  framework?: string;
  paused?: boolean;
  on_worker_failure?: "pause" | "fallback" | "escalate-immediately";
  fallback_models?: string[];
  kv_cache_quant_k?: string;
  kv_cache_quant_v?: string;
  kv_cache_quant_boundary_layers?: number;
  soul_md?: string;
  agent_md?: string;
  source_persona_id?: string | null;
  migrated_to_v2_personas?: boolean;
  framework_version_sha?: string | null;
}

interface DiskState {
  used_gib: number;
  quota_gib: number;
  percent: number;
  state: "ok" | "warn" | "hard";
  last_checked_at: string;
}

interface ArchivedAgent {
  id: string;
  archived_at: string;             // "YYYYMMDDTHHMMSS"
  archived_slug: string;
  archive_container: string;
  archive_dir: string;
  original: {
    name?: string;
    display_name?: string;
    color?: string;
    emoji?: string;
    model?: string;
    framework?: string;
  };
}

interface Framework {
  id: string;
  name: string;
  description: string;
  verification_status: "beta" | "alpha" | "broken";
}

// AgentModel is defined and exported from ModelPickerFlow
type Model = AgentModel;

/* ------------------------------------------------------------------ */
/*  Fallback data                                                      */
/* ------------------------------------------------------------------ */


const MEMORY_STEPS_MB = [256, 512, 1024, 2048, 4096, 8192, 16384, 32768, 65536, 131072];

const COLORS = [
  "#3b82f6", "#8b5cf6", "#ec4899", "#f59e0b",
  "#10b981", "#ef4444", "#06b6d4", "#f97316",
];

const STATUS_STYLES: Record<string, string> = {
  running: "bg-emerald-500/20 text-emerald-400",
  stopped: "bg-zinc-500/20 text-zinc-400",
  error: "bg-red-500/20 text-red-400",
  deploying: "bg-amber-500/20 text-amber-400",
};

/* ------------------------------------------------------------------ */
/*  AgentRow                                                           */
/* ------------------------------------------------------------------ */

function AgentRow({
  agent,
  diskState,
  latestByFramework,
  onViewLogs,
  onViewSkills,
  onViewMessages,
  onDelete,
  onResume,
  leftActions,
}: {
  agent: Agent;
  diskState?: DiskState | null;
  latestByFramework: Record<string, LatestVersion>;
  onViewLogs: (name: string) => void;
  onViewSkills: (name: string) => void;
  onViewMessages: (name: string) => void;
  onDelete: (name: string) => void;
  onResume: (name: string) => void;
  leftActions?: ReactNode;
}) {
  const isMobile = useIsMobile();
  const emoji = resolveAgentEmoji(agent.emoji, agent.framework);
  const latestForAgent = agent.framework ? latestByFramework[agent.framework] : undefined;
  const updateAvailable =
    agent.framework_version_sha &&
    latestForAgent &&
    latestForAgent.sha !== agent.framework_version_sha;

  const btnCls = isMobile ? "h-11 w-11" : "h-8 w-8";

  const actionButtons = (
    <>
      {agent.paused && (
        <Button
          variant="ghost"
          size="icon"
          className={`${btnCls} hover:bg-emerald-500/15 hover:text-emerald-400 text-amber-400`}
          onClick={() => onResume(agent.name)}
          aria-label={`Resume ${agent.name}`}
          title="Resume agent"
        >
          <RotateCcw size={15} />
        </Button>
      )}
      <Button
        variant="ghost"
        size="icon"
        className={btnCls}
        onClick={() => onViewLogs(agent.name)}
        aria-label={`View logs for ${agent.name}`}
        title="View Logs"
      >
        <ScrollText size={15} />
      </Button>
      <Button
        variant="ghost"
        size="icon"
        className={btnCls}
        onClick={() => onViewSkills(agent.name)}
        aria-label={`Manage skills for ${agent.name}`}
        title="Skills"
      >
        <Wrench size={15} />
      </Button>
      <Button
        variant="ghost"
        size="icon"
        className={btnCls}
        onClick={() => onViewMessages(agent.name)}
        aria-label={`View messages for ${agent.name}`}
        title="Messages"
      >
        <MessageSquare size={15} />
      </Button>
      <Button
        variant="ghost"
        size="icon"
        className={`${btnCls} hover:bg-red-500/15 hover:text-red-400`}
        onClick={() => onDelete(agent.name)}
        aria-label={`Delete ${agent.name}`}
        title="Delete"
      >
        <Trash2 size={15} />
      </Button>
    </>
  );

  if (isMobile) {
    return (
      <Card className="px-3 py-2.5 hover:bg-shell-surface/50 transition-colors">
        {/* Row 1: identity + status chip */}
        <div className="flex items-center gap-2 min-w-0">
          <span
            className="w-2 h-2 rounded-full shrink-0"
            style={{ backgroundColor: agent.color }}
            aria-label={`Color: ${agent.color}`}
          />
          <span className="text-base leading-none shrink-0" aria-hidden="true">
            {emoji}
          </span>
          <span className="font-medium text-sm truncate flex-1 min-w-0">
            {agent.display_name || agent.name}
          </span>
          {updateAvailable && (
            <span
              aria-label="framework update available"
              title="framework update available"
              className="inline-block w-2 h-2 bg-yellow-400 rounded-full shrink-0"
            />
          )}
          <span
            className={`inline-block px-2 py-0.5 rounded-full text-xs font-medium capitalize shrink-0 ${STATUS_STYLES[agent.status] ?? STATUS_STYLES.stopped}`}
            aria-label={`Status: ${agent.status}`}
          >
            {agent.status}
          </span>
        </div>
        {/* Row 2: host + vectors + optional chips */}
        <div className="flex items-center gap-2 mt-1 min-w-0">
          <Server size={11} className="text-shell-text-tertiary shrink-0" />
          <span className="text-xs text-shell-text-secondary truncate flex-1 min-w-0">{agent.host}</span>
          <span className="text-xs text-shell-text-tertiary tabular-nums shrink-0">
            {agent.vectors.toLocaleString()} vectors
          </span>
        </div>
        {(agent.paused || (diskState && diskState.state !== "ok")) && (
          <div className="flex flex-wrap gap-1.5 mt-1.5">
            {agent.paused && (
              <span
                className="inline-flex items-center gap-1 px-1.5 py-0.5 rounded-full text-[10px] font-medium bg-amber-500/20 text-amber-400 border border-amber-500/20"
                title="This agent is paused due to a worker failure"
              >
                <PauseCircle size={10} aria-hidden="true" />
                paused
              </span>
            )}
            {diskState && diskState.state !== "ok" && (
              <span
                className={`inline-flex items-center gap-1 px-1.5 py-0.5 rounded-full text-[10px] font-medium border ${
                  diskState.state === "hard"
                    ? "bg-red-500/20 text-red-400 border-red-500/20"
                    : "bg-amber-500/20 text-amber-400 border-amber-500/20"
                }`}
                aria-label={`Disk usage: ${diskState.used_gib}/${diskState.quota_gib} GiB (${diskState.percent}%)`}
              >
                <HardDrive size={10} aria-hidden="true" />
                {diskState.used_gib}/{diskState.quota_gib} GiB
              </span>
            )}
          </div>
        )}
        {/* Row 3: action buttons */}
        <div className="flex items-center justify-between mt-1 -ml-1.5">
          <div className="flex items-center gap-0">
            {leftActions}
          </div>
          <div className="flex items-center gap-0">
            {actionButtons}
          </div>
        </div>
      </Card>
    );
  }

  return (
    <Card className="flex items-center gap-4 px-4 py-3 hover:bg-shell-surface/50 transition-colors">
      <div className="flex items-center gap-2.5 flex-1 min-w-0">
        <span
          className="w-2.5 h-2.5 rounded-full shrink-0"
          style={{ backgroundColor: agent.color }}
          aria-label={`Color: ${agent.color}`}
        />
        <span
          className="text-base leading-none shrink-0"
          aria-hidden="true"
        >
          {emoji}
        </span>
        <span className="font-medium text-sm truncate">{agent.display_name || agent.name}</span>
        {updateAvailable && (
          <span
            aria-label="framework update available"
            title="framework update available"
            className="inline-block w-2 h-2 bg-yellow-400 rounded-full ml-1 shrink-0"
          />
        )}
        {agent.paused && (
          <span
            className="inline-flex items-center gap-1 px-1.5 py-0.5 rounded-full text-[10px] font-medium bg-amber-500/20 text-amber-400 border border-amber-500/20"
            title="This agent is paused due to a worker failure"
          >
            <PauseCircle size={10} aria-hidden="true" />
            paused
          </span>
        )}
        {diskState && diskState.state !== "ok" && (
          <span
            className={`inline-flex items-center gap-1 px-1.5 py-0.5 rounded-full text-[10px] font-medium border ${
              diskState.state === "hard"
                ? "bg-red-500/20 text-red-400 border-red-500/20"
                : "bg-amber-500/20 text-amber-400 border-amber-500/20"
            }`}
            title={
              diskState.state === "hard"
                ? "Full — agent paused until user action"
                : `Used more than ${diskState.percent}% — needs audit or expand`
            }
            aria-label={`Disk usage: ${diskState.used_gib}/${diskState.quota_gib} GiB (${diskState.percent}%)`}
          >
            <HardDrive size={10} aria-hidden="true" />
            {diskState.used_gib}/{diskState.quota_gib} GiB ({diskState.percent}%)
          </span>
        )}
      </div>
      <div className="flex items-center gap-1.5 text-sm text-shell-text-secondary min-w-0">
        <Server size={13} className="text-shell-text-tertiary" />
        <span className="truncate">{agent.host}</span>
      </div>
      <span
        className={`inline-block px-2 py-0.5 rounded-full text-xs font-medium capitalize ${STATUS_STYLES[agent.status] ?? STATUS_STYLES.stopped}`}
        aria-label={`Status: ${agent.status}`}
      >
        {agent.status}
      </span>
      <span className="text-sm text-shell-text-secondary tabular-nums w-20 text-right">
        {agent.vectors.toLocaleString()}
      </span>
      <div className="flex items-center gap-1">
        {leftActions}
        {actionButtons}
      </div>
    </Card>
  );
}

/* ------------------------------------------------------------------ */
/*  AgentDetailPanel (Logs + Skills tabs)                              */
/* ------------------------------------------------------------------ */

type DetailTab = "logs" | "persona" | "memory" | "framework" | "skills" | "messages";

function AgentDetailPanel({
  agent,
  initialTab,
  onClose,
  onAgentUpdated,
  fullHeight = false,
}: {
  agent: Agent;
  initialTab: DetailTab;
  onClose: () => void;
  onAgentUpdated: () => void;
  fullHeight?: boolean;
}) {
  const [tab, setTab] = useState<DetailTab>(initialTab);
  const [logs, setLogs] = useState<string>("Fetching logs...");
  const scrollRef = useRef<HTMLPreElement>(null);
  const agentName = agent.name;

  const fetchLogs = useCallback(async () => {
    try {
      const res = await fetch(
        `/api/agents/${encodeURIComponent(agentName)}/logs?lines=100`,
        { headers: { Accept: "application/json" } }
      );
      if (!res.ok) {
        setLogs(`[${new Date().toLocaleTimeString()}] Log fetch failed (${res.status}) for ${agentName}.`);
        return;
      }
      const data = await res.json();
      const logText = typeof data?.logs === "string"
        ? data.logs
        : Array.isArray(data?.logs)
          ? data.logs.join("\n")
          : JSON.stringify(data);
      setLogs(logText || `[${new Date().toLocaleTimeString()}] No logs available for ${agentName}.`);
    } catch {
      setLogs(`[${new Date().toLocaleTimeString()}] Unable to reach log endpoint for ${agentName}.\n[${new Date().toLocaleTimeString()}] Agent may not be running or the API is unavailable.`);
    }
  }, [agentName]);

  useEffect(() => {
    if (tab !== "logs") return;
    fetchLogs();
    const interval = setInterval(fetchLogs, 10_000);
    return () => clearInterval(interval);
  }, [fetchLogs, tab]);

  useEffect(() => {
    if (scrollRef.current) {
      scrollRef.current.scrollTop = scrollRef.current.scrollHeight;
    }
  }, [logs]);

  const dismissMigrationBanner = async () => {
    await fetch(`/api/agents/${encodeURIComponent(agentName)}/dismiss-migration-banner`, { method: "POST" });
    onAgentUpdated();
  };

  const addPersonaClick = async () => {
    await dismissMigrationBanner();
    setTab("persona");
  };

  return (
    <>
      <MigrationBanner agent={agent} onDismiss={dismissMigrationBanner} onAddPersona={addPersonaClick} />
      <Tabs
      value={tab}
      onValueChange={(v) => setTab(v as DetailTab)}
      className={fullHeight ? "border-t border-white/5 bg-shell-bg-deep flex flex-1 min-h-0 flex-col" : "border-t border-white/5 bg-shell-bg-deep flex flex-col"}
      style={fullHeight ? undefined : { height: "22rem" }}
    >
      <div className="flex items-center justify-between px-4 py-2 border-b border-white/5 shrink-0">
        <div className="flex items-center gap-3">
          <div className="flex items-center gap-1.5 text-sm">
            <span
              className="w-2 h-2 rounded-full"
              style={{ backgroundColor: agent.color }}
              aria-hidden
            />
            <span className="text-base leading-none" aria-hidden="true">
              {resolveAgentEmoji(agent.emoji, agent.framework)}
            </span>
            <span className="font-medium">{agentName}</span>
          </div>
          <TabsList aria-label="Agent detail tabs">
            <TabsTrigger value="logs">
              <ScrollText size={13} className="mr-1.5" />
              Logs
            </TabsTrigger>
            <TabsTrigger value="persona">
              <Bot size={13} className="mr-1.5" />
              Persona
            </TabsTrigger>
            <TabsTrigger value="memory">
              <Archive size={13} className="mr-1.5" />
              Memory
            </TabsTrigger>
            <TabsTrigger value="framework">
              <Box size={13} className="mr-1.5" />
              Framework
            </TabsTrigger>
            <TabsTrigger value="skills">
              <Wrench size={13} className="mr-1.5" />
              Skills
            </TabsTrigger>
            <TabsTrigger value="messages">
              <MessageSquare size={13} className="mr-1.5" />
              Messages
            </TabsTrigger>
          </TabsList>
        </div>
        <Button
          variant="ghost"
          size="icon"
          className="h-7 w-7"
          onClick={onClose}
          aria-label="Close detail panel"
        >
          <X size={14} />
        </Button>
      </div>
      <div className="flex-1 min-h-0 overflow-hidden">
        <TabsContent value="logs" className="h-full mt-0">
          <pre
            ref={scrollRef}
            className="h-full overflow-auto p-4 text-xs font-mono text-shell-text-secondary leading-relaxed whitespace-pre-wrap"
          >
            {logs}
          </pre>
        </TabsContent>
        <TabsContent value="persona" className="h-full mt-0">
          <PersonaTab agent={agent} onUpdated={onAgentUpdated} />
        </TabsContent>
        <TabsContent value="memory" className="h-full mt-0">
          <MemoryTab agent={agent} onUpdated={onAgentUpdated} />
        </TabsContent>
        <TabsContent value="framework" className="h-full mt-0">
          <FrameworkTab agent={agent} onUpdated={onAgentUpdated} />
        </TabsContent>
        <TabsContent value="skills" className="h-full mt-0">
          <AgentSkillsPanel
            agentId={agent.name}
            framework={agent.framework || "smolagents"}
          />
        </TabsContent>
        <TabsContent value="messages" className="h-full mt-0">
          <AgentMessagesPanel agentName={agent.name} />
        </TabsContent>
      </div>
    </Tabs>
    </>
  );
}

/* ------------------------------------------------------------------ */
/*  MemoryWizardStep                                                   */
/* ------------------------------------------------------------------ */

// Tier metadata kept in sync with backend MEMORY_TIERS constant.
const MEMORY_TIER_INFO: Record<string, { label: string; description: string; min_ram_mb: number; needs_accel: boolean }> = {
  lite:     { label: "Lite",     description: "Works on any device. nomic-embed-text-v1.5 (~270MB). Slower retrieval.", min_ram_mb: 1024,  needs_accel: false },
  standard: { label: "Standard", description: "Recommended for most users. bge-m3 (~2.3GB). Balanced.",                min_ram_mb: 4096,  needs_accel: false },
  heavy:    { label: "Heavy",    description: "Best quality. bge-m3 + qwen3-reranker-0.6b (~3.5GB). Needs GPU/NPU.",   min_ram_mb: 8192,  needs_accel: true  },
};

/** Parse a tier_id like "arm-vulkan-8gb" and return RAM in MB. */
function tierIdRamMb(tierId: string): number {
  const m = tierId.match(/(\d+)gb/i);
  return m && m[1] ? parseInt(m[1], 10) * 1024 : 0;
}

/** Return the largest MEMORY_TIERS key the device tier_id can support. */
function bestMemoryTierForDevice(deviceTierId: string): string {
  const ram = tierIdRamMb(deviceTierId);
  const hasAccel = /vulkan|cuda|npu|gpu/i.test(deviceTierId);
  if (ram >= 8192 && hasAccel) return "heavy";
  if (ram >= 4096) return "standard";
  return "lite";
}

interface MemoryWizardStepProps {
  memoryPlugin: "taosmd" | null;
  setMemoryPlugin: (v: "taosmd" | null) => void;
  memoryDeviceId: string | null;
  setMemoryDeviceId: (v: string | null) => void;
  memoryTierId: string | null;
  setMemoryTierId: (v: string | null) => void;
  memoryDefault: { device_id: string; tier_id: string; tier_name: string } | null | "none";
  setMemoryDefault: (v: { device_id: string; tier_id: string; tier_name: string } | null | "none") => void;
  memoryInstallTargets: Array<{ name: string; friendly_name: string; tier_id: string }>;
  setMemoryInstallTargets: (v: Array<{ name: string; friendly_name: string; tier_id: string }>) => void;
  memoryDevicesLoaded: boolean;
  setMemoryDevicesLoaded: (v: boolean) => void;
  memorySetupTaskId: string | null;
  setMemorySetupTaskId: (v: string | null) => void;
  memorySetupState: string;
  setMemorySetupState: (v: string) => void;
  memorySetupMsg: string;
  setMemorySetupMsg: (v: string) => void;
  memorySetupError: string | null;
  setMemorySetupError: (v: string | null) => void;
  memoryPickerMode: "default" | "picker";
  setMemoryPickerMode: (v: "default" | "picker") => void;
}

function MemoryWizardStep({
  memoryPlugin,
  setMemoryPlugin,
  memoryDeviceId,
  setMemoryDeviceId,
  memoryTierId,
  setMemoryTierId,
  memoryDefault,
  setMemoryDefault,
  memoryInstallTargets,
  setMemoryInstallTargets,
  memoryDevicesLoaded,
  setMemoryDevicesLoaded,
  memorySetupTaskId,
  setMemorySetupTaskId,
  memorySetupState,
  setMemorySetupState,
  memorySetupMsg,
  setMemorySetupMsg,
  memorySetupError,
  setMemorySetupError,
  memoryPickerMode,
  setMemoryPickerMode,
}: MemoryWizardStepProps) {
  // Fetch default + install targets once on mount
  useEffect(() => {
    // Fetch user's saved default
    fetch("/api/taosmd/default", { headers: { Accept: "application/json" } })
      .then(r => r.ok ? r.json() : null)
      .then(data => {
        if (data?.device_id) {
          setMemoryDefault(data as { device_id: string; tier_id: string; tier_name: string });
        } else {
          setMemoryDefault("none");
          setMemoryPickerMode("picker");
        }
      })
      .catch(() => { setMemoryDefault("none"); setMemoryPickerMode("picker"); });

    // Fetch install targets for device picker
    fetch("/api/cluster/install-targets", { headers: { Accept: "application/json" } })
      .then(r => r.ok ? r.json() : [])
      .then((data: Array<{ name: string; friendly_name: string; tier_id: string }>) => {
        setMemoryInstallTargets(Array.isArray(data) ? data : []);
      })
      .catch(() => setMemoryInstallTargets([]))
      .finally(() => setMemoryDevicesLoaded(true));
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // Poll setup task progress
  useEffect(() => {
    if (!memorySetupTaskId) return;
    if (memorySetupState === "done" || memorySetupState === "failed") return;

    const interval = setInterval(async () => {
      try {
        const r = await fetch(`/api/taosmd/setup/${memorySetupTaskId}`, { headers: { Accept: "application/json" } });
        if (!r.ok) return;
        const data = await r.json();
        setMemorySetupState(data.state ?? "pending");
        setMemorySetupMsg(data.message ?? "");
        setMemorySetupError(data.error ?? null);
      } catch { /* ignore */ }
    }, 2000);
    return () => clearInterval(interval);
  }, [memorySetupTaskId, memorySetupState, setMemorySetupState, setMemorySetupMsg, setMemorySetupError]);

  async function handleSetup() {
    if (!memoryDeviceId || !memoryTierId) return;
    setMemorySetupState("pending");
    setMemorySetupMsg("Queued…");
    setMemorySetupError(null);
    try {
      const r = await fetch("/api/taosmd/setup", {
        method: "POST",
        headers: { "Content-Type": "application/json", Accept: "application/json" },
        body: JSON.stringify({ device_id: memoryDeviceId, tier: memoryTierId }),
      });
      if (!r.ok) {
        const err = await r.json().catch(() => ({}));
        setMemorySetupState("failed");
        setMemorySetupError(err?.error ?? "Setup request failed");
        return;
      }
      const { task_id } = await r.json();
      setMemorySetupTaskId(task_id);
      // Save the default optimistically
      await fetch("/api/taosmd/default", {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ device_id: memoryDeviceId, tier_id: memoryTierId }),
      }).catch(() => { /* best-effort */ });
    } catch (e) {
      setMemorySetupState("failed");
      setMemorySetupError(e instanceof Error ? e.message : "Network error");
    }
  }

  const isRunning = memorySetupTaskId !== null && memorySetupState !== "done" && memorySetupState !== "failed";

  // "Has default" mode
  if (memoryPlugin !== null && memoryPickerMode === "default" && memoryDefault !== null && memoryDefault !== "none") {
    const def = memoryDefault;
    return (
      <div className="space-y-3">
        <span className="block text-xs text-shell-text-secondary mb-2">Memory Layer</span>
        <div className="px-4 py-3 rounded-lg border border-accent/30 bg-accent/5 flex items-start justify-between gap-2">
          <div>
            <div className="text-sm font-medium">taOSmd memory enabled</div>
            <div className="text-xs text-shell-text-secondary mt-0.5">
              {def.tier_name} on {def.device_id}
            </div>
          </div>
          <div className="flex flex-col items-end gap-1 shrink-0">
            <button
              type="button"
              onClick={() => setMemoryPickerMode("picker")}
              className="text-xs text-blue-400 hover:underline"
            >
              Change
            </button>
            <button
              type="button"
              onClick={() => setMemoryPlugin(null)}
              className="text-xs text-shell-text-tertiary hover:text-shell-text"
            >
              Skip memory for this agent
            </button>
          </div>
        </div>
      </div>
    );
  }

  // "Skipped" mode — user pressed "Skip"
  if (memoryPlugin === null) {
    return (
      <div className="space-y-3">
        <span className="block text-xs text-shell-text-secondary mb-2">Memory Layer</span>
        <div className="px-4 py-3 rounded-lg border border-white/10 bg-shell-bg-deep">
          <div className="text-sm font-medium text-shell-text-tertiary">Memory skipped for this agent</div>
          <div className="text-xs text-shell-text-tertiary mt-0.5">
            The agent will not have persistent memory. You can enable it later in agent settings.
          </div>
        </div>
        <button
          type="button"
          onClick={() => setMemoryPlugin("taosmd")}
          className="text-xs text-blue-400 hover:underline"
        >
          Enable memory
        </button>
      </div>
    );
  }

  // Full picker mode (no default, or user clicked Change)
  const selectedDevice = memoryInstallTargets.find(t => t.name === memoryDeviceId);
  const bestTier = selectedDevice ? bestMemoryTierForDevice(selectedDevice.tier_id) : null;

  return (
    <div className="space-y-4">
      <span className="block text-xs text-shell-text-secondary">Memory Layer</span>

      {/* Device picker */}
      <div>
        <Label className="mb-1.5 block text-xs">Device</Label>
        <div className="flex flex-wrap gap-2">
          {memoryInstallTargets.length === 0 && (
            <span className="text-xs text-shell-text-tertiary">
              {memoryDevicesLoaded ? "No devices available" : "Loading devices…"}
            </span>
          )}
          {memoryInstallTargets.map(t => (
            <button
              key={t.name}
              type="button"
              onClick={() => {
                setMemoryDeviceId(t.name);
                // Auto-suggest the best tier for this device
                if (!memoryTierId) setMemoryTierId(bestMemoryTierForDevice(t.tier_id));
              }}
              className={`px-3 py-1.5 rounded-lg border text-xs transition-colors ${
                memoryDeviceId === t.name
                  ? "border-accent bg-accent/10 text-shell-text"
                  : "border-white/10 bg-shell-bg-deep text-shell-text-secondary hover:bg-white/5"
              }`}
            >
              {t.friendly_name}
              {t.tier_id && (
                <span className="ml-1.5 opacity-60">{t.tier_id}</span>
              )}
            </button>
          ))}
        </div>
      </div>

      {/* Tier picker — only shown once device is selected */}
      {memoryDeviceId && (
        <div>
          <Label className="mb-1.5 block text-xs">Memory tier</Label>
          <div className="grid grid-cols-3 gap-2">
            {(Object.entries(MEMORY_TIER_INFO) as [string, typeof MEMORY_TIER_INFO[string]][]).map(([key, info]) => {
              const isRecommended = key === bestTier;
              const selected = memoryTierId === key;
              return (
                <button
                  key={key}
                  type="button"
                  onClick={() => setMemoryTierId(key)}
                  className={`p-2.5 rounded-lg border text-left transition-colors ${
                    selected
                      ? "border-accent bg-accent/10"
                      : "border-white/10 bg-shell-bg-deep hover:bg-white/5"
                  }`}
                >
                  <div className="flex items-center gap-1 mb-0.5">
                    <span className="text-xs font-semibold">{info.label}</span>
                    {isRecommended && !selected && (
                      <span className="px-1 py-0.5 rounded text-[9px] font-medium bg-emerald-500/20 text-emerald-400 leading-none">
                        recommended
                      </span>
                    )}
                  </div>
                  <p className="text-[10px] text-shell-text-tertiary leading-tight">{info.description}</p>
                </button>
              );
            })}
          </div>
        </div>
      )}

      {/* Setup button + progress */}
      {memoryDeviceId && memoryTierId && (
        <div className="space-y-2">
          {!memorySetupTaskId && (
            <Button
              size="sm"
              className="w-full"
              onClick={handleSetup}
              disabled={isRunning}
            >
              Set up memory layer
            </Button>
          )}
          {memorySetupTaskId && (
            <div className={`px-3 py-2 rounded-lg text-xs ${
              memorySetupState === "done"
                ? "bg-emerald-500/10 border border-emerald-500/30 text-emerald-300"
                : memorySetupState === "failed"
                  ? "bg-red-500/10 border border-red-500/30 text-red-300"
                  : "bg-shell-bg-deep border border-white/5 text-shell-text-secondary"
            }`} role="status" aria-live="polite">
              {memorySetupMsg || "Working…"}
              {memorySetupError && <div className="mt-1 text-red-400">{memorySetupError}</div>}
            </div>
          )}
        </div>
      )}

      {/* Skip link */}
      <button
        type="button"
        onClick={() => setMemoryPlugin(null)}
        className="text-xs text-shell-text-tertiary hover:text-shell-text transition-colors"
      >
        Skip memory for this agent
      </button>
    </div>
  );
}

/* ------------------------------------------------------------------ */
/*  DeployWizard                                                       */
/* ------------------------------------------------------------------ */

function DeployWizard({
  open,
  onClose,
}: {
  open: boolean;
  onClose: (deployed?: boolean) => void;
}) {
  const [step, setStep] = useState(0);

  // Step 0 — Persona
  const [persona, setPersona] = useState<PersonaSelection | null>(null);

  // Step 1
  const [name, setName] = useState("");
  const [customSlug, setCustomSlug] = useState<string | null>(null);
  const [editingSlug, setEditingSlug] = useState(false);
  const [color, setColor] = useState(COLORS[0]);
  const [emoji, setEmoji] = useState<string>("");

  // Step 2
  const [frameworks, setFrameworks] = useState<Framework[]>([]);
  const [selectedFramework, setSelectedFramework] = useState<string>("");
  const [showExperimental, setShowExperimental] = useState(false);

  // Step 3
  const [models, setModels] = useState<Model[]>([]);
  const [modelsLoaded, setModelsLoaded] = useState(false);
  const [selectedModel, setSelectedModel] = useState<string>("");

  // Advanced (no wizard step — defaults to unlimited)
  const [memory, setMemory] = useState("");
  const [cpus, setCpus] = useState("");

  // Step 4 — Memory layer
  // null = no choice yet (show picker); "taosmd" = enabled; null plugin = skipped
  const [memoryPlugin, setMemoryPlugin] = useState<"taosmd" | null>("taosmd");
  const [memoryDeviceId, setMemoryDeviceId] = useState<string | null>(null);
  const [memoryTierId, setMemoryTierId] = useState<string | null>(null);
  // null = loading; "none" = no default; object = has default
  const [memoryDefault, setMemoryDefault] = useState<{ device_id: string; tier_id: string; tier_name: string } | null | "none">(null);
  const [memoryInstallTargets, setMemoryInstallTargets] = useState<Array<{ name: string; friendly_name: string; tier_id: string }>>([]);
  const [memoryDevicesLoaded, setMemoryDevicesLoaded] = useState(false);
  const [memorySetupTaskId, setMemorySetupTaskId] = useState<string | null>(null);
  const [memorySetupState, setMemorySetupState] = useState<string>("pending");
  const [memorySetupMsg, setMemorySetupMsg] = useState<string>("");
  const [memorySetupError, setMemorySetupError] = useState<string | null>(null);
  const [memoryPickerMode, setMemoryPickerMode] = useState<"default" | "picker">("default");

  // Step 5 — Permissions
  const [canReadUserMemory, setCanReadUserMemory] = useState(false);

  // Step 6 — Worker failure policy
  const [onWorkerFailure, setOnWorkerFailure] = useState<"pause" | "fallback" | "escalate-immediately">("pause");
  const [fallbackModels, setFallbackModels] = useState<string[]>([]);
  const [fallbackModelOpen, setFallbackModelOpen] = useState(false);
  const [showAdvanced, setShowAdvanced] = useState(false);
  const [advancedLoaded, setAdvancedLoaded] = useState(false);
  const [systemRamMb, setSystemRamMb] = useState<number | null>(null);
  const [systemCpuCores, setSystemCpuCores] = useState<number | null>(null);

  // KV cache quantization — split K / V / boundary controls.  Visible only
  // when the cluster advertises more than fp16 for the respective axis.
  // Fetched once when the wizard opens from /api/cluster/kv-quant-options.
  const [kvQuantOptions, setKvQuantOptions] = useState<KvQuantOptions>({
    k: ["fp16"],
    v: ["fp16"],
    boundary: false,
    flat: ["fp16"],
  });
  const [kvCacheQuantK, setKvCacheQuantK] = useState<string>("fp16");
  const [kvCacheQuantV, setKvCacheQuantV] = useState<string>("fp16");
  const [kvCacheQuantBoundaryLayers, setKvCacheQuantBoundaryLayers] = useState<number>(0);

  const [deploying, setDeploying] = useState(false);

  const [deployError, setDeployError] = useState<string | null>(null);

  // Try to fetch real data
  useEffect(() => {
    if (!open) return;
    (async () => {
      try {
        const res = await fetch("/api/frameworks", {
          headers: { Accept: "application/json" },
        });
        const ct = res.headers.get("content-type") ?? "";
        if (res.ok && ct.includes("application/json")) {
          const data = await res.json();
          if (Array.isArray(data) && data.length > 0) {
            // Filter out broken adapters entirely
            const visible = data.filter(
              (a: Record<string, unknown>) => a.verification_status !== "broken"
            );
            const mapped: Framework[] = visible.map((a: Record<string, unknown>) => ({
              id: String(a.id),
              name: String(a.name ?? a.id),
              description: String(a.description ?? ""),
              verification_status: (a.verification_status as Framework["verification_status"]) ?? "alpha",
            }));
            // openclaw first, then preserve API order
            mapped.sort((a, b) => {
              if (a.id === "openclaw") return -1;
              if (b.id === "openclaw") return 1;
              return 0;
            });
            setFrameworks(mapped);
          }
        }
      } catch { /* leave frameworks empty, wizard will show nothing selectable */ }
    })();
    // Fetch cluster-wide KV quant options.  The K/V/boundary controls only
    // render when the cluster reports more than fp16 for the relevant axis,
    // so this is a no-op on single-worker fp16-only clusters.
    (async () => {
      try {
        const res = await fetch("/api/cluster/kv-quant-options", {
          headers: { Accept: "application/json" },
        });
        const ct = res.headers.get("content-type") ?? "";
        if (res.ok && ct.includes("application/json")) {
          const data = await res.json();
          // Accept the new K/V/boundary shape.  Fall back to the legacy
          // flat "options" field if the controller hasn't upgraded yet.
          const k: string[] = Array.isArray(data?.k) && data.k.length > 0
            ? data.k
            : Array.isArray(data?.options) && data.options.length > 0
              ? data.options
              : ["fp16"];
          const v: string[] = Array.isArray(data?.v) && data.v.length > 0
            ? data.v
            : Array.isArray(data?.options) && data.options.length > 0
              ? data.options
              : ["fp16"];
          const boundary = Boolean(data?.boundary_layer_protect);
          const flatSet = new Set([...k, ...v]);
          setKvQuantOptions({
            k,
            v,
            boundary,
            flat: Array.from(flatSet).sort(),
          });
        }
      } catch {
        // Fall back to computing from the workers fetch below.
        // If that also fails we stay on the fp16-only default and no
        // controls are shown.
      }
    })();

    (async () => {
      const localModels: Model[] = [];
      try {
        const res = await fetch("/api/models", {
          headers: { Accept: "application/json" },
        });
        const ct = res.headers.get("content-type") ?? "";
        if (res.ok && ct.includes("application/json")) {
          const data = await res.json();
          // /api/models returns { models: [...], downloaded_files: [...] }
          const list: Record<string, unknown>[] = Array.isArray(data)
            ? data
            : Array.isArray(data?.models)
              ? data.models
              : [];
          // Only include models the user has actually downloaded.
          const downloaded = list.filter((m) => m.has_downloaded_variant === true);
          localModels.push(
            ...downloaded.map((m) => ({
              id: String(m.id),
              name: String(m.name ?? m.id),
              host: "controller",
              hostKind: "controller" as const,
            }))
          );
        }
      } catch { /* leave local models empty */ }

      // Union in cluster-worker-hosted models. Each worker reports its
      // backends[].models[] via heartbeat; any of them is a valid deploy
      // target as far as the wizard is concerned — the user can pick it
      // and the controller-side scheduler/copy will route accordingly.
      const workerModels: Model[] = [];
      try {
        const workers = await fetchClusterWorkers();
        for (const a of workersToAggregated(workers)) {
          workerModels.push({
            id: a.id,
            name: a.name,
            host: a.host,
            hostKind: "worker",
          });
        }
        // Fall back: if the dedicated kv-quant-options fetch above already
        // found K or V options beyond fp16, leave those results.  Otherwise
        // compute from the workers we just fetched so we avoid a second
        // round-trip.
        setKvQuantOptions((prev) => {
          if (prev.k.length > 1 || prev.v.length > 1 || prev.boundary) return prev;
          return availableKvQuantOptions(workers);
        });
      } catch { /* ignore */ }

      // Fetch cloud provider models from LiteLLM's /v1/models passthrough —
      // this is the authoritative list of what the proxy actually routes,
      // so it stays in sync with provider config reloads and catches models
      // that /api/providers misses (e.g. kilocode's "kilo-auto/free" alias
      // when the upstream /models probe failed but the yaml seed registered).
      // We fetch /api/providers in parallel to build an id→provider-name
      // map so each LiteLLM entry still surfaces under the right group.
      const cloudModels: Model[] = [];
      try {
        const [modelsRes, providersRes] = await Promise.all([
          fetch("/api/providers/models?refresh=true", {
            headers: { Accept: "application/json" },
          }),
          fetch("/api/providers", {
            headers: { Accept: "application/json" },
          }),
        ]);

        // Build id → (provider-name, kind) map. We classify providers
        // into 'cloud' (CLOUD_PROVIDER_TYPES e.g. openai / anthropic /
        // openrouter / kilocode / openai-compatible) and 'network' —
        // anything whose `source` field starts with `worker:` is a
        // remote worker / network-attached backend (remote ollama,
        // remote llama.cpp, etc., per ProvidersApp's classification).
        // Network providers map onto the picker's 'worker' source tab
        // since that's what they conceptually are. Filed as #356 —
        // network providers were silently dropped before.
        // Three lanes: cloud, worker (network-attached, source starts
        // with "worker:"), and controller (everything else — backends
        // configured directly on this controller, e.g. a local ollama
        // or llama-cpp). Filed under #356 — johny reported (2026-05-08)
        // that local-provider models were still being dropped after the
        // network fix in #422. Surface them in the picker's local lane
        // alongside catalog-installed models.
        const providerByModelId = new Map<string, { name: string; kind: "cloud" | "worker" | "controller" }>();
        const cloudProviderNames = new Set<string>();
        try {
          const pct = providersRes.headers.get("content-type") ?? "";
          if (providersRes.ok && pct.includes("application/json")) {
            const providers = await providersRes.json();
            for (const p of (Array.isArray(providers) ? providers : [])) {
              const isCloud = (CLOUD_PROVIDER_TYPES as readonly string[]).includes(p.type);
              const isNetwork = typeof p.source === "string" && p.source.startsWith("worker:");
              const kind: "cloud" | "worker" | "controller" =
                isCloud ? "cloud" : isNetwork ? "worker" : "controller";
              const pname = p.name ?? p.type;
              if (kind === "cloud") cloudProviderNames.add(pname);
              const pModels: { id?: string; name?: string }[] = Array.isArray(p.models) ? p.models : [];
              for (const m of pModels) {
                const mid = m.id ?? m.name;
                if (mid && !providerByModelId.has(mid)) {
                  providerByModelId.set(mid, { name: pname, kind });
                }
              }
            }
          }
        } catch { /* treat providers as empty, still render what LiteLLM knows */ }

        const mct = modelsRes.headers.get("content-type") ?? "";
        if (modelsRes.ok && mct.includes("application/json")) {
          const body = await modelsRes.json();
          const data: { id?: string }[] = Array.isArray(body?.data) ? body.data : [];
          const seen = new Set<string>();
          for (const entry of data) {
            const mid = entry?.id;
            if (!mid || typeof mid !== "string") continue;
            // Skip the internal alias entries LiteLLM exposes for routing
            // defaults — they'd show up as a confusing "default" entry.
            if (mid === "default" || mid === "taos-embedding-default") continue;
            const provider = providerByModelId.get(mid);
            if (!provider) continue; // not a known provider — LiteLLM internal alias
            const key = `${provider.kind}:${provider.name}:${mid}`;
            if (seen.has(key)) continue;
            seen.add(key);
            // Route into the lane that matches the provider's kind so the
            // picker shows it under the right tab. Controller-hosted local
            // providers go into localModels alongside catalog installs.
            const target =
              provider.kind === "cloud" ? cloudModels :
              provider.kind === "worker" ? workerModels :
              localModels;
            target.push({
              id: mid,
              name: `${mid} (${provider.name})`,
              host: provider.name,
              hostKind: provider.kind,
            });
          }
        }
      } catch { /* ignore */ }

      // Dedupe: if the controller and a worker both report the same model id,
      // prefer the controller entry. Worker entries with duplicate (host,id)
      // pairs (e.g. same model under two backends) are kept once per worker.
      const seen = new Set<string>();
      const union: Model[] = [];
      for (const m of [...localModels, ...workerModels, ...cloudModels]) {
        const key = `${m.hostKind ?? "?"}:${m.host ?? "?"}:${m.id}`;
        if (seen.has(key)) continue;
        seen.add(key);
        union.push(m);
      }
      setModels(union);
      setModelsLoaded(true);
    })();
  }, [open]);

  // Reset when opened
  useEffect(() => {
    if (open) {
      setStep(0);
      setPersona(null);
      setName("");
      setCustomSlug(null);
      setEditingSlug(false);
      setColor(COLORS[0]);
      setEmoji("");
      setSelectedFramework("");
      setShowExperimental(false);
      setSelectedModel("");
      setModels([]);
      setModelsLoaded(false);
      setMemory("");
      setCpus("");
      setMemoryPlugin("taosmd");
      setMemoryDeviceId(null);
      setMemoryTierId(null);
      setMemoryDefault(null);
      setMemoryInstallTargets([]);
      setMemorySetupTaskId(null);
      setMemorySetupState("pending");
      setMemorySetupMsg("");
      setMemorySetupError(null);
      setMemoryPickerMode("default");
      setCanReadUserMemory(false);
      setOnWorkerFailure("pause");
      setFallbackModels([]);
      setKvQuantOptions({ k: ["fp16"], v: ["fp16"], boundary: false, flat: ["fp16"] });
      setKvCacheQuantK("fp16");
      setKvCacheQuantV("fp16");
      setKvCacheQuantBoundaryLayers(0);
      setShowAdvanced(false);
      setAdvancedLoaded(false);
      setSystemRamMb(null);
      setSystemCpuCores(null);
      setDeploying(false);
      setDeployError(null);
    }
  }, [open]);

  // Derive default policy heuristic: if fallback models are configured use
  // "fallback", otherwise "pause".
  useEffect(() => {
    if (fallbackModels.length > 0 && onWorkerFailure === "pause") {
      setOnWorkerFailure("fallback");
    }
  }, [fallbackModels, onWorkerFailure]);


  if (!open) return null;

  const STEPS = ["Persona", "Name & Color", "Framework", "Model", "Memory", "Permissions", "Failure Policy", "Review"];

  const canNext = () => {
    if (step === 0) return persona !== null;
    if (step === 1) {
      if (name.trim().length === 0) return false;
      if (customSlug !== null && !isValidSlug(customSlug)) return false;
      return true;
    }
    if (step === 2) return selectedFramework.length > 0;
    if (step === 3) return selectedModel.length > 0;
    // Step 4 (Memory): always advanceable — user can skip
    if (step === 4) {
      // Block only while a setup task is actively running
      if (memorySetupTaskId && memorySetupState !== "done" && memorySetupState !== "failed") return false;
      return true;
    }
    return true;
  };

  const totalSteps = STEPS.length;
  const reviewStep = totalSteps - 1;

  async function handleDeploy() {
    setDeploying(true);
    setDeployError(null);
    try {
      const memMb = memory ? parseInt(memory, 10) : null;
      const memoryLimit = memMb === null ? null : memMb >= 1024 ? `${Math.round(memMb / 1024)}GB` : `${memMb}MB`;
      const res = await fetch("/api/agents/deploy", {
        method: "POST",
        headers: { "Content-Type": "application/json", Accept: "application/json" },
        body: JSON.stringify({
          name: customSlug || name.trim(),
          framework: selectedFramework,
          model: selectedModel,
          color,
          emoji: emoji.trim() || null,
          memory_limit: memoryLimit,
          cpu_limit: cpus ? parseInt(cpus, 10) : null,
          can_read_user_memory: canReadUserMemory,
          on_worker_failure: onWorkerFailure,
          fallback_models: fallbackModels,
          kv_cache_quant_k: kvCacheQuantK,
          kv_cache_quant_v: kvCacheQuantV,
          kv_cache_quant_boundary_layers: kvCacheQuantBoundaryLayers,
          soul_md: persona?.soul_md ?? "",
          agent_md: persona?.agent_md ?? "",
          source_persona_id: persona?.source_persona_id ?? null,
          save_to_library: persona?.save_to_library ?? null,
          // Memory layer fields from step 4
          memory_plugin: memoryPlugin ?? null,
          memory_config: (memoryPlugin && memoryDeviceId && memoryTierId)
            ? { device_id: memoryDeviceId, tier_id: memoryTierId }
            : undefined,
        }),
      });
      if (!res.ok) {
        let msg = `Deploy failed (${res.status})`;
        try {
          const err = await res.json();
          if (err?.error) msg = String(err.error);
        } catch { /* ignore */ }
        setDeployError(msg);
        setDeploying(false);
        return;
      }
      onClose(true);
    } catch (e) {
      setDeployError(e instanceof Error ? e.message : "Network error");
      setDeploying(false);
    }
  }

  return (
    <div
      className="absolute inset-0 z-50 flex items-center justify-center bg-black/60 backdrop-blur-sm p-4"
      style={{
        paddingTop: "calc(1rem + env(safe-area-inset-top, 0px))",
        paddingBottom: "calc(1rem + env(safe-area-inset-bottom, 0px))",
      }}
      onClick={() => onClose()}
      role="dialog"
      aria-modal="true"
      aria-label="Deploy Agent"
    >
      <div
        className="w-full max-w-lg max-h-full min-h-0 bg-shell-surface rounded-xl border border-white/10 shadow-2xl overflow-hidden flex flex-col"
        onClick={(e) => e.stopPropagation()}
      >
        {/* Header */}
        <div className="flex items-center justify-between px-5 py-4 border-b border-white/5 shrink-0">
          <div className="flex items-center gap-2">
            <Play size={16} className="text-accent" />
            <h2 className="text-sm font-semibold">Deploy Agent</h2>
          </div>
          <Button
            variant="ghost"
            size="icon"
            className="h-7 w-7"
            onClick={() => onClose()}
            aria-label="Close wizard"
          >
            <X size={16} />
          </Button>
        </div>

        {/* Step indicators */}
        <div className="flex items-center gap-1 px-5 py-3 border-b border-white/5 shrink-0 overflow-x-auto">
          {STEPS.map((label, i) => (
            <div key={label} className="flex items-center gap-1">
              <div
                className={`w-6 h-6 rounded-full flex items-center justify-center text-[10px] font-medium transition-colors ${
                  i < step
                    ? "bg-accent/20 text-accent"
                    : i === step
                      ? "bg-accent text-white"
                      : "bg-white/5 text-shell-text-tertiary"
                }`}
              >
                {i < step ? <Check size={12} /> : i + 1}
              </div>
              <span
                className={`text-[11px] hidden sm:inline ${
                  i === step ? "text-shell-text" : "text-shell-text-tertiary"
                }`}
              >
                {label}
              </span>
              {i < STEPS.length - 1 && (
                <div className="w-4 h-px bg-white/10 mx-0.5" />
              )}
            </div>
          ))}
        </div>

        {/* Body */}
        <div className="px-5 py-5 flex-1 min-h-0 overflow-y-auto">
          {/* Step 0: Persona */}
          {step === 0 && (
            <Card className="p-0 border-0 bg-transparent shadow-none">
              <PersonaPicker
                onSelect={(s) => {
                  setPersona(s);
                  setStep(1);
                }}
              />
            </Card>
          )}

          {/* Step 1: Name + Color */}
          {step === 1 && (
            <Card className="p-0 border-0 bg-transparent shadow-none space-y-4">
              <div>
                <Label htmlFor="agent-name" className="mb-1.5 block">
                  Agent Name
                </Label>
                <Input
                  id="agent-name"
                  type="text"
                  value={name}
                  onChange={(e) => setName(e.target.value)}
                  placeholder="my-agent"
                  autoFocus
                />
                {(() => {
                  const derivedSlug = slugifyClient(name);
                  const slug = customSlug ?? derivedSlug;
                  const slugInvalid = customSlug !== null && !isValidSlug(customSlug);
                  return (
                    <>
                      <div className="text-xs opacity-60 mt-1">
                        Slug: <code>{slug || "—"}</code>{" "}
                        <button
                          type="button"
                          onClick={() => {
                            setCustomSlug(customSlug ?? derivedSlug);
                            setEditingSlug(true);
                          }}
                          className="text-blue-400 hover:underline"
                        >
                          edit
                        </button>
                      </div>
                      {editingSlug && (
                        <Input
                          value={customSlug ?? derivedSlug}
                          onChange={(e) => setCustomSlug(e.target.value)}
                          onBlur={() => setEditingSlug(false)}
                          className="mt-1 text-sm"
                          aria-label="Edit slug"
                          pattern={SLUG_REGEX.source}
                        />
                      )}
                      {slugInvalid && (
                        <p className="mt-1 text-xs text-red-400">
                          Slug must match <code>[a-z0-9][a-z0-9-]&#123;0,62&#125;</code>
                        </p>
                      )}
                    </>
                  );
                })()}
              </div>
              <div>
                <Label className="mb-1.5 block">Color</Label>
                <div className="flex gap-2" role="radiogroup" aria-label="Agent color">
                  {COLORS.map((c) => (
                    <button
                      key={c}
                      onClick={() => setColor(c)}
                      className={`w-7 h-7 rounded-full border-2 transition-all ${
                        color === c ? "border-white scale-110" : "border-transparent"
                      }`}
                      style={{ backgroundColor: c }}
                      role="radio"
                      aria-checked={color === c}
                      aria-label={c}
                    />
                  ))}
                </div>
              </div>
              <div>
                <Label htmlFor="agent-emoji" className="mb-1.5 block">
                  Emoji
                  <span className="ml-1.5 font-normal text-shell-text-tertiary">
                    (defaults to the framework icon)
                  </span>
                </Label>
                <Input
                  id="agent-emoji"
                  type="text"
                  value={emoji}
                  onChange={(e) => {
                    setEmoji(e.target.value);
                  }}
                  placeholder="\u{1F916}"
                  aria-describedby="agent-emoji-desc"
                  className="max-w-[8rem] text-lg"
                />
                <p
                  id="agent-emoji-desc"
                  className="mt-1 text-xs text-shell-text-tertiary"
                >
                  Paste any unicode emoji, or use the picker. Leave empty to
                  show no emoji.
                </p>
                <div className="mt-2">
                  <EmojiPickerField value={emoji} onChange={setEmoji} />
                </div>
              </div>
            </Card>
          )}

          {/* Step 2: Framework */}
          {step === 2 && (
            <div className="space-y-2">
              <span className="block text-xs text-shell-text-secondary mb-2">Select Framework</span>
              {frameworks
                .filter((fw) => fw.verification_status !== "alpha" || showExperimental)
                .map((fw) => {
                  const isAlpha = fw.verification_status === "alpha";
                  const selectable = !isAlpha || showExperimental;
                  return (
                    <button
                      key={fw.id}
                      onClick={() => selectable ? setSelectedFramework(fw.id) : undefined}
                      disabled={!selectable}
                      aria-disabled={!selectable}
                      className={`w-full text-left px-4 py-3 rounded-lg border transition-colors ${
                        !selectable
                          ? "border-white/5 bg-shell-bg-deep opacity-40 cursor-not-allowed"
                          : selectedFramework === fw.id
                            ? "border-accent bg-accent/10"
                            : "border-white/5 bg-shell-bg-deep hover:bg-white/5"
                      }`}
                    >
                      <div className="flex items-center gap-2">
                        <span className={`text-sm font-medium ${isAlpha ? "text-shell-text-tertiary" : ""}`}>
                          {fw.name}
                        </span>
                        {fw.verification_status === "beta" && (
                          <span className="px-1.5 py-0.5 rounded text-[10px] font-medium bg-amber-500/20 text-amber-400 leading-none">
                            Beta
                          </span>
                        )}
                        {fw.verification_status === "alpha" && (
                          <span className="px-1.5 py-0.5 rounded text-[10px] font-medium bg-zinc-500/20 text-zinc-400 leading-none">
                            Alpha · Testing
                          </span>
                        )}
                      </div>
                      <div className={`text-xs mt-0.5 ${isAlpha ? "text-shell-text-tertiary" : "text-shell-text-secondary"}`}>
                        {fw.description}
                      </div>
                    </button>
                  );
                })}
              {/* Alpha toggle */}
              <label
                htmlFor="show-experimental-frameworks"
                className="flex items-center gap-2 mt-3 cursor-pointer select-none"
              >
                <input
                  id="show-experimental-frameworks"
                  type="checkbox"
                  checked={showExperimental}
                  onChange={(e) => {
                    setShowExperimental(e.target.checked);
                    // Deselect if currently selected framework becomes hidden
                    if (!e.target.checked) {
                      const fw = frameworks.find((f) => f.id === selectedFramework);
                      if (fw?.verification_status === "alpha") setSelectedFramework("");
                    }
                  }}
                  className="accent-accent"
                />
                <span className="text-xs text-shell-text-secondary">Show alpha / in testing frameworks</span>
              </label>
            </div>
          )}

          {/* Step 3: Model */}
          {step === 3 && (
            <div className="space-y-2">
              {selectedModel ? (
                /* Summary card — shown after a model is picked */
                <div>
                  <span className="block text-xs text-shell-text-secondary mb-2">Selected Model</span>
                  <div className="px-4 py-3 rounded-lg border border-accent/30 bg-accent/5 flex items-start justify-between gap-2">
                    <div className="min-w-0">
                      <div className="flex items-center gap-1.5 min-w-0">
                        <div className="text-sm font-medium truncate">
                          {models.find(m => m.id === selectedModel)?.name ?? selectedModel}
                        </div>
                        {(() => {
                          const m = models.find(mo => mo.id === selectedModel);
                          return m?.host && m.hostKind !== "controller" ? (
                            <span className={HOST_BADGE_CLASS}>{m.host}</span>
                          ) : null;
                        })()}
                      </div>
                      <div className="text-xs text-shell-text-tertiary mt-0.5">{selectedModel}</div>
                    </div>
                    <button
                      onClick={() => setSelectedModel("")}
                      className="text-xs text-shell-text-tertiary hover:text-shell-text shrink-0 mt-0.5 transition-colors"
                    >
                      Change
                    </button>
                  </div>
                </div>
              ) : (
                /* Tiered picker — source → provider → list */
                <ModelPickerFlow
                  models={models}
                  modelsLoaded={modelsLoaded}
                  onSelect={(id) => setSelectedModel(id)}
                  onBack={() => setStep(2)}
                />
              )}
            </div>
          )}

          {/* Step 4: Memory layer */}
          {step === 4 && (
            <MemoryWizardStep
              memoryPlugin={memoryPlugin}
              setMemoryPlugin={setMemoryPlugin}
              memoryDeviceId={memoryDeviceId}
              setMemoryDeviceId={setMemoryDeviceId}
              memoryTierId={memoryTierId}
              setMemoryTierId={setMemoryTierId}
              memoryDefault={memoryDefault}
              setMemoryDefault={setMemoryDefault}
              memoryInstallTargets={memoryInstallTargets}
              setMemoryInstallTargets={setMemoryInstallTargets}
              memoryDevicesLoaded={memoryDevicesLoaded}
              setMemoryDevicesLoaded={setMemoryDevicesLoaded}
              memorySetupTaskId={memorySetupTaskId}
              setMemorySetupTaskId={setMemorySetupTaskId}
              memorySetupState={memorySetupState}
              setMemorySetupState={setMemorySetupState}
              memorySetupMsg={memorySetupMsg}
              setMemorySetupMsg={setMemorySetupMsg}
              memorySetupError={memorySetupError}
              setMemorySetupError={setMemorySetupError}
              memoryPickerMode={memoryPickerMode}
              setMemoryPickerMode={setMemoryPickerMode}
            />
          )}

          {/* Step 5: Permissions */}
          {step === 5 && (
            <div className="space-y-4">
              <span className="block text-xs text-shell-text-secondary mb-2">Permissions</span>
              <label
                htmlFor="agent-user-memory"
                className={`flex items-start gap-3 p-3 rounded-lg border cursor-pointer transition-colors ${
                  canReadUserMemory
                    ? "border-accent bg-accent/10"
                    : "border-white/5 bg-shell-bg-deep hover:bg-white/5"
                }`}
              >
                <input
                  id="agent-user-memory"
                  type="checkbox"
                  checked={canReadUserMemory}
                  onChange={(e) => setCanReadUserMemory(e.target.checked)}
                  className="mt-0.5 accent-accent"
                  aria-describedby="agent-user-memory-desc"
                />
                <div className="flex-1">
                  <div className="text-sm font-medium">Allow this agent to read your memory</div>
                  <div
                    id="agent-user-memory-desc"
                    className="text-xs text-shell-text-secondary mt-0.5"
                  >
                    Agents with this permission can search your notes, conversations, and files. Read-only access.
                  </div>
                </div>
              </label>
            </div>
          )}

          {/* Step 6: Failure Policy */}
          {step === 6 && (
            <div className="space-y-4">
              <span className="block text-xs text-shell-text-secondary mb-2">Worker Failure Policy</span>
              <div>
                <Label htmlFor="agent-failure-policy" className="mb-1.5 block">
                  On worker failure
                </Label>
                <select
                  id="agent-failure-policy"
                  value={onWorkerFailure}
                  onChange={(e) => setOnWorkerFailure(e.target.value as typeof onWorkerFailure)}
                  className="flex h-9 w-full rounded-lg border border-white/10 bg-shell-bg-deep px-3 py-1 text-sm text-shell-text focus-visible:outline-none focus-visible:border-accent/40 focus-visible:ring-2 focus-visible:ring-accent/20 transition-colors"
                  aria-describedby="agent-failure-policy-desc"
                >
                  <option value="pause">Pause (wait for human to intervene)</option>
                  <option value="fallback">Fallback (try alternate models before pausing)</option>
                  <option value="escalate-immediately">Escalate immediately (notify at first sign of trouble)</option>
                </select>
                <p id="agent-failure-policy-desc" className="mt-1 text-xs text-shell-text-tertiary">
                  {onWorkerFailure === "pause" && "Retries briefly, then pauses and notifies you."}
                  {onWorkerFailure === "fallback" && "Retries, falls back to alternate models, then pauses if all fail."}
                  {onWorkerFailure === "escalate-immediately" && "Notifies you as soon as the first retry fails."}
                </p>
              </div>
              <div>
                <Label className="mb-1.5 block">
                  Fallback models{" "}
                  <span className="font-normal text-shell-text-tertiary">(optional, in priority order)</span>
                </Label>
                <div className="space-y-1.5">
                  {fallbackModels.filter(Boolean).map((m, i) => (
                    <div key={i} className="flex items-center gap-2 px-3 py-2 rounded-lg border border-white/5 bg-shell-bg-deep">
                      <span className="flex-1 text-sm truncate">
                        {models.find(mo => mo.id === m)?.name ?? m}
                      </span>
                      <button
                        onClick={() => setFallbackModels(prev => prev.filter((_, j) => j !== i))}
                        className="text-shell-text-tertiary hover:text-red-400 transition-colors"
                        aria-label={`Remove fallback model ${i + 1}`}
                      >
                        <X size={14} />
                      </button>
                    </div>
                  ))}
                  {modelsLoaded && models.filter(mo => mo.id !== selectedModel && !fallbackModels.includes(mo.id)).length > 0 && (
                    <Button
                      variant="outline"
                      size="sm"
                      onClick={() => setFallbackModelOpen(true)}
                      className="w-full"
                    >
                      <Plus size={13} />
                      Add fallback model
                    </Button>
                  )}
                </div>
                <ModelPickerModal
                  open={fallbackModelOpen}
                  onClose={() => setFallbackModelOpen(false)}
                  models={models.filter(mo => mo.id !== selectedModel && !fallbackModels.includes(mo.id))}
                  modelsLoaded={modelsLoaded}
                  title="Add Fallback Model"
                  onSelect={(id) => setFallbackModels(prev => [...prev, id])}
                />
              </div>
              {/* KV cache quant — split K / V / boundary controls.
                  Each sub-control is only rendered when its axis has more than
                  the fp16 baseline. If the cluster has no TurboQuant-capable
                  workers, this whole section is absent from the DOM. */}
              {(kvQuantOptions.k.length > 1 || kvQuantOptions.v.length > 1 || kvQuantOptions.boundary) && (
                <div className="space-y-3">
                  {kvQuantOptions.k.length > 1 && (
                    <div>
                      <Label htmlFor="agent-kv-quant-k" className="mb-1.5 block">
                        K cache bits
                      </Label>
                      <select
                        id="agent-kv-quant-k"
                        value={kvCacheQuantK}
                        onChange={(e) => setKvCacheQuantK(e.target.value)}
                        className="flex h-9 w-full rounded-lg border border-white/10 bg-shell-bg-deep px-3 py-1 text-sm text-shell-text focus-visible:outline-none focus-visible:border-accent/40 focus-visible:ring-2 focus-visible:ring-accent/20 transition-colors"
                        aria-describedby="agent-kv-quant-k-desc"
                      >
                        {kvQuantOptions.k.map((opt) => (
                          <option key={opt} value={opt}>{opt}</option>
                        ))}
                      </select>
                      <p id="agent-kv-quant-k-desc" className="mt-1 text-xs text-shell-text-tertiary">
                        Quantization for the key cache (-ctk). fp16 is the default.
                      </p>
                    </div>
                  )}
                  {kvQuantOptions.v.length > 1 && (
                    <div>
                      <Label htmlFor="agent-kv-quant-v" className="mb-1.5 block">
                        V cache bits
                      </Label>
                      <select
                        id="agent-kv-quant-v"
                        value={kvCacheQuantV}
                        onChange={(e) => setKvCacheQuantV(e.target.value)}
                        className="flex h-9 w-full rounded-lg border border-white/10 bg-shell-bg-deep px-3 py-1 text-sm text-shell-text focus-visible:outline-none focus-visible:border-accent/40 focus-visible:ring-2 focus-visible:ring-accent/20 transition-colors"
                        aria-describedby="agent-kv-quant-v-desc"
                      >
                        {kvQuantOptions.v.map((opt) => (
                          <option key={opt} value={opt}>{opt}</option>
                        ))}
                      </select>
                      <p id="agent-kv-quant-v-desc" className="mt-1 text-xs text-shell-text-tertiary">
                        Quantization for the value cache (-ctv). fp16 is the default.
                      </p>
                    </div>
                  )}
                  {kvQuantOptions.boundary && (
                    <div>
                      <Label htmlFor="agent-kv-boundary-layers" className="mb-1.5 block">
                        Boundary layers
                      </Label>
                      <input
                        id="agent-kv-boundary-layers"
                        type="number"
                        min={0}
                        max={4}
                        step={1}
                        value={kvCacheQuantBoundaryLayers}
                        onChange={(e) => {
                          const v = Math.max(0, Math.min(4, parseInt(e.target.value, 10) || 0));
                          setKvCacheQuantBoundaryLayers(v);
                        }}
                        className="flex h-9 w-full rounded-lg border border-white/10 bg-shell-bg-deep px-3 py-1 text-sm text-shell-text focus-visible:outline-none focus-visible:border-accent/40 focus-visible:ring-2 focus-visible:ring-accent/20 transition-colors"
                        aria-describedby="agent-kv-boundary-layers-desc"
                      />
                      <p id="agent-kv-boundary-layers-desc" className="mt-1 text-xs text-shell-text-tertiary">
                        Number of leading transformer layers kept in fp16 regardless of KV quant setting. Range 0-4.
                      </p>
                    </div>
                  )}
                </div>
              )}
            </div>
          )}

          {/* Step 7: Review */}
          {step === 7 && (
            <div className="space-y-3">
              <span className="block text-xs text-shell-text-secondary mb-2">Review Configuration</span>
              <div className="rounded-lg bg-shell-bg-deep border border-white/5 divide-y divide-white/5">
                {[
                  ["Name", name],
                  ["Color", color],
                  ["Emoji", emoji.trim() || "—"],
                  ["Framework", frameworks.find((f) => f.id === selectedFramework)?.name ?? selectedFramework],
                  ["Model", models.find((m) => m.id === selectedModel)?.name ?? selectedModel],
                  ["Memory layer", memoryPlugin === null ? "Skipped" : memoryTierId ? `taOSmd · ${memoryTierId} on ${memoryDeviceId}` : "taOSmd (global default)"],
                  ["RAM limit", memory ? (parseInt(memory, 10) >= 1024 ? `${Math.round(parseInt(memory, 10) / 1024)} GB` : `${memory} MB`) : "Unlimited"],
                  ["CPUs", cpus ? `${cpus} Core${cpus !== "1" ? "s" : ""}` : "Unlimited"],
                  ["User Memory", canReadUserMemory ? "Allowed (read-only)" : "Denied"],
                  ["On failure", onWorkerFailure],
                  ["Fallbacks", fallbackModels.filter(Boolean).join(", ") || "none"],
                  // Only include KV quant rows in the review when the cluster
                  // actually offered a choice — avoids surfacing fp16-only
                  // entries that would confuse users who never saw the controls.
                  ...(kvQuantOptions.k.length > 1 ? [["K cache bits", kvCacheQuantK]] : []),
                  ...(kvQuantOptions.v.length > 1 ? [["V cache bits", kvCacheQuantV]] : []),
                  ...(kvQuantOptions.boundary ? [["Boundary layers", String(kvCacheQuantBoundaryLayers)]] : []),
                ].map(([label, value]) => (
                  <div key={label} className="flex items-center justify-between px-4 py-2.5">
                    <span className="text-xs text-shell-text-secondary">{label}</span>
                    <span className="text-sm font-medium flex items-center gap-1.5">
                      {label === "Color" && (
                        <span className="w-3 h-3 rounded-full inline-block" style={{ backgroundColor: value }} />
                      )}
                      {value}
                    </span>
                  </div>
                ))}
              </div>

              {/* Advanced settings collapsible */}
              <div className="mt-1">
                <button
                  onClick={() => {
                    if (!showAdvanced && !advancedLoaded) {
                      fetch("/api/activity", { headers: { Accept: "application/json" } })
                        .then(r => { if (!r.ok) throw new Error(String(r.status)); return r.json(); })
                        .then(data => {
                          setSystemRamMb(data?.hardware?.ram_mb ?? null);
                          setSystemCpuCores(data?.hardware?.cpu?.cores ?? null);
                          setAdvancedLoaded(true);
                        })
                        .catch(() => setAdvancedLoaded(true));
                    }
                    setShowAdvanced(v => !v);
                  }}
                  className="flex items-center gap-1.5 text-xs text-shell-text-tertiary hover:text-shell-text transition-colors"
                  aria-expanded={showAdvanced}
                  aria-controls="agent-advanced-settings"
                >
                  <ChevronRight size={14} className={`transition-transform ${showAdvanced ? "rotate-90" : ""}`} />
                  Advanced settings
                </button>
                <div
                  id="agent-advanced-settings"
                  className={`mt-3 space-y-3 px-4 py-3 rounded-lg bg-shell-bg-deep border border-white/5 ${showAdvanced ? "" : "hidden"}`}
                >
                    <div>
                      <Label htmlFor="agent-memory-adv" className="mb-1.5 block">Memory</Label>
                      <select
                        id="agent-memory-adv"
                        value={memory}
                        onChange={e => setMemory(e.target.value)}
                        className="flex h-9 w-full rounded-lg border border-white/10 bg-shell-bg-deep px-3 py-1 text-sm text-shell-text focus-visible:outline-none focus-visible:border-accent/40 focus-visible:ring-2 focus-visible:ring-accent/20 transition-colors"
                      >
                        <option value="">Unlimited (default)</option>
                        {MEMORY_STEPS_MB
                          .filter(mb => systemRamMb !== null ? mb <= systemRamMb : mb <= 4096)
                          .map(mb => (
                            <option key={mb} value={String(mb)}>
                              {mb >= 1024 ? `${Math.round(mb / 1024)} GB` : `${mb} MB`}
                            </option>
                          ))
                        }
                      </select>
                    </div>
                    <div>
                      <Label htmlFor="agent-cpus-adv" className="mb-1.5 block">CPU Cores</Label>
                      <select
                        id="agent-cpus-adv"
                        value={cpus}
                        onChange={e => setCpus(e.target.value)}
                        className="flex h-9 w-full rounded-lg border border-white/10 bg-shell-bg-deep px-3 py-1 text-sm text-shell-text focus-visible:outline-none focus-visible:border-accent/40 focus-visible:ring-2 focus-visible:ring-accent/20 transition-colors"
                      >
                        <option value="">Unlimited (default)</option>
                        {Array.from({ length: systemCpuCores ?? 4 }, (_, i) => i + 1).map(n => (
                          <option key={n} value={String(n)}>
                            {n} {n === 1 ? "Core" : "Cores"}
                          </option>
                        ))}
                      </select>
                    </div>
                  </div>
              </div>
            </div>
          )}
        </div>

        {/* Error */}
        {deployError && (
          <div
            role="alert"
            className="mx-5 mb-3 px-3 py-2 rounded-lg bg-red-500/15 border border-red-500/30 text-xs text-red-300"
          >
            {deployError}
          </div>
        )}

        {/* Footer — hidden while the inline model picker is active (has its own nav) */}
        {!(step === 3 && !selectedModel) && (
        <div className="flex items-center justify-between px-5 py-3 border-t border-white/5 shrink-0">
          <Button
            variant="outline"
            size="sm"
            onClick={() => (step === 0 ? onClose() : setStep(step - 1))}
          >
            <ChevronLeft size={14} />
            {step === 0 ? "Cancel" : "Back"}
          </Button>

          {step < reviewStep ? (
            <Button
              size="sm"
              onClick={() => setStep(step + 1)}
              disabled={!canNext()}
            >
              Next
              <ChevronRight size={14} />
            </Button>
          ) : (
            <Button
              size="sm"
              onClick={handleDeploy}
              disabled={deploying}
              className="bg-emerald-600 hover:bg-emerald-500 text-white"
            >
              <Play size={13} />
              {deploying ? "Deploying..." : "Deploy Agent"}
            </Button>
          )}
        </div>
        )}
      </div>
    </div>
  );
}

/* ------------------------------------------------------------------ */
/*  Archived agents helpers + panel                                    */
/* ------------------------------------------------------------------ */

function parseArchiveTimestamp(ts: string): Date | null {
  const m = /^(\d{4})(\d{2})(\d{2})T(\d{2})(\d{2})(\d{2})$/.exec(ts);
  if (!m) return null;
  return new Date(Date.UTC(+m[1]!, +m[2]! - 1, +m[3]!, +m[4]!, +m[5]!, +m[6]!));
}

function relativeTimeFromTs(ts: string): string {
  const d = parseArchiveTimestamp(ts);
  if (!d) return ts;
  const diff = Date.now() - d.getTime();
  const mins = Math.floor(diff / 60000);
  if (mins < 1) return "just now";
  if (mins < 60) return `${mins}m ago`;
  const hrs = Math.floor(mins / 60);
  if (hrs < 24) return `${hrs}h ago`;
  const days = Math.floor(hrs / 24);
  if (days < 7) return `${days}d ago`;
  return d.toLocaleDateString();
}

function ArchivedAgentRow({
  entry,
  onRestore,
  onPurge,
}: {
  entry: ArchivedAgent;
  onRestore: (id: string, name: string) => void;
  onPurge: (id: string, name: string) => void;
}) {
  const displayName = entry.original?.display_name || entry.original?.name || entry.archived_slug;
  const color = entry.original?.color || "#6b7280";
  const emoji = resolveAgentEmoji(entry.original?.emoji, entry.original?.framework);
  const model = entry.original?.model;
  const when = relativeTimeFromTs(entry.archived_at);

  return (
    <Card className="flex items-center gap-4 px-4 py-3 hover:bg-shell-surface/50 transition-colors opacity-80">
      <div className="flex items-center gap-2.5 flex-1 min-w-0">
        <span
          className="w-2.5 h-2.5 rounded-full shrink-0"
          style={{ backgroundColor: color }}
          aria-hidden
        />
        <span className="text-base leading-none shrink-0" aria-hidden="true">
          {emoji}
        </span>
        <span className="font-medium text-sm truncate">{displayName}</span>
        {model && (
          <span className="text-[11px] text-shell-text-tertiary truncate">{model}</span>
        )}
      </div>
      <span className="text-xs text-shell-text-tertiary shrink-0">archived {when}</span>
      <div className="flex items-center gap-1">
        <Button
          variant="ghost"
          size="icon"
          className="h-8 w-8 hover:bg-emerald-500/15 hover:text-emerald-400"
          onClick={() => onRestore(entry.id, displayName)}
          aria-label={`Restore ${displayName}`}
          title="Restore agent"
        >
          <RotateCcw size={15} />
        </Button>
        <Button
          variant="ghost"
          size="icon"
          className="h-8 w-8 hover:bg-red-500/15 hover:text-red-400"
          onClick={() => onPurge(entry.id, displayName)}
          aria-label={`Permanently delete ${displayName}`}
          title="Delete permanently"
        >
          <Trash2 size={15} />
        </Button>
      </div>
    </Card>
  );
}

function ArchivedAgentsPanel({
  archived,
  onRestore,
  onPurge,
}: {
  archived: ArchivedAgent[];
  onRestore: (id: string, name: string) => void;
  onPurge: (id: string, name: string) => void;
}) {
  const [expanded, setExpanded] = useState(false);
  if (archived.length === 0) return null;
  return (
    <section className="mt-4" aria-label="Archived agents">
      <button
        onClick={() => setExpanded(v => !v)}
        className="flex items-center gap-2 text-xs text-shell-text-secondary hover:text-shell-text transition-colors mb-2"
        aria-expanded={expanded}
        aria-controls="archived-agents-panel"
      >
        <ChevronRight size={14} className={`transition-transform ${expanded ? "rotate-90" : ""}`} />
        <Archive size={13} />
        Archived ({archived.length})
      </button>
      <div
        id="archived-agents-panel"
        className={`space-y-2 ${expanded ? "" : "hidden"}`}
      >
        {archived.map(entry => (
          <ArchivedAgentRow
            key={entry.id}
            entry={entry}
            onRestore={onRestore}
            onPurge={onPurge}
          />
        ))}
      </div>
    </section>
  );
}

/* ------------------------------------------------------------------ */
/*  AgentsApp (main)                                                   */
/* ------------------------------------------------------------------ */

export function AgentsApp({ windowId: _windowId }: { windowId: string }) {
  const [agents, setAgents] = useState<Agent[]>([]);
  const [archived, setArchived] = useState<ArchivedAgent[]>([]);
  const [loading, setLoading] = useState(true);
  const [wizardOpen, setWizardOpen] = useState(false);
  const [detail, setDetail] = useState<{ name: string; tab: DetailTab } | null>(null);
  const [diskStates, setDiskStates] = useState<Record<string, DiskState>>({});
  const [quotaErrors, setQuotaErrors] = useState<Record<string, string>>({});
  const [latestByFramework, setLatestByFramework] = useState<Record<string, LatestVersion>>({});
  const isMobile = useIsMobile();
  const openWindow = useProcessStore((s) => s.openWindow);

  const fetchAgents = useCallback(async () => {
    try {
      const res = await fetch("/api/agents");
      if (res.ok) {
        const ct = res.headers.get("content-type") ?? "";
        if (ct.includes("application/json")) {
          const data = await res.json();
          if (Array.isArray(data)) {
            setAgents(
              data.map((a: Record<string, unknown>) => ({
                name: String(a.name ?? "unknown"),
                display_name: a.display_name ? String(a.display_name) : undefined,
                host: String(a.host ?? "localhost"),
                color: String(a.color ?? "#3b82f6"),
                emoji: a.emoji ? String(a.emoji) : undefined,
                status: String(a.status ?? "stopped") as Agent["status"],
                vectors: Number(a.vectors ?? 0),
                framework: a.framework ? String(a.framework) : undefined,
                paused: Boolean(a.paused),
                on_worker_failure: (a.on_worker_failure as Agent["on_worker_failure"]) ?? "pause",
                fallback_models: Array.isArray(a.fallback_models) ? (a.fallback_models as string[]) : [],
                kv_cache_quant_k: a.kv_cache_quant_k ? String(a.kv_cache_quant_k) : (a.kv_cache_quant ? String(a.kv_cache_quant) : "fp16"),
                kv_cache_quant_v: a.kv_cache_quant_v ? String(a.kv_cache_quant_v) : (a.kv_cache_quant ? String(a.kv_cache_quant) : "fp16"),
                kv_cache_quant_boundary_layers: typeof a.kv_cache_quant_boundary_layers === "number" ? a.kv_cache_quant_boundary_layers : 0,
                framework_version_sha: a.framework_version_sha != null ? String(a.framework_version_sha) : null,
                migrated_to_v2_personas: Boolean(a.migrated_to_v2_personas),
              }))
            );
            setLoading(false);
            return;
          }
        }
      }
    } catch { /* fall through */ }
    setAgents([]);
    setLoading(false);
  }, []);

  const fetchArchived = useCallback(async () => {
    try {
      const res = await fetch("/api/agents/archived");
      if (!res.ok) {
        console.warn(`fetchArchived: ${res.status} ${res.statusText}`);
        return;
      }
      const ct = res.headers.get("content-type") ?? "";
      if (!ct.includes("application/json")) {
        console.warn("fetchArchived: response not JSON, content-type:", ct);
        return;
      }
      const data = await res.json();
      if (Array.isArray(data)) {
        setArchived(data as ArchivedAgent[]);
      }
    } catch (err) {
      // Surface the failure in DevTools so a silent empty-archived list
      // isn't mistaken for "no archived agents". UI keeps prior state.
      console.warn("fetchArchived: network/parse error", err);
    }
  }, []);

  const fetchDiskStates = useCallback(async (agentNames: string[]) => {
    if (agentNames.length === 0) return;
    const results = await Promise.allSettled(
      agentNames.map(async (name) => {
        const res = await fetch(`/api/agents/${encodeURIComponent(name)}/disk`, {
          headers: { Accept: "application/json" },
        });
        if (!res.ok) return null;
        const ct = res.headers.get("content-type") ?? "";
        if (!ct.includes("application/json")) return null;
        const data: DiskState = await res.json();
        return { name, data };
      })
    );
    const next: Record<string, DiskState> = {};
    for (const r of results) {
      // Explicit nullness + structural check: the inner map can return
      // null (when !res.ok or content-type mismatch), and we also guard
      // against malformed data missing the expected fields. Without the
      // shape check, AgentRow would deref undefined fields on render.
      if (
        r.status === "fulfilled" &&
        r.value !== null &&
        r.value !== undefined &&
        typeof r.value.name === "string" &&
        r.value.data
      ) {
        next[r.value.name] = r.value.data;
      }
    }
    setDiskStates(next);
  }, []);

  // Listen for agent-resumed events from the notification toast
  useEffect(() => {
    const handler = () => fetchAgents();
    window.addEventListener("taos:agent-resumed", handler);
    return () => window.removeEventListener("taos:agent-resumed", handler);
  }, [fetchAgents]);

  useEffect(() => {
    fetchLatestFrameworks().then(setLatestByFramework).catch(() => {});
  }, []);

  async function handleResume(name: string) {
    try {
      const res = await fetch(`/api/agents/${encodeURIComponent(name)}/resume`, {
        method: "POST",
        headers: { Accept: "application/json" },
      });
      if (!res.ok) {
        let msg = `Resume failed (${res.status})`;
        try {
          const err = await res.json();
          if (err?.error) msg = String(err.error);
        } catch { /* ignore */ }
        window.alert(msg);
        return;
      }
      fetchAgents();
    } catch (e) {
      window.alert(e instanceof Error ? e.message : "Network error");
    }
  }

  // Fetch disk states whenever agent list changes
  useEffect(() => {
    if (agents.length > 0) {
      fetchDiskStates(agents.map((a) => a.name));
    }
  }, [agents, fetchDiskStates]);

  useEffect(() => {
    fetchAgents();
    fetchArchived();
  }, [fetchAgents, fetchArchived]);

  async function handleDelete(name: string) {
    if (!window.confirm(`Archive "${name}"? It can be restored later from the Archived section.`)) return;
    try {
      const res = await fetch(`/api/agents/${encodeURIComponent(name)}`, {
        method: "DELETE",
        headers: { Accept: "application/json" },
      });
      if (!res.ok) {
        let msg = `Delete failed (${res.status})`;
        try {
          const err = await res.json();
          if (err?.error) msg = String(err.error);
        } catch { /* ignore */ }
        window.alert(msg);
        return;
      }
      if (detail?.name === name) setDetail(null);
      fetchAgents();
      fetchArchived();
    } catch (e) {
      window.alert(e instanceof Error ? e.message : "Network error");
    }
  }

  async function handleRestore(id: string, name: string) {
    if (!window.confirm(`Restore "${name}"?`)) return;
    try {
      const res = await fetch(`/api/agents/archived/${id}/restore`, { method: "POST" });
      if (!res.ok) {
        const err = await res.json().catch(() => ({}));
        window.alert(`Restore failed: ${(err as { error?: string }).error ?? res.status}`);
        return;
      }
      await fetchAgents();
      await fetchArchived();
    } catch (e) {
      window.alert(`Network error: ${String(e)}`);
    }
  }

  async function handlePurge(id: string, name: string) {
    if (!window.confirm(`Permanently delete "${name}"? This cannot be undone.`)) return;
    try {
      const res = await fetch(`/api/agents/archived/${id}`, { method: "DELETE" });
      if (!res.ok) {
        const err = await res.json().catch(() => ({}));
        window.alert(`Permanent delete failed: ${(err as { error?: string }).error ?? res.status}`);
        return;
      }
      await fetchArchived();
    } catch (e) {
      window.alert(`Network error: ${String(e)}`);
    }
  }

  async function handleExpandQuota(name: string, currentGib: number) {
    const newGib = currentGib + 10;
    setQuotaErrors((prev) => { const next = { ...prev }; delete next[name]; return next; });
    try {
      const res = await fetch(`/api/agents/${encodeURIComponent(name)}/quota`, {
        method: "POST",
        headers: { "Content-Type": "application/json", Accept: "application/json" },
        body: JSON.stringify({ size_gib: newGib }),
      });
      if (res.status === 409) {
        setQuotaErrors((prev) => ({
          ...prev,
          [name]: "Cannot resize on this storage backend — run install-time migration to btrfs",
        }));
        return;
      }
      if (!res.ok) {
        let msg = `Expand failed (${res.status})`;
        try { const e = await res.json(); if (e?.error) msg = String(e.error); } catch { /* ignore */ }
        setQuotaErrors((prev) => ({ ...prev, [name]: msg }));
        return;
      }
      await fetchDiskStates([name]);
    } catch (e) {
      setQuotaErrors((prev) => ({ ...prev, [name]: e instanceof Error ? e.message : "Network error" }));
    }
  }

  function handleAuditWithAgent(name: string) {
    // Find the agent's DM channel by convention (agent name is the channel id or name)
    // Dispatch cross-app navigation event — MessagesApp listens for taos:open-messages
    window.dispatchEvent(
      new CustomEvent("taos:open-messages", {
        detail: {
          channelId: name,
          prefillPromptName: "disk-audit",
          prefillAgent: name,
        },
      })
    );
  }

  const handleShortcutLaunch = useCallback(async (agentId: string, shortcut: AgentShortcut) => {
    const res = await fetch(
      `/api/agents/${encodeURIComponent(agentId)}/shortcuts/${shortcut.idx}/launch`,
      { method: "POST", headers: { Accept: "application/json" } }
    );
    if (!res.ok) return;
    const { redirect_url } = await res.json() as { redirect_url: string };
    const kind = shortcut.kind;
    if (kind === "dashboard") {
      const app = getApp("browser");
      if (app) openWindow("browser", app.defaultSize, { initialUrl: redirect_url });
    } else if (kind === "tui" || kind === "container-terminal") {
      const parsed = new URL(redirect_url, window.location.href);
      const ticket = parsed.searchParams.get("t") ?? "";
      const wsUrl = redirect_url
        .replace(/^http:\/\//, "ws://")
        .replace(/^https:\/\//, "wss://");
      const app = getApp("terminal");
      if (app) openWindow("terminal", app.defaultSize, { shortcut: { wsUrl, ticket } });
    }
  }, [openWindow]);

  function handleWizardClose(deployed?: boolean) {
    setWizardOpen(false);
    if (deployed) {
      fetchAgents();
      fetchArchived();
    }
  }

  return (
    <div className="flex flex-col h-full min-h-0 overflow-hidden bg-shell-bg text-shell-text select-none relative">
      {/* Toolbar */}
      <div className="flex items-center justify-between gap-2 px-4 py-3 border-b border-white/5">
        <div className="flex items-center gap-2 min-w-0">
          <Bot size={18} className="text-accent shrink-0" />
          <h1 className="text-sm font-semibold shrink-0">Agents</h1>
          <span className="text-xs text-shell-text-tertiary truncate">
            {agents.length} deployed
          </span>
        </div>
        <Button
          onClick={() => setWizardOpen(true)}
          size="sm"
          className="text-white shadow-lg hover:shadow-xl hover:-translate-y-0.5 hover:brightness-110 border-0 shrink-0"
          style={{ background: "linear-gradient(135deg, #8b92a3, #5b6170)" }}
          aria-label="Deploy new agent"
        >
          <Plus size={14} />
          Deploy Agent
        </Button>
      </div>

      {/* Content */}
      <div className="flex-1 overflow-auto">
        {loading ? (
          <div className="flex items-center justify-center h-full text-shell-text-tertiary text-sm">
            Loading agents...
          </div>
        ) : agents.length === 0 && archived.length === 0 ? (
          <div className="flex flex-col items-center justify-center h-full gap-4 text-shell-text-tertiary">
            <div className="w-20 h-20 rounded-2xl flex items-center justify-center"
              style={{ background: "linear-gradient(135deg, rgba(139,146,163,0.15), rgba(91,97,112,0.08))" }}
            >
              <Bot size={36} className="text-accent/50" />
            </div>
            <div className="text-center">
              <p className="text-base font-medium text-shell-text-secondary mb-1">No agents deployed yet</p>
              <p className="text-xs text-shell-text-tertiary max-w-xs">Deploy your first AI agent to start automating tasks on your device.</p>
            </div>
            <Button
              onClick={() => setWizardOpen(true)}
              className="text-white shadow-lg hover:shadow-xl hover:-translate-y-0.5 hover:brightness-110 border-0 mt-1"
              style={{ background: "linear-gradient(135deg, #8b92a3, #5b6170)" }}
            >
              <Plus size={15} />
              Deploy your first agent
            </Button>
          </div>
        ) : agents.length === 0 ? (
          <div className="p-4">
            <ArchivedAgentsPanel
              archived={archived}
              onRestore={handleRestore}
              onPurge={handlePurge}
            />
          </div>
        ) : (
          <div className="p-4">
            {/* Disk quota notification cards */}
            {agents
              .filter((a) => diskStates[a.name] != null && diskStates[a.name]!.state !== "ok")
              .map((agent) => {
                const ds = diskStates[agent.name]!;
                const isHard = ds.state === "hard";
                return (
                  <div
                    key={`quota-card-${agent.name}`}
                    className={`mb-3 px-4 py-3 rounded-lg border ${
                      isHard
                        ? "bg-red-500/10 border-red-500/30"
                        : "bg-amber-500/10 border-amber-500/30"
                    }`}
                    role="alert"
                    aria-label={`Disk quota warning for ${agent.display_name || agent.name}`}
                  >
                    <div className={`text-xs font-medium mb-2 ${isHard ? "text-red-400" : "text-amber-400"}`}>
                      Disk quota {isHard ? "full" : "warning"} — {agent.display_name || agent.name} at {ds.percent}%
                    </div>
                    {quotaErrors[agent.name] && (
                      <div className="text-xs text-red-400 mb-2" role="alert">
                        {quotaErrors[agent.name]}
                      </div>
                    )}
                    <div className="flex items-center gap-2">
                      <Button
                        size="sm"
                        variant="outline"
                        onClick={() => handleExpandQuota(agent.name, ds.quota_gib)}
                        aria-label={`Expand disk quota for ${agent.name} by 10 GB`}
                        className={isHard ? "border-red-500/30 hover:bg-red-500/10" : "border-amber-500/30 hover:bg-amber-500/10"}
                      >
                        <HardDrive size={13} aria-hidden="true" />
                        Expand +10 GB
                      </Button>
                      <Button
                        size="sm"
                        variant="ghost"
                        onClick={() => handleAuditWithAgent(agent.name)}
                        aria-label={`Audit disk usage with ${agent.name}`}
                      >
                        <MessageSquare size={13} aria-hidden="true" />
                        Audit with agent
                      </Button>
                    </div>
                  </div>
                );
              })}
            <div className="space-y-2" role="list" aria-label="Agent list">
              {agents.map((agent) => (
                <AgentRow
                  key={agent.name}
                  agent={agent}
                  diskState={diskStates[agent.name] ?? null}
                  latestByFramework={latestByFramework}
                  onViewLogs={(name) => setDetail({ name, tab: "logs" })}
                  onViewSkills={(name) => setDetail({ name, tab: "skills" })}
                  onViewMessages={(name) => setDetail({ name, tab: "messages" })}
                  onDelete={handleDelete}
                  onResume={handleResume}
                  leftActions={<AgentShortcutRow agentId={agent.name} onLaunch={handleShortcutLaunch} btnCls="h-11 w-11 md:h-8 md:w-8" />}
                />
              ))}
            </div>
            <ArchivedAgentsPanel
              archived={archived}
              onRestore={handleRestore}
              onPurge={handlePurge}
            />
          </div>
        )}
      </div>

      {/* Detail panel (Logs + Skills tabs) */}
      {detail && (() => {
        const agent = agents.find((a) => a.name === detail.name);
        if (!agent) return null;
        if (isMobile) {
          // Render in-place over the agent list, scoped to the AgentsApp's
          // relative wrapper. The previous implementation portaled to
          // document.body with fixed inset-0, which escaped the
          // MobileAppWindow chrome and covered the whole viewport — the
          // user lost the app's title bar and the safe-area insets had to
          // be reapplied here. Inline absolute keeps the app window
          // visible and matches how ProjectsApp renders its workspace.
          return (
            <div
              role="dialog"
              aria-modal="true"
              aria-label={`Agent details — ${agent.display_name || agent.name}`}
              className="absolute inset-0 z-30 flex flex-col bg-shell-bg text-zinc-200"
            >
              <div className="flex items-center gap-2 border-b border-zinc-800 bg-zinc-900 px-3 py-2">
                <button
                  type="button"
                  aria-label="Back to agents"
                  onClick={() => setDetail(null)}
                  className="rounded-lg px-2 py-1 text-sm text-zinc-300"
                >
                  ‹ Back
                </button>
                <div className="flex-1 truncate text-center text-sm font-medium text-zinc-200">
                  {agent.display_name || agent.name}
                </div>
                <span className="w-10" aria-hidden="true" />
              </div>
              <div className="flex flex-1 min-h-0 flex-col overflow-hidden">
                <AgentDetailPanel
                  agent={agent}
                  initialTab={detail.tab}
                  onClose={() => setDetail(null)}
                  onAgentUpdated={fetchAgents}
                  fullHeight
                />
              </div>
            </div>
          );
        }
        return (
          <AgentDetailPanel
            agent={agent}
            initialTab={detail.tab}
            onClose={() => setDetail(null)}
            onAgentUpdated={fetchAgents}
          />
        );
      })()}

      {/* Deploy wizard overlay */}
      <DeployWizard open={wizardOpen} onClose={handleWizardClose} />
    </div>
  );
}

// TODO (#144): Cluster widget KV quant chip.
// Once a backend actually reports a type beyond fp16, add a small chip to the
// loaded-model row in ActivityApp's cluster widget showing the active KV
// quant mode.  The chip should be absent when the mode is fp16 (the default),
// visible for anything else.  The data is already in ClusterWorker
// kv_cache_quant_support and flows through to the model list in
// /api/cluster/backends.  No dead widget today — wait for a real reporter.

