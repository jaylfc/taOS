import { useState, useEffect, useRef, useCallback } from "react";
import {
  Plug,
  Play,
  Square,
  RotateCcw,
  Trash2,
  Plus,
  X,
  Copy,
  Check,
  ChevronDown,
  ShoppingBag,
  AlertCircle,
  Loader2,
} from "lucide-react";
import {
  Button,
  Card,
  Input,
  Label,
  Switch,
  Textarea,
} from "@/components/ui";
import { MobileSplitView } from "@/components/mobile/MobileSplitView";
import { useProcessStore } from "@/stores/process-store";
import { useNotificationStore } from "@/stores/notification-store";
import { getApp } from "@/registry/app-registry";

/* ------------------------------------------------------------------ */
/*  Types                                                              */
/* ------------------------------------------------------------------ */

type ServerStatus = "running" | "stopped" | "failed" | "installing";

interface MCPServer {
  id: string;
  name: string;
  version: string;
  description?: string;
  transport: "stdio" | "sse" | "ws";
  status: ServerStatus;
  pid?: number;
  last_started_at?: number;
  last_exit_code?: number;
  last_error?: string;
  installed_at: number;
}

interface Capability {
  name: string;
  description?: string;
  type: "tool" | "resource";
}

interface Attachment {
  id: number;
  scope_kind: "all" | "agent" | "group";
  scope_id?: string;
  allowed_tools: string[];
  allowed_resources: string[];
  created_at: number;
}

interface AgentInfo {
  name: string;
  display_name?: string;
  color: string;
}

interface GroupInfo {
  id: string;
  name: string;
}

type DetailTab = "overview" | "permissions" | "env" | "config" | "logs" | "used-by";

/* ------------------------------------------------------------------ */
/*  Helpers / pill components                                          */
/* ------------------------------------------------------------------ */

const STATUS_DOT: Record<ServerStatus, string> = {
  running: "bg-emerald-500",
  stopped: "bg-zinc-500",
  failed: "bg-red-500",
  installing: "bg-amber-500",
};

const STATUS_PILL: Record<ServerStatus, string> = {
  running: "bg-emerald-500/20 text-emerald-400",
  stopped: "bg-zinc-500/20 text-zinc-400",
  failed: "bg-red-500/20 text-red-400",
  installing: "bg-amber-500/20 text-amber-400",
};

const STATUS_LABEL: Record<ServerStatus, string> = {
  running: "Running",
  stopped: "Stopped",
  failed: "Failed",
  installing: "Installing",
};

const TRANSPORT_PILL: Record<string, string> = {
  stdio: "bg-blue-500/20 text-blue-300",
  sse: "bg-cyan-500/20 text-cyan-300",
  ws: "bg-teal-500/20 text-teal-300",
};

const STATUS_GROUP_ORDER: ServerStatus[] = ["running", "installing", "failed", "stopped"];

function groupByStatus(servers: MCPServer[]): Record<ServerStatus, MCPServer[]> {
  const out: Record<ServerStatus, MCPServer[]> = {
    running: [],
    stopped: [],
    failed: [],
    installing: [],
  };
  for (const s of servers) {
    out[s.status].push(s);
  }
  return out;
}

function fmtTime(ts: number): string {
  const d = new Date(ts * 1000);
  return d.toLocaleTimeString(undefined, { hour: "2-digit", minute: "2-digit" });
}

/* ------------------------------------------------------------------ */
/*  Uninstall confirm modal                                            */
/* ------------------------------------------------------------------ */

interface UninstallModalProps {
  server: MCPServer;
  attachments: Attachment[];
  onConfirm: () => void;
  onClose: () => void;
  loading: boolean;
}

function UninstallModal({ server, attachments, onConfirm, onClose, loading }: UninstallModalProps) {
  const [typed, setTyped] = useState("");
  const needsTyped = attachments.length >= 3;
  const canConfirm = !needsTyped || typed === server.id;
  const inputRef = useRef<HTMLInputElement>(null);

  useEffect(() => {
    inputRef.current?.focus();
  }, []);

  const scopeNames = attachments.map((a) => {
    if (a.scope_kind === "all") return "all agents";
    if (a.scope_kind === "agent") return `agent: ${a.scope_id}`;
    return `group: ${a.scope_id}`;
  });

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 backdrop-blur-sm"
      role="dialog"
      aria-modal="true"
      aria-label={`Uninstall ${server.name}`}
    >
      <div className="bg-[#1a1a2e] border border-white/10 rounded-2xl p-6 w-full max-w-md shadow-2xl">
        <div className="flex items-start gap-3 mb-4">
          <div className="p-2 rounded-lg bg-red-500/15 mt-0.5">
            <AlertCircle size={20} className="text-red-400" aria-hidden />
          </div>
          <div className="flex-1 min-w-0">
            <h2 className="text-base font-semibold text-shell-text">Uninstall {server.name}?</h2>
            <p className="text-xs text-shell-text-secondary mt-0.5">v{server.version}</p>
          </div>
          <button onClick={onClose} className="text-shell-text-secondary hover:text-shell-text transition-colors" aria-label="Close">
            <X size={16} />
          </button>
        </div>

        <div className="space-y-2 mb-4">
          {attachments.length > 0 && (
            <div className="text-sm text-shell-text-secondary bg-white/[0.03] rounded-lg px-3 py-2.5 border border-white/[0.06]">
              <span className="font-medium text-red-400">{attachments.length} attachment{attachments.length !== 1 ? "s" : ""}</span> will be revoked:{" "}
              <span className="text-shell-text">{scopeNames.join(", ")}</span>
            </div>
          )}
          <p className="text-xs text-shell-text-secondary">
            This will stop the server process, remove all attachments, delete env secrets, and remove files from disk. This cannot be undone.
          </p>
        </div>

        {needsTyped && (
          <div className="mb-4">
            <Label htmlFor="uninstall-confirm-input" className="text-xs mb-1.5 block text-shell-text-secondary">
              Type <span className="font-mono font-semibold text-shell-text">{server.id}</span> to confirm
            </Label>
            <Input
              ref={inputRef}
              id="uninstall-confirm-input"
              value={typed}
              onChange={(e) => setTyped(e.target.value)}
              placeholder={server.id}
              className="font-mono"
              aria-label={`Type ${server.id} to confirm uninstall`}
            />
          </div>
        )}

        <div className="flex gap-2 justify-end">
          <Button variant="outline" size="sm" onClick={onClose} disabled={loading}>
            Cancel
          </Button>
          <Button
            variant="destructive"
            size="sm"
            onClick={onConfirm}
            disabled={!canConfirm || loading}
            aria-label={`Confirm uninstall ${server.name}`}
          >
            {loading ? <Loader2 size={14} className="animate-spin mr-1" /> : <Trash2 size={14} className="mr-1" />}
            Uninstall
          </Button>
        </div>
      </div>
    </div>
  );
}

