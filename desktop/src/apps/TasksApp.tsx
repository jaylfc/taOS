import { useState, useEffect, useCallback } from "react";
import { CalendarClock, Plus, Edit, Trash2, Play, Pause, X } from "lucide-react";
import {
  Button,
  Card,
  CardContent,
  CardHeader,
  CardTitle,
  Input,
  Label,
} from "@/components/ui";

/* ------------------------------------------------------------------ */
/*  Types                                                              */
/* ------------------------------------------------------------------ */

interface Task {
  id: number;
  name: string;
  agent_name: string | null;
  schedule: string;
  command: string;
  description: string;
  enabled: boolean;
  last_run: string | number | null;
}

interface Preset {
  id?: number;
  name: string;
  schedule?: string;
  command?: string;
  description: string;
  tasks?: { name: string; schedule: string; command: string; description?: string }[];
}

/* ------------------------------------------------------------------ */
/*  Constants                                                          */
/* ------------------------------------------------------------------ */

const FALLBACK_PRESETS: Preset[] = [
  { name: "Daily Summary", schedule: "0 9 * * *", command: "summarize --last 24h", description: "Generate a daily summary every morning at 9 AM" },
  { name: "Hourly Sync", schedule: "0 * * * *", command: "sync --sources all", description: "Sync all data sources every hour" },
  { name: "Weekly Report", schedule: "0 10 * * 1", command: "report --type weekly", description: "Generate a weekly report every Monday at 10 AM" },
  { name: "Nightly Cleanup", schedule: "0 2 * * *", command: "cleanup --older-than 30d", description: "Clean up old data every night at 2 AM" },
  { name: "Health Check", schedule: "*/15 * * * *", command: "health-check", description: "Run health checks every 15 minutes" },
  { name: "Backup", schedule: "0 3 * * 0", command: "backup --full", description: "Full backup every Sunday at 3 AM" },
];

/* ------------------------------------------------------------------ */
/*  TasksApp                                                           */
/* ------------------------------------------------------------------ */

