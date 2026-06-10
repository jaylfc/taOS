import { useState, useEffect, useCallback } from "react";
import { useIsMobile } from "@/hooks/use-is-mobile";
import { Bot, Plus, HardDrive, MessageSquare } from "lucide-react";
import { fetchLatestFrameworks, LatestVersion } from "@/lib/framework-api";
import type { AgentShortcut } from "@/hooks/use-agent-shortcuts";
import { useProcessStore } from "@/stores/process-store";
import { getApp } from "@/registry/app-registry";
import { useNotificationStore } from "@/stores/notification-store";
import { deriveTerminalShortcutTarget } from "./shortcut-launch";
import { Button } from "@/components/ui";
import { type Agent, type DiskState, type ArchivedAgent } from "./agents/types";
import { AgentRow } from "./agents/AgentRow";
import { AgentDetailPanel, type DetailTab } from "./agents/AgentDetailPanel";
import { TaosAgentDetailPanel } from "./agents/TaosAgentDetailPanel";
import { DeployWizard } from "./agents/DeployWizard";
import { ArchivedAgentsPanel } from "./agents/ArchivedAgents";
import { RegistryPanel } from "./agents/RegistryPanel";
import { fetchTaosAgentConfig } from "@/lib/taos-agent-api";

/* ------------------------------------------------------------------ */
/*  AgentsApp (main)                                                   */
/* ------------------------------------------------------------------ */

// Minimal fixed representation of the taOS system agent as an Agent shape.
// Status is always "running" — it is host-resident and cannot be stopped.
const TAOS_AGENT_STUB: Agent = {
  name: "taos-agent",
  display_name: "taOS agent",
  host: "localhost",
  color: "#6366f1",
  emoji: "🤖",
  status: "running",
  vectors: 0,
  framework: "opencode",
  paused: false,
};