/* ------------------------------------------------------------------ */
/*  Attach permission modal                                            */
/* ------------------------------------------------------------------ */

interface AttachModalProps {
  serverId: string;
  agents: AgentInfo[];
  groups: GroupInfo[];
  capabilities: Capability[];
  onSaved: () => void;
  onClose: () => void;
}

function AttachModal({ serverId, agents, groups, capabilities, onSaved, onClose }: AttachModalProps) {
  type ScopeKind = "all" | "agent" | "group";
  const [scopeKind, setScopeKind] = useState<ScopeKind>("all");
  const [scopeSearch, setScopeSearch] = useState("");
  const [scopeId, setScopeId] = useState<string>("");
  const [unrestricted, setUnrestricted] = useState(true);
  const [selectedTools, setSelectedTools] = useState<Set<string>>(new Set());
  const [resourcePatterns, setResourcePatterns] = useState<string[]>([]);
  const [saving, setSaving] = useState(false);
  const [saveError, setSaveError] = useState<string | null>(null);

  const tools = capabilities.filter((c) => c.type === "tool");

  const filteredAgents = agents.filter((a) =>
    (a.display_name || a.name).toLowerCase().includes(scopeSearch.toLowerCase())
  );
  const filteredGroups = groups.filter((g) =>
    g.name.toLowerCase().includes(scopeSearch.toLowerCase())
  );

  function toggleTool(name: string) {
    setSelectedTools((prev) => {
      const next = new Set(prev);
      if (next.has(name)) next.delete(name);
      else next.add(name);
      return next;
    });
  }

  function addPattern() {
    setResourcePatterns((prev) => [...prev, ""]);
  }

  function updatePattern(idx: number, val: string) {
    setResourcePatterns((prev) => prev.map((p, i) => (i === idx ? val : p)));
  }

  function removePattern(idx: number) {
    setResourcePatterns((prev) => prev.filter((_, i) => i !== idx));
  }

  async function handleSave() {
    if (scopeKind !== "all" && !scopeId) {
      setSaveError("Select a specific agent or group.");
      return;
    }
    setSaving(true);
    setSaveError(null);
    try {
      const body = {
        scope_kind: scopeKind,
        scope_id: scopeKind === "all" ? undefined : scopeId,
        allowed_tools: unrestricted ? [] : Array.from(selectedTools),
        allowed_resources: resourcePatterns.filter((p) => p.trim()),
      };
      const res = await fetch(`/api/mcp/servers/${encodeURIComponent(serverId)}/permissions`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body),
      });
      if (!res.ok) {
        const err = await res.json().catch(() => ({ detail: "Failed to attach" }));
        setSaveError(err.detail ?? "Failed to attach");
        setSaving(false);
        return;
      }
      onSaved();
    } catch {
      setSaveError("Network error");
      setSaving(false);
    }
  }

  // Dynamic summary
  const scopeLabel =
    scopeKind === "all" ? "all agents" :
    scopeKind === "agent" ? (scopeId ? `${scopeId}` : "the selected agent") :
    scopeId ? `group ${scopeId}` : "the selected group";

  const toolSummary = unrestricted
    ? "all tools"
    : selectedTools.size === 0
      ? "no tools (unrestricted within this attachment)"
      : `${selectedTools.size} tool${selectedTools.size !== 1 ? "s" : ""}`;

  const notGranted = unrestricted ? [] : tools.filter((t) => !selectedTools.has(t.name));

  return (
    <div
      className="fixed inset-0 z-50 flex items-end sm:items-center justify-center bg-black/60 backdrop-blur-sm"
      role="dialog"
      aria-modal="true"
      aria-label="Attach permission"
    >
      <div className="bg-[#1a1a2e] border border-white/10 rounded-t-2xl sm:rounded-2xl p-5 w-full max-w-lg shadow-2xl max-h-[90vh] flex flex-col overflow-hidden">
        <div className="flex items-center justify-between mb-4 shrink-0">
          <h2 className="text-base font-semibold text-shell-text">Attach Permission</h2>
          <button onClick={onClose} className="text-shell-text-secondary hover:text-shell-text transition-colors" aria-label="Close">
            <X size={16} />
          </button>
        </div>

        <div className="overflow-y-auto flex-1 min-h-0 space-y-5 pr-1">
          {/* Scope picker */}
          <div>
            <Label className="text-xs mb-2 block text-shell-text-secondary">Scope</Label>
            <div className="flex gap-1 p-1 bg-white/[0.04] rounded-lg">
              {(["all", "agent", "group"] as ScopeKind[]).map((k) => (
                <button
                  key={k}
                  onClick={() => { setScopeKind(k); setScopeId(""); setScopeSearch(""); }}
                  className={`flex-1 py-1.5 rounded-md text-xs font-medium transition-colors ${scopeKind === k ? "bg-white/[0.1] text-shell-text shadow-sm" : "text-shell-text-secondary hover:text-shell-text"}`}
                  aria-pressed={scopeKind === k}
                >
                  {k === "all" ? "All agents" : k === "agent" ? "Specific agent" : "Specific group"}
                </button>
              ))}
            </div>
          </div>

          {/* Scope selector */}
          {(scopeKind === "agent" || scopeKind === "group") && (
            <div>
              <Label className="text-xs mb-2 block text-shell-text-secondary">
                {scopeKind === "agent" ? "Select agent" : "Select group"}
              </Label>
              <Input
                placeholder={`Search ${scopeKind}s...`}
                value={scopeSearch}
                onChange={(e) => setScopeSearch(e.target.value)}
                className="mb-2"
                aria-label={`Search ${scopeKind}s`}
              />
              <div className="max-h-32 overflow-y-auto space-y-1">
                {(scopeKind === "agent" ? filteredAgents : filteredGroups).map((item) => {
                  const id = "name" in item ? item.name : (item as GroupInfo).id;
                  const label = "display_name" in item && (item as AgentInfo).display_name ? (item as AgentInfo).display_name! : "name" in item ? item.name : (item as GroupInfo).name;
                  return (
                    <button
                      key={id}
                      onClick={() => setScopeId(id)}
                      className={`w-full text-left px-3 py-2 rounded-lg text-sm transition-colors ${scopeId === id ? "bg-accent/20 text-accent-foreground border border-accent/30" : "hover:bg-white/[0.06] text-shell-text-secondary"}`}
                      aria-pressed={scopeId === id}
                    >
                      {label}
                    </button>
                  );
                })}
                {(scopeKind === "agent" ? filteredAgents : filteredGroups).length === 0 && (
                  <p className="text-xs text-shell-text-secondary text-center py-2">No results</p>
                )}
              </div>
            </div>
          )}

          {/* Tools section */}
          <div>
            <div className="flex items-center justify-between mb-2">
              <Label className="text-xs text-shell-text-secondary">Tools</Label>
              <div className="flex items-center gap-2">
                <span className="text-xs text-shell-text-secondary">Unrestricted</span>
                <Switch
                  checked={unrestricted}
                  onCheckedChange={setUnrestricted}
                  aria-label="Allow all tools (unrestricted)"
                />
              </div>
            </div>
            {!unrestricted && (
              <>
                <div className="flex gap-2 mb-2">
                  <button
                    className="text-xs text-accent hover:underline"
                    onClick={() => setSelectedTools(new Set(tools.map((t) => t.name)))}
                    aria-label="Select all tools"
                  >
                    Select all
                  </button>
                  <span className="text-shell-text-secondary text-xs">/</span>
                  <button
                    className="text-xs text-accent hover:underline"
                    onClick={() => setSelectedTools(new Set())}
                    aria-label="Select no tools"
                  >
                    None
                  </button>
                </div>
                <div className="space-y-1 max-h-40 overflow-y-auto">
                  {tools.length === 0 && (
                    <p className="text-xs text-shell-text-secondary py-2 text-center">No tools discovered yet. Attach will be unrestricted within scope.</p>
                  )}
                  {tools.map((t) => (
                    <label key={t.name} className="flex items-start gap-2.5 p-2 rounded-lg hover:bg-white/[0.04] cursor-pointer">
                      <input
                        type="checkbox"
                        checked={selectedTools.has(t.name)}
                        onChange={() => toggleTool(t.name)}
                        className="mt-0.5 accent-blue-500"
                        aria-label={`Allow tool ${t.name}`}
                      />
                      <div className="min-w-0">
                        <span className="text-xs font-medium font-mono text-shell-text">{t.name}</span>
                        {t.description && (
                          <p className="text-[11px] text-shell-text-secondary truncate">{t.description}</p>
                        )}
                      </div>
                    </label>
                  ))}
                </div>
              </>
            )}
            {unrestricted && (
              <p className="text-xs text-shell-text-secondary">All tools are allowed within this scope.</p>
            )}
          </div>

          {/* Resources section */}
          <div>
            <div className="flex items-center justify-between mb-2">
              <Label className="text-xs text-shell-text-secondary">Resource patterns</Label>
              <button
                onClick={addPattern}
                className="text-xs text-accent hover:underline flex items-center gap-1"
                aria-label="Add resource pattern"
              >
                <Plus size={12} />
                Add pattern
              </button>
            </div>
            {resourcePatterns.length === 0 && (
              <p className="text-xs text-shell-text-secondary">No patterns — all resources unrestricted.</p>
            )}
            <div className="space-y-1.5">
              {resourcePatterns.map((p, i) => (
                <div key={i} className="flex gap-1.5">
                  <Input
                    value={p}
                    onChange={(e) => updatePattern(i, e.target.value)}
                    placeholder="/workspace/* or https://api.github.com/*"
                    className="font-mono text-xs"
                    aria-label={`Resource pattern ${i + 1}`}
                  />
                  <button
                    onClick={() => removePattern(i)}
                    className="text-shell-text-secondary hover:text-red-400 transition-colors shrink-0"
                    aria-label={`Remove pattern ${i + 1}`}
                  >
                    <X size={14} />
                  </button>
                </div>
              ))}
            </div>
          </div>

          {/* Summary */}
          <div className="bg-blue-500/[0.07] border border-blue-500/20 rounded-lg p-3">
            <p className="text-xs text-blue-200 leading-relaxed">
              <span className="font-semibold">{scopeLabel}</span> will be able to call:{" "}
              <span className="font-medium">{toolSummary}</span>.
              {notGranted.length > 0 && (
                <>
                  {" "}It will NOT be able to call:{" "}
                  <span className="font-medium">{notGranted.map((t) => t.name).join(", ")}</span>.
                </>
              )}
              {resourcePatterns.filter((p) => p.trim()).length > 0 && (
                <>
                  {" "}Resource access restricted to {resourcePatterns.filter((p) => p.trim()).length} pattern{resourcePatterns.filter((p) => p.trim()).length !== 1 ? "s" : ""}.
                </>
              )}
            </p>
          </div>
        </div>

        {saveError && (
          <p className="text-xs text-red-400 mt-2 shrink-0">{saveError}</p>
        )}

        <div className="flex gap-2 justify-end mt-4 shrink-0">
          <Button variant="outline" size="sm" onClick={onClose} disabled={saving}>
            Cancel
          </Button>
          <Button size="sm" onClick={handleSave} disabled={saving} aria-label="Save attachment">
            {saving ? <Loader2 size={14} className="animate-spin mr-1" /> : null}
            Attach
          </Button>
        </div>
      </div>
    </div>
  );
}