export function TasksApp({ windowId: _windowId }: { windowId: string }) {
  const [tasks, setTasks] = useState<Task[]>([]);
  const [presets, setPresets] = useState<Preset[]>(FALLBACK_PRESETS);
  const [agents, setAgents] = useState<string[]>([]);
  const [loading, setLoading] = useState(true);
  const [showDialog, setShowDialog] = useState(false);
  const [editingId, setEditingId] = useState<number | null>(null);
  const [formError, setFormError] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);

  // Form state
  const [formName, setFormName] = useState("");
  const [formAgent, setFormAgent] = useState("");
  const [formSchedule, setFormSchedule] = useState("");
  const [formCommand, setFormCommand] = useState("");
  const [formDesc, setFormDesc] = useState("");

  const fetchTasks = useCallback(async () => {
    try {
      const res = await fetch("/api/tasks", {
        headers: { Accept: "application/json" },
      });
      if (res.ok) {
        const ct = res.headers.get("content-type") ?? "";
        if (ct.includes("application/json")) {
          const data = await res.json();
          if (Array.isArray(data)) {
            setTasks(
              data.map((t: Record<string, unknown>) => ({
                id: Number(t.id),
                name: String(t.name ?? ""),
                agent_name: (t.agent_name as string | null) ?? null,
                schedule: String(t.schedule ?? ""),
                command: String(t.command ?? ""),
                description: String(t.description ?? ""),
                enabled: Boolean(t.enabled),
                last_run: (t.last_run as string | number | null) ?? null,
              }))
            );
            setLoading(false);
            return;
          }
        }
      }
    } catch { /* fall through */ }
    setTasks([]);
    setLoading(false);
  }, []);

  const fetchPresets = useCallback(async () => {
    try {
      const res = await fetch("/api/tasks/presets", {
        headers: { Accept: "application/json" },
      });
      if (res.ok) {
        const ct = res.headers.get("content-type") ?? "";
        if (ct.includes("application/json")) {
          const data = await res.json();
          if (Array.isArray(data) && data.length > 0) {
            setPresets(data);
          }
        }
      }
    } catch { /* keep fallback */ }
  }, []);

  useEffect(() => {
    fetchTasks();
    fetchPresets();
  }, [fetchTasks, fetchPresets]);

  useEffect(() => {
    (async () => {
      try {
        const res = await fetch("/api/agents", {
          headers: { Accept: "application/json" },
        });
        if (res.ok) {
          const ct = res.headers.get("content-type") ?? "";
          if (ct.includes("application/json")) {
            const data = await res.json();
            if (Array.isArray(data) && data.length > 0) {
              setAgents(data.map((a: Record<string, unknown>) => String(a.name ?? "unknown")));
            }
          }
        }
      } catch { /* use fallback */ }
    })();
  }, []);

  function resetForm() {
    setFormName("");
    setFormAgent("");
    setFormSchedule("");
    setFormCommand("");
    setFormDesc("");
    setEditingId(null);
    setFormError(null);
    setSubmitting(false);
    setShowDialog(false);
  }

  function openEdit(task: Task) {
    setFormName(task.name);
    setFormAgent(task.agent_name ?? "");
    setFormSchedule(task.schedule);
    setFormCommand(task.command);
    setFormDesc(task.description);
    setEditingId(task.id);
    setFormError(null);
    setShowDialog(true);
  }

  async function applyPreset(preset: Preset) {
    // Server-side preset apply (needs agent + preset id)
    if (preset.id != null) {
      const agentName = window.prompt(
        `Apply "${preset.name}" preset to which agent?`,
        agents[0] ?? ""
      );
      if (!agentName) return;
      try {
        const res = await fetch(`/api/tasks/presets/${preset.id}/apply`, {
          method: "POST",
          headers: { "Content-Type": "application/json", Accept: "application/json" },
          body: JSON.stringify({ agent_name: agentName }),
        });
        if (!res.ok) {
          let msg = `Apply failed (${res.status})`;
          try {
            const err = await res.json();
            if (err?.error) msg = String(err.error);
          } catch { /* ignore */ }
          window.alert(msg);
          return;
        }
        fetchTasks();
      } catch (e) {
        window.alert(e instanceof Error ? e.message : "Network error");
      }
      return;
    }
    // Fallback: prefill the dialog
    setFormName(preset.name);
    setFormSchedule(preset.schedule ?? "");
    setFormCommand(preset.command ?? "");
    setFormDesc(preset.description);
    setEditingId(null);
    setFormError(null);
    setShowDialog(true);
  }

  async function handleSave() {
    if (!formName.trim() || !formSchedule.trim() || !formCommand.trim()) return;
    setSubmitting(true);
    setFormError(null);
    const body = {
      name: formName.trim(),
      schedule: formSchedule.trim(),
      command: formCommand.trim(),
      description: formDesc.trim(),
      agent_name: formAgent || null,
    };
    try {
      const res = editingId != null
        ? await fetch(`/api/tasks/${editingId}`, {
            method: "PUT",
            headers: { "Content-Type": "application/json", Accept: "application/json" },
            body: JSON.stringify(body),
          })
        : await fetch("/api/tasks", {
            method: "POST",
            headers: { "Content-Type": "application/json", Accept: "application/json" },
            body: JSON.stringify(body),
          });
      if (!res.ok) {
        let msg = `Save failed (${res.status})`;
        try {
          const err = await res.json();
          if (err?.error) msg = String(err.error);
        } catch { /* ignore */ }
        setFormError(msg);
        setSubmitting(false);
        return;
      }
      resetForm();
      fetchTasks();
    } catch (e) {
      setFormError(e instanceof Error ? e.message : "Network error");
      setSubmitting(false);
    }
  }

  async function toggleEnabled(id: number) {
    try {
      const res = await fetch(`/api/tasks/${id}/toggle`, {
        method: "POST",
        headers: { Accept: "application/json" },
      });
      if (!res.ok) {
        let msg = `Toggle failed (${res.status})`;
        try {
          const err = await res.json();
          if (err?.error) msg = String(err.error);
        } catch { /* ignore */ }
        window.alert(msg);
        return;
      }
      fetchTasks();
    } catch (e) {
      window.alert(e instanceof Error ? e.message : "Network error");
    }
  }

  async function handleDelete(id: number) {
    if (!window.confirm("Delete this routine?")) return;
    try {
      const res = await fetch(`/api/tasks/${id}`, {
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
      fetchTasks();
    } catch (e) {
      window.alert(e instanceof Error ? e.message : "Network error");
    }
  }

  return (
    <div className="flex flex-col h-full bg-shell-bg text-shell-text select-none relative">
      {/* Toolbar */}
      <div className="flex items-center justify-between px-4 py-3 border-b border-white/5">
        <div className="flex items-center gap-2">
          <CalendarClock size={18} className="text-accent" />
          <h1 className="text-sm font-semibold">Routines</h1>
          <span className="text-xs text-shell-text-tertiary">
            {tasks.length} scheduled
          </span>
        </div>
        <Button
          size="sm"
          onClick={() => { resetForm(); setShowDialog(true); }}
          aria-label="Add routine"
        >
          <Plus size={14} />
          Add Routine
        </Button>
      </div>

      {/* Content */}
      <div className="flex-1 overflow-auto">
        {loading ? (
          <div className="flex items-center justify-center h-full text-shell-text-tertiary text-sm">
            Loading routines...
          </div>
        ) : tasks.length === 0 && !showDialog ? (
          <div className="p-4 space-y-6">
            {/* Empty state */}
            <div className="flex flex-col items-center justify-center py-8 gap-3 text-shell-text-tertiary">
              <CalendarClock size={40} className="opacity-30" />
              <p className="text-sm">No scheduled routines</p>
            </div>

            {/* Presets */}
            <div>
              <h2 className="text-sm font-medium text-shell-text-secondary mb-3">Quick Start Presets</h2>
              <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-2">
                {presets.map((preset) => (
                  <Card key={preset.id ?? preset.name}>
                    <CardContent className="p-3.5 space-y-2">
                      <p className="text-sm font-medium">{preset.name}</p>
                      <p className="text-xs text-shell-text-tertiary">{preset.description}</p>
                      {preset.schedule && (
                        <p className="text-[11px] text-shell-text-tertiary font-mono">{preset.schedule}</p>
                      )}
                      {preset.tasks && preset.tasks.length > 0 && (
                        <p className="text-[11px] text-shell-text-tertiary">{preset.tasks.length} routines</p>
                      )}
                      <Button
                        variant="secondary"
                        size="sm"
                        onClick={() => applyPreset(preset)}
                      >
                        <Play size={11} />
                        Apply
                      </Button>
                    </CardContent>
                  </Card>
                ))}
              </div>
            </div>
          </div>
        ) : (
          <div className="p-4 space-y-4">
            {/* Task rows */}
            {tasks.length > 0 && (
              <div className="space-y-2" aria-label="Routine list">
                {tasks.map((task) => (
                  <Card key={task.id}>
                    <CardContent className="flex items-center gap-3 p-3">
                      <div className="flex-1 min-w-0">
                        <div className="flex items-center gap-2">
                          <p className="text-sm font-medium truncate">{task.name}</p>
                          <span className="text-xs text-shell-text-secondary">
                            {task.agent_name || "\u2014"}
                          </span>
                        </div>
                        <div className="flex flex-wrap items-center gap-x-3 gap-y-0.5 mt-1 text-xs text-shell-text-tertiary">
                          <span className="font-mono">{task.schedule}</span>
                          <span className="font-mono truncate max-w-[200px]">{task.command}</span>
                          <span className="tabular-nums">
                            Last: {task.last_run
                              ? new Date(Number(task.last_run) * 1000).toLocaleString()
                              : "Never"}
                          </span>
                        </div>
                      </div>
                      <Button
                        variant="ghost"
                        size="icon"
                        onClick={() => toggleEnabled(task.id)}
                        className={
                          task.enabled
                            ? "text-emerald-400 hover:text-emerald-300"
                            : "text-zinc-400"
                        }
                        aria-label={`Toggle ${task.name} ${task.enabled ? "off" : "on"}`}
                        title={task.enabled ? "Enabled" : "Disabled"}
                      >
                        {task.enabled ? <Play size={14} /> : <Pause size={14} />}
                      </Button>
                      <Button
                        variant="ghost"
                        size="icon"
                        onClick={() => openEdit(task)}
                        aria-label={`Edit ${task.name}`}
                        title="Edit"
                      >
                        <Edit size={15} />
                      </Button>
                      <Button
                        variant="ghost"
                        size="icon"
                        onClick={() => handleDelete(task.id)}
                        className="hover:text-red-400 hover:bg-red-500/15"
                        aria-label={`Delete ${task.name}`}
                        title="Delete"
                      >
                        <Trash2 size={15} />
                      </Button>
                    </CardContent>
                  </Card>
                ))}
              </div>
            )}

            {/* Presets */}
            <div>
              <h2 className="text-sm font-medium text-shell-text-secondary mb-3">Presets</h2>
              <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-2">
                {presets.map((preset) => (
                  <Card key={preset.id ?? preset.name}>
                    <CardContent className="p-3 space-y-1.5">
                      <p className="text-sm font-medium">{preset.name}</p>
                      <p className="text-xs text-shell-text-tertiary">{preset.description}</p>
                      <Button
                        variant="secondary"
                        size="sm"
                        onClick={() => applyPreset(preset)}
                      >
                        <Play size={11} />
                        Apply
                      </Button>
                    </CardContent>
                  </Card>
                ))}
              </div>
            </div>
          </div>
        )}
      </div>

      {/* Add/Edit Task Dialog */}
      {showDialog && (
        <div
          className="absolute inset-0 z-50 flex items-center justify-center bg-black/60 backdrop-blur-sm p-4"
          onClick={() => resetForm()}
          role="dialog"
          aria-modal="true"
          aria-label={editingId ? "Edit Routine" : "Add Routine"}
        >
          <Card
            className="w-full max-w-md max-h-full flex flex-col shadow-2xl overflow-hidden bg-shell-surface"
            onClick={(e) => e.stopPropagation()}
          >
            <CardHeader className="flex flex-row items-center justify-between border-b border-white/5 px-5 py-4 shrink-0">
              <CardTitle className="text-sm font-semibold">{editingId ? "Edit Routine" : "Add Routine"}</CardTitle>
              <Button
                variant="ghost"
                size="icon"
                onClick={resetForm}
                aria-label="Close dialog"
                className="h-7 w-7"
              >
                <X size={16} />
              </Button>
            </CardHeader>

            <CardContent className="px-5 py-4 space-y-3 flex-1 overflow-y-auto">
              <div className="space-y-1.5">
                <Label htmlFor="task-name">Name</Label>
                <Input
                  id="task-name"
                  type="text"
                  value={formName}
                  onChange={(e) => setFormName(e.target.value)}
                  placeholder="My Routine"
                  autoFocus
                />
              </div>
              <div className="space-y-1.5">
                <Label htmlFor="task-agent">Agent (optional)</Label>
                <select
                  id="task-agent"
                  value={formAgent}
                  onChange={(e) => setFormAgent(e.target.value)}
                  className="flex h-9 w-full rounded-lg border border-white/10 bg-shell-bg-deep px-3 py-1 text-sm text-shell-text focus-visible:outline-none focus-visible:border-accent/40 focus-visible:ring-2 focus-visible:ring-accent/20"
                >
                  <option value="">None</option>
                  {agents.map((a) => (
                    <option key={a} value={a}>{a}</option>
                  ))}
                </select>
              </div>
              <div className="space-y-1.5">
                <Label htmlFor="task-schedule">Schedule (cron)</Label>
                <Input
                  id="task-schedule"
                  type="text"
                  value={formSchedule}
                  onChange={(e) => setFormSchedule(e.target.value)}
                  placeholder="0 9 * * *"
                  className="font-mono"
                />
              </div>
              <div className="space-y-1.5">
                <Label htmlFor="task-command">Command</Label>
                <Input
                  id="task-command"
                  type="text"
                  value={formCommand}
                  onChange={(e) => setFormCommand(e.target.value)}
                  placeholder="summarize --last 24h"
                  className="font-mono"
                />
              </div>
              <div className="space-y-1.5">
                <Label htmlFor="task-desc">Description</Label>
                <Input
                  id="task-desc"
                  type="text"
                  value={formDesc}
                  onChange={(e) => setFormDesc(e.target.value)}
                  placeholder="Optional description"
                />
              </div>
            </CardContent>

            {formError && (
              <div role="alert" className="mx-5 mb-2 px-3 py-2 rounded-lg bg-red-500/15 border border-red-500/30 text-xs text-red-300">
                {formError}
              </div>
            )}

            <div className="flex items-center justify-end gap-2 px-5 py-3 border-t border-white/5 shrink-0">
              <Button
                variant="ghost"
                onClick={resetForm}
              >
                Cancel
              </Button>
              <Button
                onClick={handleSave}
                disabled={submitting || !formName.trim() || !formSchedule.trim() || !formCommand.trim()}
              >
                <Plus size={14} />
                {submitting ? "Saving..." : editingId ? "Save Changes" : "Add Routine"}
              </Button>
            </div>
          </Card>
        </div>
      )}
    </div>
  );
}
