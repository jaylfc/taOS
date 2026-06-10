import { useState, useEffect, useRef, useCallback } from "react";
import { Bot, Box, ScrollText, X, Wrench, MessageSquare, Archive, Terminal, ExternalLink, Stethoscope, Play, Zap } from "lucide-react";
import { AgentSkillsPanel } from "../AgentSkillsPanel";
import { AgentMessagesPanel } from "../AgentMessagesPanel";
import { AgentTracesPanel } from "./AgentTracesPanel";
import { PersonaTab } from "@/components/agent-settings/PersonaTab";
import { MemoryTab } from "@/components/agent-settings/MemoryTab";
import { FrameworkTab } from "@/components/agent-settings/FrameworkTab";
import { resolveAgentEmoji } from "@/lib/agent-emoji";
import {
  Button,
  Tabs,
  TabsContent,
  TabsList,
  TabsTrigger,
} from "@/components/ui";
import { MigrationBanner } from "@/components/MigrationBanner";
import { type Agent } from "./types";
import { useAgentShortcuts, type AgentShortcut, type ShortcutIcon } from "@/hooks/use-agent-shortcuts";

const SHORTCUT_ICON: Record<ShortcutIcon, React.ComponentType<{ size?: number }>> = {
  terminal: Terminal,
  tui: Wrench,
  dashboard: ExternalLink,
  diagnostic: Stethoscope,
};

/* ------------------------------------------------------------------ */
/*  AgentDetailPanel (Logs + Skills tabs)                              */
/* ------------------------------------------------------------------ */

export type DetailTab = "logs" | "traces" | "persona" | "memory" | "framework" | "skills" | "messages" | "shortcuts";

export function AgentDetailPanel({
  agent,
  initialTab,
  onClose,
  onAgentUpdated,
  onShortcutLaunch,
  fullHeight = false,
}: {
  agent: Agent;
  initialTab: DetailTab;
  onClose: () => void;
  onAgentUpdated: () => void;
  onShortcutLaunch: (agentId: string, shortcut: AgentShortcut) => void;
  fullHeight?: boolean;
}) {
  const [tab, setTab] = useState<DetailTab>(initialTab);
  const [logs, setLogs] = useState<string>("Fetching logs...");
  const scrollRef = useRef<HTMLPreElement>(null);
  const agentName = agent.name;
  const { shortcuts, loading: shortcutsLoading, error: shortcutsError, refetch: refetchShortcuts } = useAgentShortcuts(agentName);

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
    try {
      const res = await fetch(`/api/agents/${encodeURIComponent(agentName)}/dismiss-migration-banner`, { method: "POST" });
      if (res.ok) {
        onAgentUpdated();
      }
    } catch {
      // Network error — banner stays visible; user can retry.
    }
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
            <TabsTrigger value="traces">
              <Zap size={13} className="mr-1.5" />
              Traces
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
            <TabsTrigger value="shortcuts">
              <Terminal size={13} className="mr-1.5" />
              Shortcuts
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
        <TabsContent value="traces" className="h-full mt-0">
          <AgentTracesPanel agentName={agentName} />
        </TabsContent>
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
        <TabsContent value="shortcuts" className="h-full mt-0 overflow-auto">
          <div className="p-4 flex flex-col gap-4">
            <div>
              <p className="text-xs font-medium text-shell-text-tertiary uppercase tracking-wider mb-1">Developer access</p>
              <p className="text-xs text-shell-text-secondary">
                These shortcuts open developer tools for this agent. They are intended for advanced users and debugging — most users will not need them.
              </p>
            </div>
            {shortcutsLoading ? (
              <p className="text-sm text-shell-text-tertiary">Loading shortcuts…</p>
            ) : shortcutsError ? (
              <div className="flex flex-col gap-2">
                <p className="text-sm text-shell-text-secondary">Failed to load shortcuts: {shortcutsError}</p>
                <Button variant="outline" size="sm" className="self-start" onClick={refetchShortcuts}>
                  Retry
                </Button>
              </div>
            ) : shortcuts.length === 0 ? (
              <p className="text-sm text-shell-text-tertiary">No shortcuts configured for this agent.</p>
            ) : (
              <div className="flex flex-col gap-2" role="list" aria-label="Agent shortcuts">
                {shortcuts.map((shortcut) => {
                  const Icon = SHORTCUT_ICON[shortcut.icon] ?? Play;
                  return (
                    <div key={shortcut.idx} role="listitem">
                      <Button
                        variant="outline"
                        className="w-full justify-start gap-3 px-3 py-2 h-auto"
                        onClick={() => onShortcutLaunch(agentName, shortcut)}
                        aria-label={shortcut.label}
                      >
                        <Icon size={15} aria-hidden="true" />
                        <span className="text-sm">{shortcut.label}</span>
                      </Button>
                    </div>
                  );
                })}
              </div>
            )}
          </div>
        </TabsContent>
      </div>
    </Tabs>
    </>
  );
}