/* ------------------------------------------------------------------ */
/*  Server list row                                                    */
/* ------------------------------------------------------------------ */

function ServerRow({
  server,
  selected,
  onSelect,
}: {
  server: MCPServer;
  selected: boolean;
  onSelect: () => void;
}) {
  return (
    <button
      onClick={onSelect}
      className={`w-full text-left flex items-center gap-3 px-4 py-3 transition-colors hover:bg-white/[0.05] ${selected ? "bg-white/[0.07]" : ""}`}
      aria-pressed={selected}
      aria-label={`${server.name}, ${STATUS_LABEL[server.status]}`}
    >
      <div className="relative shrink-0">
        <div className="w-8 h-8 rounded-lg bg-white/[0.06] flex items-center justify-center">
          <Plug size={15} className="text-shell-text-secondary" aria-hidden />
        </div>
        <span
          className={`absolute -bottom-0.5 -right-0.5 w-2.5 h-2.5 rounded-full border-2 border-[#0f0f1e] ${STATUS_DOT[server.status]}`}
          aria-label={`Status: ${STATUS_LABEL[server.status]}`}
        />
      </div>
      <div className="flex-1 min-w-0">
        <div className="flex items-center gap-1.5 min-w-0">
          <span className="text-sm font-medium text-shell-text truncate">{server.name}</span>
          <span
            className={`shrink-0 text-[10px] px-1.5 py-0.5 rounded font-medium ${TRANSPORT_PILL[server.transport] ?? "bg-zinc-500/20 text-zinc-300"}`}
          >
            {server.transport}
          </span>
        </div>
        <div className="flex items-center gap-2 mt-0.5 text-[11px] text-shell-text-secondary">
          {server.last_started_at && (
            <span>Started {fmtTime(server.last_started_at)}</span>
          )}
          {server.pid && <span>PID {server.pid}</span>}
        </div>
      </div>
    </button>
  );
}

