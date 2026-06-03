import { useState, useEffect } from "react";
import { RefreshCw } from "lucide-react";
import { Button } from "@/components/ui";

/* ------------------------------------------------------------------ */
/*  safeFetch                                                          */
/* ------------------------------------------------------------------ */

export async function safeFetch<T>(url: string, fallback: T): Promise<T> {
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

/* ------------------------------------------------------------------ */
/*  ProgressBar                                                        */
/* ------------------------------------------------------------------ */

export function ProgressBar({ value, max }: { value: number; max: number }) {
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
/*  RestartProgressModal                                               */
/* ------------------------------------------------------------------ */

interface RestartOrchestratorStatus {
  phase: string;
  reason: string;
  started_at: number;
  agents: Record<string, { status: string; duration_s: number; note_path: string | null }>;
}

export function RestartProgressModal({
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