export function AgentsApp({ windowId: _windowId }: { windowId: string }) {
  const [agents, setAgents] = useState<Agent[]>([]);
  const [archived, setArchived] = useState<ArchivedAgent[]>([]);
  const [loading, setLoading] = useState(true);
  const [wizardOpen, setWizardOpen] = useState(false);
  const [detail, setDetail] = useState<{ name: string; tab: DetailTab } | null>(null);
  const [taosDetailOpen, setTaosDetailOpen] = useState(false);
  const [diskStates, setDiskStates] = useState<Record<string, DiskState>>({});
  const [quotaErrors, setQuotaErrors] = useState<Record<string, string>>({});
  const [latestByFramework, setLatestByFramework] = useState<Record<string, LatestVersion>>({});
  // Hydrate the taOS agent stub with live model info (display only — the detail
  // panel fetches its own config on open). We only use this to show the current
  // model in the row's framework pill area; failures are silently ignored.
  const [taosModel, setTaosModel] = useState<string | undefined>(undefined);
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
    fetchTaosAgentConfig().then((cfg) => setTaosModel(cfg.model ?? undefined)).catch(() => {});
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
        useNotificationStore.getState().addNotification({
          source: name, title: "Resume failed", body: msg, level: "error",
        });
        return;
      }
      fetchAgents();
    } catch (e) {
      useNotificationStore.getState().addNotification({
        source: name, title: "Resume failed",
        body: e instanceof Error ? e.message : "Network error", level: "error",
      });
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
    const failed = (body: string) =>
      useNotificationStore.getState().addNotification({
        source: agentId,
        title: `Couldn't open ${shortcut.label}`,
        body,
        level: "error",
        icon: "terminal",
      });

    let res: Response;
    try {
      res = await fetch(
        `/api/agents/${encodeURIComponent(agentId)}/shortcuts/${shortcut.idx}/launch`,
        { method: "POST", headers: { Accept: "application/json" } }
      );
    } catch {
      failed("Couldn't reach the server.");
      return;
    }
    if (!res.ok) {
      let detail = `Request failed (${res.status}).`;
      try {
        const e = await res.json();
        if (e?.detail || e?.error) detail = String(e.detail ?? e.error);
      } catch { /* keep generic detail */ }
      failed(detail);
      return;
    }

    let redirect_url: unknown;
    try {
      ({ redirect_url } = await res.json() as { redirect_url?: unknown });
    } catch {
      failed("Server returned an unexpected response.");
      return;
    }
    if (typeof redirect_url !== "string" || !redirect_url) {
      failed("Server response was missing a launch URL.");
      return;
    }
    // On mobile a freshly opened window only shows once it's the active window
    // (App.tsx renders MobileAppWindow for activeWindowId). openWindow alone
    // leaves it invisible, so announce the new window id for activation.
    const surface = (wid: string | undefined) => {
      if (isMobile && wid) {
        window.dispatchEvent(new CustomEvent("taos:activate-window", { detail: { windowId: wid } }));
      }
    };
    const kind = shortcut.kind;
    if (kind === "dashboard") {
      const app = getApp("browser");
      if (app) surface(openWindow("browser", app.defaultSize, { initialUrl: redirect_url }));
    } else if (kind === "tui" || kind === "container-terminal") {
      const { ticket, wsUrl, redeemUrl } = deriveTerminalShortcutTarget(
        redirect_url,
        agentId,
        shortcut.idx,
        window.location.href,
      );
      // Establish the taos_shortcut session cookie before opening the PTY
      // socket. The WebSocket endpoint authenticates via that cookie, which
      // only GET /redeem sets; the 302 it returns sets the cookie regardless
      // of where the redirect points. Best-effort: a failure just means the
      // socket may fail to auth (the terminal surfaces its own connection
      // error), so we log rather than block — but log non-OK responses too,
      // not only rejected fetches (a 401/500 resolves and wouldn't hit .catch).
      try {
        const redeemRes = await fetch(redeemUrl, { credentials: "include" });
        if (!redeemRes.ok) {
          console.warn(`shortcut /redeem for ${agentId} returned ${redeemRes.status}`);
        }
      } catch (e) {
        console.warn(`shortcut /redeem failed for ${agentId}:`, e);
      }
      const app = getApp("terminal");
      if (app) surface(openWindow("terminal", app.defaultSize, { shortcut: { wsUrl, ticket } }));
    }
  }, [openWindow, isMobile]);

  function handleWizardClose(deployed?: boolean) {
    setWizardOpen(false);
    if (deployed) {
      fetchAgents();
      fetchArchived();
    }
  }

  // Full-window detail view for a regular agent
  if (detail) {
    const agent = agents.find((a) => a.name === detail.name);
    if (agent) {
      return (
        <div className="flex flex-col h-full min-h-0 overflow-hidden bg-shell-bg text-shell-text select-none">
          {/* Back header */}
          <div className="flex items-center gap-2 px-3 py-2 border-b border-white/5 shrink-0">
            <button
              type="button"
              aria-label="Back to agents"
              onClick={() => setDetail(null)}
              className="flex items-center gap-1 rounded-lg px-2 py-1 text-sm text-shell-text-secondary hover:text-shell-text hover:bg-white/5 transition-colors"
            >
              ← Back
            </button>
            <span className="text-sm font-medium text-shell-text truncate">
              {agent.display_name || agent.name}
            </span>
          </div>
          <div className="flex flex-1 min-h-0 flex-col overflow-hidden">
            <AgentDetailPanel
              agent={agent}
              initialTab={detail.tab}
              onClose={() => setDetail(null)}
              onAgentUpdated={fetchAgents}
              onShortcutLaunch={handleShortcutLaunch}
              fullHeight
            />
          </div>
          <DeployWizard open={wizardOpen} onClose={handleWizardClose} />
        </div>
      );
    }
    // Agent not found — fall through to list (clears stale detail)
    setDetail(null);
  }

  // Full-window detail view for the taOS system agent
  if (taosDetailOpen) {
    return (
      <div className="flex flex-col h-full min-h-0 overflow-hidden bg-shell-bg text-shell-text select-none">
        {/* Back header */}
        <div className="flex items-center gap-2 px-3 py-2 border-b border-white/5 shrink-0">
          <button
            type="button"
            aria-label="Back to agents"
            onClick={() => setTaosDetailOpen(false)}
            className="flex items-center gap-1 rounded-lg px-2 py-1 text-sm text-shell-text-secondary hover:text-shell-text hover:bg-white/5 transition-colors"
          >
            ← Back
          </button>
          <span className="text-sm font-medium text-shell-text truncate">taOS agent</span>
        </div>
        <div className="flex flex-1 min-h-0 flex-col overflow-hidden">
          <TaosAgentDetailPanel onClose={() => setTaosDetailOpen(false)} fullHeight />
        </div>
        <DeployWizard open={wizardOpen} onClose={handleWizardClose} />
      </div>
    );
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
          <div className="flex flex-col h-full min-h-0">
            <div className="p-4">
              <p className="text-xs font-medium text-shell-text-tertiary uppercase tracking-wider mb-2">System agent</p>
              <AgentRow
                agent={{ ...TAOS_AGENT_STUB, display_name: taosModel ? `taOS agent — ${taosModel}` : "taOS agent" }}
                diskState={null}
                latestByFramework={latestByFramework}
                onViewLogs={() => setTaosDetailOpen(true)}
                onViewSkills={() => setTaosDetailOpen(true)}
                onViewMessages={() => setTaosDetailOpen(true)}
                onDelete={() => {}}
                onResume={() => {}}
                protected
              />
            </div>
            <div className="flex flex-col items-center justify-center flex-1 gap-4 text-shell-text-tertiary px-4 pb-8">
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
          </div>
        ) : agents.length === 0 ? (
          <div className="p-4">
            <p className="text-xs font-medium text-shell-text-tertiary uppercase tracking-wider mb-2">System agent</p>
            <AgentRow
              agent={{ ...TAOS_AGENT_STUB, display_name: taosModel ? `taOS agent — ${taosModel}` : "taOS agent" }}
              diskState={null}
              latestByFramework={latestByFramework}
              onViewLogs={() => setTaosDetailOpen(true)}
              onViewSkills={() => setTaosDetailOpen(true)}
              onViewMessages={() => setTaosDetailOpen(true)}
              onDelete={() => {}}
              onResume={() => {}}
              protected
            />
            <ArchivedAgentsPanel
              archived={archived}
              onRestore={handleRestore}
              onPurge={handlePurge}
            />
            <RegistryPanel />
          </div>
        ) : (
          <div className="p-4">
            {/* System agent — always shown above the deployed agents list */}
            <p className="text-xs font-medium text-shell-text-tertiary uppercase tracking-wider mb-2">System agent</p>
            <AgentRow
              agent={{ ...TAOS_AGENT_STUB, display_name: taosModel ? `taOS agent — ${taosModel}` : "taOS agent" }}
              diskState={null}
              latestByFramework={latestByFramework}
              onViewLogs={() => setTaosDetailOpen(true)}
              onViewSkills={() => setTaosDetailOpen(true)}
              onViewMessages={() => setTaosDetailOpen(true)}
              onDelete={() => {}}
              onResume={() => {}}
              protected
            />

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
                />
              ))}
            </div>
            <ArchivedAgentsPanel
              archived={archived}
              onRestore={handleRestore}
              onPurge={handlePurge}
            />
            <RegistryPanel />
          </div>
        )}
      </div>

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