/* ------------------------------------------------------------------ */
/*  List pane                                                          */
/* ------------------------------------------------------------------ */

function ServerListPane({
  servers,
  loading,
  selectedId,
  onSelect,
  onOpenStore,
}: {
  servers: MCPServer[];
  loading: boolean;
  selectedId: string | null;
  onSelect: (id: string) => void;
  onOpenStore: () => void;
}) {
  const groups = groupByStatus(servers);

  if (loading) {
    return (
      <div className="flex items-center justify-center h-32">
        <Loader2 size={20} className="animate-spin text-shell-text-secondary" />
      </div>
    );
  }

  if (servers.length === 0) {
    return (
      <div className="flex flex-col items-center justify-center gap-4 h-40 px-6 text-center">
        <Plug size={32} className="text-shell-text-tertiary opacity-40" aria-hidden />
        <p className="text-sm text-shell-text-secondary">No MCP servers installed</p>
        <Button size="sm" variant="outline" onClick={onOpenStore} aria-label="Browse MCP servers in Store">
          <ShoppingBag size={14} className="mr-1.5" />
          Browse MCP servers in Store
        </Button>
      </div>
    );
  }

  return (
    <div>
      {STATUS_GROUP_ORDER.map((status) => {
        const group = groups[status];
        if (group.length === 0) return null;
        return (
          <div key={status}>
            <div className="px-4 py-1.5 text-[10px] font-semibold uppercase tracking-wider text-shell-text-tertiary border-b border-white/[0.04]">
              {STATUS_LABEL[status]}
            </div>
            {group.map((srv) => (
              <ServerRow
                key={srv.id}
                server={srv}
                selected={selectedId === srv.id}
                onSelect={() => onSelect(srv.id)}
              />
            ))}
          </div>
        );
      })}
    </div>
  );
}

/* ------------------------------------------------------------------ */
/*  Overview tab                                                       */
/* ------------------------------------------------------------------ */

function OverviewTab({
  server,
  capabilities,
  attachments,
  onAction,
  onUninstall,
}: {
  server: MCPServer;
  capabilities: Capability[];
  attachments: Attachment[];
  onAction: (action: "start" | "stop" | "restart") => void;
  onUninstall: () => void;
}) {
  const toolCount = capabilities.filter((c) => c.type === "tool").length;

  return (
    <div className="p-4 space-y-5 overflow-y-auto h-full">
      {/* Status + actions */}
      <div className="flex flex-wrap items-center gap-2">
        <span className={`text-xs px-2 py-1 rounded-full font-medium ${STATUS_PILL[server.status]}`}>
          {STATUS_LABEL[server.status]}
        </span>
        {server.pid && (
          <span className="text-xs text-shell-text-secondary">PID {server.pid}</span>
        )}
        <div className="flex-1" />
        {server.status !== "running" && (
          <Button size="sm" variant="outline" onClick={() => onAction("start")} aria-label="Start server">
            <Play size={13} className="mr-1" />
            Start
          </Button>
        )}
        {server.status === "running" && (
          <Button size="sm" variant="outline" onClick={() => onAction("stop")} aria-label="Stop server">
            <Square size={13} className="mr-1" />
            Stop
          </Button>
        )}
        <Button size="sm" variant="outline" onClick={() => onAction("restart")} aria-label="Restart server">
          <RotateCcw size={13} className="mr-1" />
          Restart
        </Button>
      </div>

      {/* Info */}
      <div className="space-y-2">
        {server.description && (
          <p className="text-sm text-shell-text-secondary">{server.description}</p>
        )}
        <div className="grid grid-cols-2 gap-2">
          <Card className="px-3 py-2.5">
            <div className="text-[10px] text-shell-text-tertiary uppercase tracking-wide">Version</div>
            <div className="text-sm font-mono font-medium">{server.version}</div>
          </Card>
          <Card className="px-3 py-2.5">
            <div className="text-[10px] text-shell-text-tertiary uppercase tracking-wide">Transport</div>
            <div className="text-sm font-medium">{server.transport}</div>
          </Card>
          <Card className="px-3 py-2.5">
            <div className="text-[10px] text-shell-text-tertiary uppercase tracking-wide">Tools</div>
            <div className="text-sm font-medium">{toolCount}</div>
          </Card>
          <Card className="px-3 py-2.5">
            <div className="text-[10px] text-shell-text-tertiary uppercase tracking-wide">Attachments</div>
            <div className="text-sm font-medium">{attachments.length}</div>
          </Card>
        </div>
        {server.last_error && (
          <div className="bg-red-500/10 border border-red-500/20 rounded-lg px-3 py-2">
            <p className="text-xs font-medium text-red-400 mb-0.5">Last error</p>
            <pre className="text-[11px] text-red-300 whitespace-pre-wrap font-mono">{server.last_error}</pre>
          </div>
        )}
      </div>

      {/* Uninstall */}
      <div className="pt-2 border-t border-white/[0.06]">
        <Button
          variant="destructive"
          size="sm"
          onClick={onUninstall}
          aria-label={`Uninstall ${server.name}`}
        >
          <Trash2 size={13} className="mr-1.5" />
          Uninstall
        </Button>
      </div>
    </div>
  );
}

/* ------------------------------------------------------------------ */
/*  Permissions tab                                                    */
/* ------------------------------------------------------------------ */

