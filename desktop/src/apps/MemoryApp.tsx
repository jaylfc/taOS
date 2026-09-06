import { useState, useEffect, useCallback } from "react";
import { Database, LayoutDashboard, CalendarSearch, GitFork, Users, Settings2 } from "lucide-react";
import { Tabs, TabsList, TabsTrigger, TabsContent } from "@/components/ui/tabs";
import { fetchBackendCapabilities } from "@/lib/memory";
import { Dashboard } from "@/components/memory/Dashboard";
import { SessionBrowser } from "@/components/memory/SessionBrowser";
import { PipelineControl } from "@/components/memory/PipelineControl";
import { AgentMemoryTable } from "@/components/memory/AgentMemoryTable";
import { MemorySettings } from "@/components/memory/MemorySettings";

/* ------------------------------------------------------------------ */
/*  Types                                                              */
/* ------------------------------------------------------------------ */

type Tab = 'dashboard' | 'sessions' | 'pipeline' | 'agents' | 'settings';

interface AgentEntry {
  name: string;
  color: string;
  strategy?: string;
  layers?: string[];
  backend?: string;
  collections?: string[];
}

/* ------------------------------------------------------------------ */
/*  MemoryApp                                                          */
/* ------------------------------------------------------------------ */

export function MemoryApp({ windowId: _windowId }: { windowId: string }) {
  const [activeTab, setActiveTab] = useState<Tab>('dashboard');
  const [capabilities, setCapabilities] = useState<string[]>([]);
  const [agents, setAgents] = useState<AgentEntry[]>([]);

  // Load backend capabilities on mount
  useEffect(() => {
    (async () => {
      const caps = await fetchBackendCapabilities();
      setCapabilities(caps.capabilities ?? []);
    })();
  }, []);

  // Load agents for the agent config tab
  const loadAgents = useCallback(async () => {
    try {
      const res = await fetch('/api/agents', { headers: { Accept: 'application/json' } });
      if (!res.ok) return;
      const ct = res.headers.get('content-type') ?? '';
      if (!ct.includes('application/json')) return;
      const data = await res.json();
      if (!Array.isArray(data)) return;
      setAgents(
        data.map((a: Record<string, unknown>) => ({
          name: String(a.name ?? 'unknown'),
          color: String(a.color ?? '#3b82f6'),
          strategy: typeof a.strategy === 'string' ? a.strategy : undefined,
          layers: Array.isArray(a.layers) ? a.layers.map(String) : undefined,
          backend: typeof a.backend === 'string' ? a.backend : undefined,
          collections: Array.isArray(a.collections) ? a.collections.map(String) : [],
        })),
      );
    } catch { /* ignore */ }
  }, []);

  // Determine which tabs to show based on capabilities
  const hasCapability = (cap: string) =>
    capabilities.length === 0 || capabilities.includes(cap);

  const showSessions = hasCapability('catalog');
  const showPipeline = hasCapability('pipeline');
  const showAgents = true;   // Always visible
  const showSettings = true; // Always visible

  const tabs: { id: Tab; label: string; icon: React.ReactNode; show: boolean }[] = [
    { id: 'dashboard', label: 'Dashboard', icon: <LayoutDashboard size={14} />, show: true },
    { id: 'sessions', label: 'Sessions', icon: <CalendarSearch size={14} />, show: showSessions },
    { id: 'pipeline', label: 'Pipeline', icon: <GitFork size={14} />, show: showPipeline },
    { id: 'agents', label: 'Agents', icon: <Users size={14} />, show: showAgents },
    { id: 'settings', label: 'Settings', icon: <Settings2 size={14} />, show: showSettings },
  ];

  const visibleTabs = tabs.filter((t) => t.show);

  // If current tab becomes hidden, fall back to dashboard
  useEffect(() => {
    const visible = visibleTabs.find((t) => t.id === activeTab);
    if (!visible && visibleTabs.length > 0) {
      setActiveTab(visibleTabs[0]!.id);
    }
  }, [activeTab, visibleTabs]);

  // Load agents when agents tab is first activated
  useEffect(() => {
    if (activeTab === 'agents' && agents.length === 0) {
      loadAgents();
    }
  }, [activeTab, agents.length, loadAgents]);

  return (
    <div className="flex flex-col h-full bg-shell-bg text-shell-text select-none">
      {/* Header */}
      <header className="flex items-center gap-2 px-4 py-2.5 border-b border-white/5 shrink-0">
        <Database size={15} className="text-accent" aria-hidden="true" />
        <h1 className="text-sm font-semibold">Memory</h1>
      </header>

      {/* Tabs */}
      <Tabs
        value={activeTab}
        onValueChange={(v) => setActiveTab(v as Tab)}
        className="flex flex-col flex-1 min-h-0"
      >
        <div className="px-4 pt-2.5 pb-0 shrink-0 border-b border-white/5">
          <TabsList className="h-8 gap-0.5" aria-label="Memory app sections">
            {visibleTabs.map((tab) => (
              <TabsTrigger
                key={tab.id}
                value={tab.id}
                aria-label={tab.label}
                className="h-7 px-3 gap-1.5 text-xs"
              >
                <span aria-hidden="true">{tab.icon}</span>
                {tab.label}
              </TabsTrigger>
            ))}
          </TabsList>
        </div>

        {/* Tab panels */}
        <div className="flex-1 min-h-0 overflow-hidden">
          <TabsContent value="dashboard" className="h-full mt-0 overflow-hidden">
            <Dashboard />
          </TabsContent>

          <TabsContent value="sessions" className="h-full mt-0 overflow-hidden">
            <SessionBrowser />
          </TabsContent>

          <TabsContent value="pipeline" className="h-full mt-0 overflow-hidden">
            <PipelineControl />
          </TabsContent>

          <TabsContent value="agents" className="h-full mt-0 overflow-hidden">
            <AgentMemoryTable agents={agents} />
          </TabsContent>

          <TabsContent value="settings" className="h-full mt-0 overflow-hidden">
            <MemorySettings />
          </TabsContent>
        </div>
      </Tabs>
    </div>
  );
}
