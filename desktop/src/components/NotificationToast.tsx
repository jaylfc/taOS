import { useEffect, useState } from "react";
import { X, Info, CheckCircle, AlertTriangle, AlertCircle, Server, Bot, BellOff } from "lucide-react";
import { useNotificationStore, type Notification } from "@/stores/notification-store";
import { useProcessStore } from "@/stores/process-store";
import { getApp } from "@/registry/app-registry";

const LEVEL_ICONS = {
  info: Info,
  success: CheckCircle,
  warning: AlertTriangle,
  error: AlertCircle,
};

const LEVEL_COLORS = {
  info: "text-blue-400 bg-blue-500/10 border-blue-500/20",
  success: "text-emerald-400 bg-emerald-500/10 border-emerald-500/20",
  warning: "text-amber-400 bg-amber-500/10 border-amber-500/20",
  error: "text-red-400 bg-red-500/10 border-red-500/20",
};

/* ------------------------------------------------------------------ */
/*  Model picker modal for agent.paused notifications                  */
/* ------------------------------------------------------------------ */

interface ReachableModel {
  id: string;
  name: string;
  host?: string;
}

function ModelPickerModal({
  agentName,
  onPick,
  onCancel,
}: {
  agentName: string;
  onPick: (modelId: string) => void;
  onCancel: () => void;
}) {
  const [models, setModels] = useState<ReachableModel[]>([]);
  const [loading, setLoading] = useState(true);
  const [selected, setSelected] = useState("");
  const [applying, setApplying] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    (async () => {
      const found: ReachableModel[] = [];
      try {
        // Local backend catalog
        const res = await fetch("/api/models", { headers: { Accept: "application/json" } });
        if (res.ok) {
          const data = await res.json();
          const list = Array.isArray(data) ? data : Array.isArray(data?.models) ? data.models : [];
          for (const m of list.filter((m: Record<string, unknown>) => m.has_downloaded_variant === true)) {
            found.push({ id: String(m.id), name: String(m.name ?? m.id), host: "controller" });
          }
        }
      } catch { /* ignore */ }
      try {
        // Cluster workers
        const res = await fetch("/api/cluster/workers", { headers: { Accept: "application/json" } });
        if (res.ok) {
          const workers = await res.json();
          for (const w of Array.isArray(workers) ? workers : []) {
            if (w.status !== "online") continue;
            for (const b of w.backends ?? []) {
              for (const m of b.models ?? []) {
                const mid = m.name ?? m.id ?? "";
                if (mid) found.push({ id: mid, name: mid, host: w.name });
              }
            }
          }
        }
      } catch { /* ignore */ }
      setModels(found);
      setLoading(false);
    })();
  }, []);

  async function handleApply() {
    if (!selected) return;
    setApplying(true);
    setError(null);
    try {
      const res = await fetch(`/api/agents/${encodeURIComponent(agentName)}/model`, {
        method: "POST",
        headers: { "Content-Type": "application/json", Accept: "application/json" },
        body: JSON.stringify({ model: selected }),
      });
      if (!res.ok) {
        const body = await res.json().catch(() => ({}));
        setError(body?.error ?? `Request failed (${res.status})`);
        setApplying(false);
        return;
      }
      onPick(selected);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Network error");
      setApplying(false);
    }
  }

  return (
    <div
      className="fixed inset-0 z-[10100] flex items-center justify-center bg-black/60 backdrop-blur-sm"
      role="dialog"
      aria-modal="true"
      aria-label="Pick alternate model"
    >
      <div className="w-full max-w-sm bg-shell-surface rounded-xl border border-white/10 shadow-2xl overflow-hidden flex flex-col max-h-[80vh]">
        <div className="flex items-center justify-between px-4 py-3 border-b border-white/5 shrink-0">
          <div className="flex items-center gap-2 text-sm font-semibold">
            <Bot size={15} className="text-accent" />
            Pick alternate model for <span className="text-accent">{agentName}</span>
          </div>
          <button
            onClick={onCancel}
            className="text-shell-text-tertiary hover:text-shell-text"
            aria-label="Close model picker"
          >
            <X size={14} />
          </button>
        </div>
        <div className="flex-1 min-h-0 overflow-y-auto p-3 space-y-1.5">
          {loading && (
            <p className="text-xs text-shell-text-secondary py-4 text-center">Loading models...</p>
          )}
          {!loading && models.length === 0 && (
            <p className="text-xs text-shell-text-secondary py-4 text-center">
              No reachable models found. Check that at least one worker is online.
            </p>
          )}
          {models.map((m) => {
            const key = `${m.host ?? "?"}:${m.id}`;
            return (
              <button
                key={key}
                onClick={() => setSelected(m.id)}
                className={`w-full text-left px-3 py-2 rounded-lg border text-sm transition-colors ${
                  selected === m.id
                    ? "border-accent bg-accent/10"
                    : "border-white/5 bg-shell-bg-deep hover:bg-white/5"
                }`}
              >
                <div className="font-medium truncate">{m.name}</div>
                {m.host && m.host !== "controller" && (
                  <div className="text-xs text-shell-text-tertiary">{m.host}</div>
                )}
              </button>
            );
          })}
        </div>
        {error && (
          <p
            role="alert"
            className="mx-3 mb-2 px-3 py-1.5 rounded-lg bg-red-500/15 border border-red-500/30 text-xs text-red-300"
          >
            {error}
          </p>
        )}
        <div className="flex items-center justify-end gap-2 px-4 py-3 border-t border-white/5 shrink-0">
          <button
            onClick={onCancel}
            className="px-3 py-1.5 rounded-lg text-xs text-shell-text-secondary hover:text-shell-text border border-white/10 hover:bg-white/5 transition-colors"
          >
            Cancel
          </button>
          <button
            onClick={handleApply}
            disabled={!selected || applying}
            className="px-3 py-1.5 rounded-lg text-xs font-medium bg-accent hover:brightness-110 text-white disabled:opacity-50 disabled:cursor-not-allowed transition-all"
          >
            {applying ? "Applying..." : "Apply & Resume"}
          </button>
        </div>
      </div>
    </div>
  );
}