function PermissionsTab({
  serverId,
  attachments,
  onRefresh,
}: {
  serverId: string;
  attachments: Attachment[];
  onRefresh: () => void;
}) {
  const [showAttach, setShowAttach] = useState(false);
  const [agents, setAgents] = useState<AgentInfo[]>([]);
  const [groups, setGroups] = useState<GroupInfo[]>([]);
  const [capabilities, setCapabilities] = useState<Capability[]>([]);
  const [expandedId, setExpandedId] = useState<number | null>(null);

  useEffect(() => {
    fetch("/api/agents", { headers: { Accept: "application/json" } })
      .then((r) => r.json())
      .then((d) => setAgents(Array.isArray(d) ? d : d.agents ?? []))
      .catch(() => {});
    fetch("/api/relationships/groups", { headers: { Accept: "application/json" } })
      .then((r) => r.json())
      .then((d) => setGroups(Array.isArray(d) ? d : []))
      .catch(() => {});
    fetch(`/api/mcp/servers/${encodeURIComponent(serverId)}/capabilities`, { headers: { Accept: "application/json" } })
      .then((r) => r.json())
      .then((d) => setCapabilities(Array.isArray(d) ? d : d.capabilities ?? []))
      .catch(() => {});
  }, [serverId]);

  async function handleDelete(attachmentId: number) {
    await fetch(
      `/api/mcp/servers/${encodeURIComponent(serverId)}/permissions/${attachmentId}`,
      { method: "DELETE" }
    );
    onRefresh();
  }

  function scopeLabel(a: Attachment): string {
    if (a.scope_kind === "all") return "All agents";
    if (a.scope_kind === "agent") return `Agent: ${a.scope_id}`;
    return `Group: ${a.scope_id}`;
  }

  function toolSummary(a: Attachment): string {
    return a.allowed_tools.length === 0 ? "all tools" : `${a.allowed_tools.length} tool${a.allowed_tools.length !== 1 ? "s" : ""}`;
  }

  function resourceSummary(a: Attachment): string {
    return a.allowed_resources.length === 0 ? "no restriction" : `${a.allowed_resources.length} pattern${a.allowed_resources.length !== 1 ? "s" : ""}`;
  }

  return (
    <div className="p-4 flex flex-col gap-4 overflow-y-auto h-full">
      <div className="flex items-center justify-between">
        <p className="text-xs text-shell-text-secondary">
          {attachments.length === 0
            ? "No attachments. Server is unreachable to all agents."
            : `${attachments.length} attachment${attachments.length !== 1 ? "s" : ""}`}
        </p>
        <Button
          size="sm"
          variant="outline"
          onClick={() => setShowAttach(true)}
          aria-label="Add attachment"
        >
          <Plus size={13} className="mr-1" />
          Attach
        </Button>
      </div>

      {attachments.length === 0 && (
        <div className="flex flex-col items-center justify-center py-10 gap-2 text-center">
          <Plug size={28} className="text-shell-text-tertiary opacity-40" aria-hidden />
          <p className="text-sm text-shell-text-secondary">Zero-access by default</p>
          <p className="text-xs text-shell-text-secondary max-w-xs">
            Attach this server to an agent or group to grant access. Tool and resource restrictions are optional.
          </p>
        </div>
      )}

      <div className="space-y-2">
        {attachments.map((a) => (
          <Card key={a.id} className="overflow-hidden">
            <div className="flex items-center gap-3 px-3 py-2.5">
              <div className="flex-1 min-w-0 space-y-1">
                <div className="flex items-center gap-1.5 flex-wrap">
                  <span className="text-xs font-medium text-shell-text">{scopeLabel(a)}</span>
                  <span className="text-[10px] px-1.5 py-0.5 rounded bg-white/[0.06] text-shell-text-secondary">
                    {toolSummary(a)}
                  </span>
                  <span className="text-[10px] px-1.5 py-0.5 rounded bg-white/[0.06] text-shell-text-secondary">
                    {resourceSummary(a)}
                  </span>
                </div>
              </div>
              {(a.allowed_tools.length > 0 || a.allowed_resources.length > 0) && (
                <button
                  onClick={() => setExpandedId(expandedId === a.id ? null : a.id)}
                  className="text-shell-text-secondary hover:text-shell-text transition-colors"
                  aria-label={expandedId === a.id ? "Collapse details" : "Expand details"}
                  aria-expanded={expandedId === a.id}
                >
                  <ChevronDown
                    size={14}
                    className={`transition-transform ${expandedId === a.id ? "rotate-180" : ""}`}
                  />
                </button>
              )}
              <button
                onClick={() => handleDelete(a.id)}
                className="text-shell-text-secondary hover:text-red-400 transition-colors"
                aria-label={`Remove attachment for ${scopeLabel(a)}`}
              >
                <X size={14} />
              </button>
            </div>
            {expandedId === a.id && (
              <div className="px-3 pb-2.5 space-y-2 border-t border-white/[0.06] pt-2">
                {a.allowed_tools.length > 0 && (
                  <div>
                    <p className="text-[10px] text-shell-text-tertiary uppercase tracking-wide mb-1">Allowed tools</p>
                    <div className="flex flex-wrap gap-1">
                      {a.allowed_tools.map((t) => (
                        <span key={t} className="text-[10px] px-1.5 py-0.5 rounded bg-blue-500/15 text-blue-300 font-mono">{t}</span>
                      ))}
                    </div>
                  </div>
                )}
                {a.allowed_resources.length > 0 && (
                  <div>
                    <p className="text-[10px] text-shell-text-tertiary uppercase tracking-wide mb-1">Resource patterns</p>
                    <div className="flex flex-wrap gap-1">
                      {a.allowed_resources.map((r, i) => (
                        <span key={i} className="text-[10px] px-1.5 py-0.5 rounded bg-cyan-500/15 text-cyan-300 font-mono">{r}</span>
                      ))}
                    </div>
                  </div>
                )}
              </div>
            )}
          </Card>
        ))}
      </div>

      {showAttach && (
        <AttachModal
          serverId={serverId}
          agents={agents}
          groups={groups}
          capabilities={capabilities}
          onSaved={() => { setShowAttach(false); onRefresh(); }}
          onClose={() => setShowAttach(false)}
        />
      )}
    </div>
  );
}

/* ------------------------------------------------------------------ */
/*  Env tab                                                            */
/* ------------------------------------------------------------------ */

interface EnvEntry {
  key: string;
  value: string;
  revealed: boolean;
}

function EnvTab({ serverId }: { serverId: string }) {
  const [entries, setEntries] = useState<EnvEntry[]>([]);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [saveError, setSaveError] = useState<string | null>(null);
  const [saved, setSaved] = useState(false);

  useEffect(() => {
    setLoading(true);
    fetch(`/api/mcp/servers/${encodeURIComponent(serverId)}/env`, { headers: { Accept: "application/json" } })
      .then((r) => r.json())
      .then((d) => {
        const obj: Record<string, string> = d ?? {};
        setEntries(Object.entries(obj).map(([k, v]) => ({ key: k, value: v, revealed: false })));
      })
      .catch(() => setEntries([]))
      .finally(() => setLoading(false));
  }, [serverId]);

  function addEntry() {
    setEntries((prev) => [...prev, { key: "", value: "", revealed: true }]);
  }

  function updateKey(i: number, k: string) {
    setEntries((prev) => prev.map((e, idx) => idx === i ? { ...e, key: k } : e));
  }

  function updateValue(i: number, v: string) {
    setEntries((prev) => prev.map((e, idx) => idx === i ? { ...e, value: v } : e));
  }

  function removeEntry(i: number) {
    setEntries((prev) => prev.filter((_, idx) => idx !== i));
  }

  function toggleReveal(i: number) {
    setEntries((prev) => prev.map((e, idx) => idx === i ? { ...e, revealed: !e.revealed } : e));
  }

  async function handleSave() {
    setSaving(true);
    setSaveError(null);
    setSaved(false);
    const obj: Record<string, string> = {};
    for (const e of entries) {
      if (e.key.trim()) obj[e.key.trim()] = e.value;
    }
    try {
      const res = await fetch(`/api/mcp/servers/${encodeURIComponent(serverId)}/env`, {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(obj),
      });
      if (!res.ok) {
        const err = await res.json().catch(() => ({ detail: "Save failed" }));
        setSaveError(err.detail ?? "Save failed");
      } else {
        setSaved(true);
        setTimeout(() => setSaved(false), 2000);
      }
    } catch {
      setSaveError("Network error");
    }
    setSaving(false);
  }

  if (loading) {
    return <div className="flex items-center justify-center h-24"><Loader2 size={18} className="animate-spin text-shell-text-secondary" /></div>;
  }

  return (
    <div className="p-4 flex flex-col gap-4 overflow-y-auto h-full">
      <div className="flex items-center justify-between">
        <p className="text-xs text-shell-text-secondary">Environment variables are stored as secrets.</p>
        <button onClick={addEntry} className="text-xs text-accent hover:underline flex items-center gap-1" aria-label="Add environment variable">
          <Plus size={12} />
          Add
        </button>
      </div>
      <div className="space-y-2">
        {entries.map((e, i) => (
          <div key={i} className="flex gap-2 items-center">
            <Input
              value={e.key}
              onChange={(ev) => updateKey(i, ev.target.value)}
              placeholder="KEY"
              className="font-mono text-xs w-36 shrink-0"
              aria-label={`Environment variable name ${i + 1}`}
            />
            <div className="flex-1 relative">
              <Input
                type={e.revealed ? "text" : "password"}
                value={e.value}
                onChange={(ev) => updateValue(i, ev.target.value)}
                placeholder="value"
                className="font-mono text-xs pr-8"
                aria-label={`Environment variable value ${i + 1}`}
              />
              <button
                onClick={() => toggleReveal(i)}
                className="absolute right-2 top-1/2 -translate-y-1/2 text-shell-text-tertiary hover:text-shell-text transition-colors"
                aria-label={e.revealed ? "Hide value" : "Reveal value"}
              >
                {e.revealed ? <span className="text-[10px]">hide</span> : <span className="text-[10px]">show</span>}
              </button>
            </div>
            <button onClick={() => removeEntry(i)} className="text-shell-text-secondary hover:text-red-400 transition-colors shrink-0" aria-label={`Remove variable ${e.key || i + 1}`}>
              <X size={14} />
            </button>
          </div>
        ))}
      </div>
      {saveError && <p className="text-xs text-red-400">{saveError}</p>}
      <Button
        size="sm"
        onClick={handleSave}
        disabled={saving}
        className="self-start"
        aria-label="Save environment variables"
      >
        {saved ? <Check size={13} className="mr-1 text-emerald-400" /> : saving ? <Loader2 size={13} className="animate-spin mr-1" /> : null}
        {saved ? "Saved" : "Save"}
      </Button>
    </div>
  );
}

/* ------------------------------------------------------------------ */
/*  Config tab                                                         */
/* ------------------------------------------------------------------ */

function ConfigTab({ serverId }: { serverId: string }) {
  const [text, setText] = useState("");
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [saveError, setSaveError] = useState<string | null>(null);
  const [saved, setSaved] = useState(false);

  useEffect(() => {
    setLoading(true);
    fetch(`/api/mcp/servers/${encodeURIComponent(serverId)}/config`, { headers: { Accept: "application/json" } })
      .then((r) => r.json())
      .then((d) => setText(JSON.stringify(d, null, 2)))
      .catch(() => setText("{}"))
      .finally(() => setLoading(false));
  }, [serverId]);

  let jsonValid = true;
  try { JSON.parse(text); } catch { jsonValid = false; }

  async function handleSave() {
    if (!jsonValid) return;
    setSaving(true);
    setSaveError(null);
    setSaved(false);
    try {
      const parsed = JSON.parse(text);
      const res = await fetch(`/api/mcp/servers/${encodeURIComponent(serverId)}/config`, {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(parsed),
      });
      if (!res.ok) {
        const err = await res.json().catch(() => ({ detail: "Save failed" }));
        setSaveError(err.detail ?? "Save failed");
      } else {
        setSaved(true);
        setTimeout(() => setSaved(false), 2000);
      }
    } catch {
      setSaveError("Network error");
    }
    setSaving(false);
  }

  if (loading) {
    return <div className="flex items-center justify-center h-24"><Loader2 size={18} className="animate-spin text-shell-text-secondary" /></div>;
  }

  return (
    <div className="p-4 flex flex-col gap-3 h-full overflow-hidden">
      <p className="text-xs text-shell-text-secondary shrink-0">JSON configuration overrides for this server.</p>
      <Textarea
        value={text}
        onChange={(e) => setText(e.target.value)}
        className={`flex-1 font-mono text-xs resize-none ${!jsonValid ? "border-red-500/50" : ""}`}
        aria-label="Server configuration JSON"
        aria-invalid={!jsonValid}
        spellCheck={false}
      />
      {!jsonValid && <p className="text-xs text-red-400 shrink-0">Invalid JSON</p>}
      {saveError && <p className="text-xs text-red-400 shrink-0">{saveError}</p>}
      <Button
        size="sm"
        onClick={handleSave}
        disabled={!jsonValid || saving}
        className="self-start shrink-0"
        aria-label="Save configuration"
      >
        {saved ? <Check size={13} className="mr-1 text-emerald-400" /> : saving ? <Loader2 size={13} className="animate-spin mr-1" /> : null}
        {saved ? "Saved" : "Save"}
      </Button>
    </div>
  );
}