/* ------------------------------------------------------------------ */
/*  Agent-paused action buttons                                        */
/* ------------------------------------------------------------------ */

function AgentPausedActions({
  notif,
  onDismiss,
}: {
  notif: Notification;
  onDismiss: () => void;
}) {
  const [showPicker, setShowPicker] = useState(false);
  const openWindow = useProcessStore((s) => s.openWindow);

  // Extract agent and worker names from the notification source/body.
  // The failure handler encodes them as "agent:NAME worker:NAME" in the
  // message body.
  const agentName = notif.meta?.agent ?? extractTagged(notif.body, "agent") ?? "unknown";
  const workerName = notif.meta?.worker ?? extractTagged(notif.body, "worker") ?? "unknown";

  function handleCheckWorker() {
    const clusterApp = getApp("cluster");
    if (clusterApp) openWindow("cluster", clusterApp.defaultSize);
    onDismiss();
  }

  function handlePickModel() {
    setShowPicker(true);
  }

  function handleModelPicked(modelId: string) {
    setShowPicker(false);
    onDismiss();
    // Trigger a soft-reload of the agents list by dispatching a custom event
    // that AgentsApp listens on.  Avoids tight coupling via shared store.
    window.dispatchEvent(new CustomEvent("taos:agent-resumed", { detail: { agent: agentName, model: modelId } }));
  }

  return (
    <>
      <div className="flex flex-wrap gap-1.5 mt-2" role="group" aria-label="Recovery actions">
        <button
          onClick={handleCheckWorker}
          className="flex items-center gap-1 px-2 py-1 rounded-md text-[11px] font-medium bg-white/5 hover:bg-white/10 text-shell-text-secondary hover:text-shell-text border border-white/10 transition-colors"
          title={`Open Cluster app focused on '${workerName}'`}
        >
          <Server size={11} />
          Check worker
        </button>
        <button
          onClick={handlePickModel}
          className="flex items-center gap-1 px-2 py-1 rounded-md text-[11px] font-medium bg-accent/15 hover:bg-accent/25 text-accent border border-accent/20 transition-colors"
        >
          <Bot size={11} />
          Pick alternate model
        </button>
        <button
          onClick={onDismiss}
          className="flex items-center gap-1 px-2 py-1 rounded-md text-[11px] font-medium bg-white/5 hover:bg-white/10 text-shell-text-tertiary hover:text-shell-text border border-white/10 transition-colors"
          title="Dismiss and leave agent paused"
        >
          <BellOff size={11} />
          Keep paused
        </button>
      </div>
      {showPicker && (
        <ModelPickerModal
          agentName={agentName}
          onPick={handleModelPicked}
          onCancel={() => setShowPicker(false)}
        />
      )}
    </>
  );
}

function extractTagged(text: string, tag: string): string | undefined {
  const match = text.match(new RegExp(`${tag}:(\\S+)`));
  return match?.[1];
}

/* ------------------------------------------------------------------ */
/*  ToastItem                                                          */
/* ------------------------------------------------------------------ */

function ToastItem({ notif }: { notif: Notification }) {
  const dismiss = useNotificationStore((s) => s.dismiss);
  const Icon = LEVEL_ICONS[notif.level];
  const isAgentPaused = notif.source === "agent.paused";

  useEffect(() => {
    // Agent-paused toasts stay until the user explicitly acts on them.
    if (isAgentPaused) return;
    const timer = setTimeout(() => dismiss(notif.id), 5000);
    return () => clearTimeout(timer);
  }, [notif.id, dismiss, isAgentPaused]);

  return (
    <div
      className={`flex items-start gap-3 p-3 rounded-xl border backdrop-blur-lg shadow-xl w-80 ${LEVEL_COLORS[notif.level]}`}
      style={{ backgroundColor: "var(--color-dock-bg)" }}
      role="alert"
      aria-live="assertive"
    >
      <Icon size={18} className="shrink-0 mt-0.5" aria-hidden="true" />
      <div className="flex-1 min-w-0">
        <div className="text-sm font-medium text-shell-text">{notif.title}</div>
        {notif.body && (
          <div className="text-xs text-shell-text-secondary mt-0.5 line-clamp-3">{notif.body}</div>
        )}
        {isAgentPaused && (
          <AgentPausedActions notif={notif} onDismiss={() => dismiss(notif.id)} />
        )}
      </div>
      {!isAgentPaused && (
        <button
          onClick={() => dismiss(notif.id)}
          className="shrink-0 text-shell-text-tertiary hover:text-shell-text"
          aria-label="Dismiss notification"
        >
          <X size={14} />
        </button>
      )}
    </div>
  );
}

export function NotificationToasts() {
  const notifications = useNotificationStore((s) => s.notifications);
  // Only show unread, non-persisted notifications as toasts (latest 3)
  const active = notifications.filter((n) => !n.read).slice(0, 3);

  return (
    <div
      className="fixed top-12 right-4 z-[10001] flex flex-col gap-2 pointer-events-auto"
      aria-label="Notifications"
      role="region"
    >
      {active.map((n) => (
        <ToastItem key={n.id} notif={n} />
      ))}
    </div>
  );
}