/* ------------------------------------------------------------------ */
/*  Logs tab (SSE live tail)                                           */
/* ------------------------------------------------------------------ */

function LogsTab({ serverId }: { serverId: string }) {
  const [lines, setLines] = useState<string[]>([]);
  const [connected, setConnected] = useState(false);
  const [paused, setPaused] = useState(false);
  const [copied, setCopied] = useState(false);
  const scrollRef = useRef<HTMLDivElement>(null);
  const pausedRef = useRef(false);
  const esRef = useRef<EventSource | null>(null);

  pausedRef.current = paused;

  useEffect(() => {
    const es = new EventSource(`/api/mcp/servers/${encodeURIComponent(serverId)}/logs/stream`);
    esRef.current = es;
    es.onopen = () => setConnected(true);
    es.onerror = () => setConnected(false);
    es.onmessage = (e) => {
      if (!pausedRef.current) {
        setLines((prev) => [...prev.slice(-500), e.data]);
      }
    };
    return () => { es.close(); esRef.current = null; };
  }, [serverId]);

  useEffect(() => {
    if (!paused && scrollRef.current) {
      scrollRef.current.scrollTop = scrollRef.current.scrollHeight;
    }
  }, [lines, paused]);

  function handleScroll() {
    const el = scrollRef.current;
    if (!el) return;
    const atBottom = el.scrollHeight - el.scrollTop - el.clientHeight < 40;
    if (!atBottom && !pausedRef.current) setPaused(true);
    if (atBottom && pausedRef.current) setPaused(false);
  }

  async function handleCopy() {
    await navigator.clipboard.writeText(lines.join("\n"));
    setCopied(true);
    setTimeout(() => setCopied(false), 1500);
  }

  return (
    <div className="flex flex-col h-full overflow-hidden">
      <div className="flex items-center gap-3 px-4 py-2 border-b border-white/[0.06] shrink-0">
        <span className={`w-2 h-2 rounded-full ${connected ? "bg-emerald-500" : "bg-zinc-500"}`} aria-label={connected ? "Connected" : "Disconnected"} />
        <span className="text-xs text-shell-text-secondary">{connected ? "Live" : "Disconnected"}</span>
        {paused && <span className="text-xs text-amber-400">Paused — scroll to bottom to resume</span>}
        <div className="flex-1" />
        <Button size="sm" variant="ghost" onClick={handleCopy} aria-label="Copy all logs">
          {copied ? <Check size={13} /> : <Copy size={13} />}
        </Button>
      </div>
      <div
        ref={scrollRef}
        onScroll={handleScroll}
        className="flex-1 overflow-y-auto p-4 font-mono text-[11px] leading-relaxed text-shell-text-secondary whitespace-pre-wrap"
        role="log"
        aria-label="Server logs"
        aria-live="polite"
      >
        {lines.length === 0 && <span className="text-shell-text-tertiary">Waiting for log lines...</span>}
        {lines.map((line, i) => {
          const isError = /error|exception|traceback/i.test(line);
          return (
            <div key={i} className={isError ? "text-red-400" : ""}>
              {line}
            </div>
          );
        })}
      </div>
    </div>
  );
}

/* ------------------------------------------------------------------ */
/*  Used-by tab (3s poll)                                             */
/* ------------------------------------------------------------------ */

interface UsedByEntry {
  agent_name: string;
  tool?: string;
  started_at?: number;
}

function UsedByTab({ serverId }: { serverId: string }) {
  const [entries, setEntries] = useState<UsedByEntry[]>([]);

  useEffect(() => {
    function poll() {
      fetch(`/api/mcp/servers/${encodeURIComponent(serverId)}/used-by`, { headers: { Accept: "application/json" } })
        .then((r) => r.json())
        .then((d) => setEntries(Array.isArray(d) ? d : []))
        .catch(() => {});
    }
    poll();
    const t = setInterval(poll, 3000);
    return () => clearInterval(t);
  }, [serverId]);

  return (
    <div className="p-4 overflow-y-auto h-full">
      {entries.length === 0 ? (
        <div className="flex flex-col items-center justify-center gap-2 py-12 text-center">
          <p className="text-sm text-shell-text-secondary">No agents currently calling this server</p>
          <p className="text-xs text-shell-text-tertiary">Updates every 3 seconds</p>
        </div>
      ) : (
        <div className="space-y-2">
          {entries.map((e, i) => (
            <Card key={i} className="px-3 py-2.5 flex items-center gap-3">
              <span className="w-2 h-2 rounded-full bg-emerald-500 shrink-0" aria-label="Active" />
              <div className="flex-1 min-w-0">
                <p className="text-sm font-medium text-shell-text">{e.agent_name}</p>
                {e.tool && <p className="text-xs text-shell-text-secondary font-mono">{e.tool}</p>}
              </div>
              {e.started_at && (
                <span className="text-xs text-shell-text-secondary">{fmtTime(e.started_at)}</span>
              )}
            </Card>
          ))}
        </div>
      )}
    </div>
  );
}

/* ------------------------------------------------------------------ */
/*  Detail pane                                                        */
/* ------------------------------------------------------------------ */

function ServerDetail({
  server,
  onRefreshList,
  onDeselect,
}: {
  server: MCPServer;
  onRefreshList: () => void;
  onDeselect: () => void;
}) {
  const [tab, setTab] = useState<DetailTab>("overview");
  const [capabilities, setCapabilities] = useState<Capability[]>([]);
  const [attachments, setAttachments] = useState<Attachment[]>([]);
  const [actionLoading, setActionLoading] = useState<string | null>(null);
  const [showUninstall, setShowUninstall] = useState(false);
  const [uninstalling, setUninstalling] = useState(false);
  const addNotification = useNotificationStore((s) => s.addNotification);

  function fetchAttachments() {
    fetch(`/api/mcp/servers/${encodeURIComponent(server.id)}/permissions`, {
      headers: { Accept: "application/json" },
    })
      .then((r) => r.json())
      .then((d) => setAttachments(Array.isArray(d) ? d : []))
      .catch(() => {});
  }

  useEffect(() => {
    fetchAttachments();
    fetch(`/api/mcp/servers/${encodeURIComponent(server.id)}/capabilities`, {
      headers: { Accept: "application/json" },
    })
      .then((r) => r.json())
      .then((d) => setCapabilities(Array.isArray(d) ? d : d.capabilities ?? []))
      .catch(() => {});
  }, [server.id]);

  async function handleAction(action: "start" | "stop" | "restart") {
    setActionLoading(action);
    try {
      await fetch(`/api/mcp/servers/${encodeURIComponent(server.id)}/${action}`, { method: "POST" });
      onRefreshList();
    } catch {
      addNotification({ source: "mcp", title: "Action failed", body: `Failed to ${action} ${server.name}`, level: "error" });
    }
    setActionLoading(null);
  }

  async function handleUninstall() {
    setUninstalling(true);
    try {
      const res = await fetch(`/api/mcp/servers/${encodeURIComponent(server.id)}`, { method: "DELETE" });
      const report = await res.json().catch(() => ({}));
      const agents_affected: number = report.agents_affected ?? attachments.length;
      const secrets_dropped: number = report.secrets_dropped ?? 0;
      addNotification({
        source: "mcp",
        title: `Removed ${server.name}`,
        body: `${agents_affected} agent${agents_affected !== 1 ? "s" : ""} lost access, ${secrets_dropped} secret${secrets_dropped !== 1 ? "s" : ""} dropped.`,
        level: "info",
      });
      setShowUninstall(false);
      onDeselect();
      onRefreshList();
    } catch {
      addNotification({ source: "mcp", title: "Uninstall failed", body: `Could not uninstall ${server.name}`, level: "error" });
    }
    setUninstalling(false);
  }

  const TABS: { id: DetailTab; label: string }[] = [
    { id: "overview", label: "Overview" },
    { id: "permissions", label: "Permissions" },
    { id: "env", label: "Env" },
    { id: "config", label: "Config" },
    { id: "logs", label: "Logs" },
    { id: "used-by", label: "Used by" },
  ];

  return (
    <div className="flex flex-col h-full overflow-hidden">
      {/* Header */}
      <div className="shrink-0 px-4 py-3 border-b border-white/[0.06] flex items-center gap-3">
        <div className="w-8 h-8 rounded-lg bg-white/[0.06] flex items-center justify-center shrink-0">
          <Plug size={15} className="text-shell-text-secondary" aria-hidden />
        </div>
        <div className="flex-1 min-w-0">
          <h2 className="text-sm font-semibold text-shell-text truncate">{server.name}</h2>
          <p className="text-[11px] text-shell-text-secondary">v{server.version}</p>
        </div>
        <span className={`text-[10px] px-2 py-0.5 rounded-full font-medium ${STATUS_PILL[server.status]}`}>
          {STATUS_LABEL[server.status]}
        </span>
        {actionLoading && <Loader2 size={14} className="animate-spin text-shell-text-secondary shrink-0" aria-label="Loading" />}
      </div>

      {/* Tab bar — horizontal scroll on mobile */}
      <div className="shrink-0 border-b border-white/[0.06] overflow-x-auto">
        <div className="flex min-w-max px-2" role="tablist" aria-label="Server detail tabs">
          {TABS.map((t) => (
            <button
              key={t.id}
              role="tab"
              aria-selected={tab === t.id}
              onClick={() => setTab(t.id)}
              className={`px-3 py-2.5 text-xs font-medium whitespace-nowrap transition-colors border-b-2 ${
                tab === t.id
                  ? "border-accent text-shell-text"
                  : "border-transparent text-shell-text-secondary hover:text-shell-text"
              }`}
            >
              {t.label}
            </button>
          ))}
        </div>
      </div>

      {/* Tab content */}
      <div className="flex-1 min-h-0 overflow-hidden">
        {tab === "overview" && (
          <OverviewTab
            server={server}
            capabilities={capabilities}
            attachments={attachments}
            onAction={handleAction}
            onUninstall={() => setShowUninstall(true)}
          />
        )}
        {tab === "permissions" && (
          <PermissionsTab
            serverId={server.id}
            attachments={attachments}
            onRefresh={fetchAttachments}
          />
        )}
        {tab === "env" && <EnvTab serverId={server.id} />}
        {tab === "config" && <ConfigTab serverId={server.id} />}
        {tab === "logs" && <LogsTab serverId={server.id} />}
        {tab === "used-by" && <UsedByTab serverId={server.id} />}
      </div>

      {showUninstall && (
        <UninstallModal
          server={server}
          attachments={attachments}
          loading={uninstalling}
          onConfirm={handleUninstall}
          onClose={() => setShowUninstall(false)}
        />
      )}
    </div>
  );
}

/* ------------------------------------------------------------------ */
/*  MCPApp root                                                        */
/* ------------------------------------------------------------------ */

export function MCPApp({ windowId: _windowId }: { windowId: string }) {
  const [servers, setServers] = useState<MCPServer[]>([]);
  const [loading, setLoading] = useState(true);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const openWindow = useProcessStore((s) => s.openWindow);

  const fetchServers = useCallback(async () => {
    try {
      const res = await fetch("/api/mcp/servers", { headers: { Accept: "application/json" } });
      if (res.ok) {
        const data = await res.json();
        setServers(Array.isArray(data) ? data : data.servers ?? []);
      }
    } catch {
      // silently ignore; show last known state
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    fetchServers();
    const t = setInterval(fetchServers, 10_000);
    return () => clearInterval(t);
  }, [fetchServers]);

  const selectedServer = servers.find((s) => s.id === selectedId) ?? null;

  function handleOpenStore() {
    const storeApp = getApp("store");
    if (storeApp) openWindow("store", storeApp.defaultSize);
  }

  const listPane = (
    <ServerListPane
      servers={servers}
      loading={loading}
      selectedId={selectedId}
      onSelect={setSelectedId}
      onOpenStore={handleOpenStore}
    />
  );

  const detailPane = selectedServer ? (
    <ServerDetail
      server={selectedServer}
      onRefreshList={fetchServers}
      onDeselect={() => setSelectedId(null)}
    />
  ) : null;

  return (
    <MobileSplitView
      list={listPane}
      detail={detailPane}
      selectedId={selectedId}
      onBack={() => setSelectedId(null)}
      listTitle="MCP"
      detailTitle={selectedServer?.name ?? ""}
    />
  );
}
