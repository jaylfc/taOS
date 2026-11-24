import { useState, useEffect, useCallback, useRef } from "react";
import {
  HeartPulse, RefreshCw, ChevronRight, CheckCircle2, AlertTriangle, XCircle, HelpCircle,
} from "lucide-react";
import { Card, CardContent, Button } from "@/components/ui";
import { useRefreshOnFocus } from "@/hooks/use-refresh-on-focus";
import { useOnBackendRecovered } from "@/hooks/use-on-backend-recovered";
import { useProcessStore } from "@/stores/process-store";
import { getApp } from "@/registry/app-registry";
import {
  buildHealthRows,
  overallHealthStatus,
  type AgentSummary,
  type DetailedHealth,
  type HealthCheckEntry,
  type HealthRow,
  type HealthStatus,
  type MemoryStats,
} from "@/lib/system-health";

// #621: the System health glance card. One compact card at the top of the
// Activity app answering "is everything healthy" without digging: LLM
// gateway, agents, cluster workers, container backend, model backends,
// memory pipeline, recent errors. It only consumes existing endpoints
// (the derivation lives in lib/system-health.ts) and fills the gap the
// Activity and Cluster apps leave — CPU/RAM/disk/scheduler keep their
// dedicated cards below and are not duplicated here. Every unhealthy row
// is a button into the app that owns the fix.

const PILL_CLASS: Record<HealthStatus, string> = {
  ok: "bg-emerald-500/20 text-emerald-300",
  degraded: "bg-amber-500/20 text-amber-300",
  error: "bg-red-500/20 text-red-300",
  unavailable: "bg-white/10 text-shell-text-tertiary",
};

const PILL_LABEL: Record<HealthStatus, string> = {
  ok: "OK",
  degraded: "Degraded",
  error: "Error",
  unavailable: "Unknown",
};

const VERDICT_TEXT: Record<HealthStatus, string> = {
  ok: "All systems healthy",
  degraded: "Some systems need attention",
  error: "Some systems are down",
  unavailable: "Health unknown",
};

const STATUS_ICON: Record<HealthStatus, typeof CheckCircle2> = {
  ok: CheckCircle2,
  degraded: AlertTriangle,
  error: XCircle,
  unavailable: HelpCircle,
};

const ICON_CLASS: Record<HealthStatus, string> = {
  ok: "text-emerald-400",
  degraded: "text-amber-400",
  error: "text-red-400",
  unavailable: "text-shell-text-tertiary",
};

const APP_LABEL: Record<string, string> = {
  agents: "Agents",
  cluster: "Cluster",
  memory: "Memory",
  providers: "Providers",
};

// /api/health-check runs its probes sequentially server-side, each
// capped at 10 s (four fixed checks plus one per configured backend),
// so a legitimate round can take far longer than this card's 30 s poll
// interval. The bound below leaves room for that while guaranteeing a
// request that never settles cannot wedge the card's refresh
// coalescing: on timeout the endpoint's rows degrade and the next
// refresh can start.
const FETCH_TIMEOUT_MS = 90_000;

async function fetchJsonOrNull(url: string): Promise<unknown> {
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), FETCH_TIMEOUT_MS);
  try {
    const res = await fetch(url, {
      headers: { Accept: "application/json" },
      signal: controller.signal,
    });
    if (!res.ok) return null;
    return await res.json();
  } catch {
    return null;
  } finally {
    clearTimeout(timer);
  }
}

function HealthRowItem({
  row,
  onOpen,
}: {
  row: HealthRow;
  onOpen: (appId: string) => void;
}) {
  const Icon = STATUS_ICON[row.status];
  const content = (
    <>
      <span className="flex items-center gap-2 min-w-0">
        <Icon size={14} className={`shrink-0 ${ICON_CLASS[row.status]}`} aria-hidden="true" />
        <span className="text-xs font-medium text-shell-text shrink-0">{row.label}</span>
        <span className="text-xs text-shell-text-tertiary truncate">{row.detail}</span>
      </span>
      <span className="flex items-center gap-1 shrink-0">
        <span className={`text-[11px] px-1.5 py-0.5 rounded ${PILL_CLASS[row.status]}`}>
          {PILL_LABEL[row.status]}
        </span>
        {row.appId && (
          <ChevronRight size={14} className="text-shell-text-tertiary" aria-hidden="true" />
        )}
      </span>
    </>
  );
  if (row.appId) {
    return (
      <li>
        <button
          type="button"
          onClick={() => onOpen(row.appId!)}
          aria-label={`${row.label}: ${PILL_LABEL[row.status]} — open ${APP_LABEL[row.appId] ?? row.appId} app`}
          className="w-full flex items-center justify-between gap-3 py-2 px-1 -mx-1 rounded text-left hover:bg-white/[0.03]"
        >
          {content}
        </button>
      </li>
    );
  }
  return (
    <li className="flex items-center justify-between gap-3 py-2 px-1 -mx-1">{content}</li>
  );
}

export function SystemHealthCard() {
  const [rows, setRows] = useState<HealthRow[] | null>(null);
  const [busy, setBusy] = useState(true);
  const openWindow = useProcessStore((s) => s.openWindow);
  const inFlight = useRef(false);

  const load = useCallback(async () => {
    // Coalesce overlapping refreshes (poll, focus, backend recovery,
    // manual): /api/health-check runs its probes sequentially and can
    // legitimately take longer than the 30 s poll interval, so a second
    // load must neither discard the running one's result nor race it —
    // the running load's rows are the freshest there are.
    if (inFlight.current) return;
    inFlight.current = true;
    setBusy(true);
    try {
      const [health, healthCheck, agents, memoryStats] = await Promise.all([
        fetchJsonOrNull("/api/health?detailed=true"),
        fetchJsonOrNull("/api/health-check"),
        fetchJsonOrNull("/api/agents"),
        fetchJsonOrNull("/api/memory/stats"),
      ]);
      const checks = (healthCheck as { checks?: unknown } | null)?.checks;
      setRows(
        buildHealthRows({
          health: (health as DetailedHealth | null) ?? null,
          checks: Array.isArray(checks) ? (checks as HealthCheckEntry[]) : null,
          agents: Array.isArray(agents) ? (agents as AgentSummary[]) : null,
          memoryStats: (memoryStats as MemoryStats | null) ?? null,
        }),
      );
    } finally {
      inFlight.current = false;
      setBusy(false);
    }
  }, []);

  useEffect(() => {
    void load();
    // /api/health-check runs subprocess probes (incus, Docker) and a
    // per-backend HTTP check, so poll it far less often than the 2 s
    // activity feed below.
    const interval = setInterval(() => {
      void load();
    }, 30_000);
    return () => clearInterval(interval);
  }, [load]);

  useRefreshOnFocus(load);
  useOnBackendRecovered(load);

  const openApp = useCallback(
    (appId: string) => {
      const app = getApp(appId);
      if (app) openWindow(appId, app.defaultSize);
    },
    [openWindow],
  );

  const overall = rows ? overallHealthStatus(rows) : null;

  return (
    <Card className="col-span-12 p-4">
      <CardContent className="p-0">
        <div className="flex items-center justify-between mb-3">
          <div className="flex items-center gap-2 min-w-0">
            <HeartPulse size={14} className="text-rose-400 shrink-0" aria-hidden="true" />
            <h3 className="text-xs font-semibold text-shell-text">System health</h3>
            {overall && (
              <span className={`text-[11px] px-1.5 py-0.5 rounded ${PILL_CLASS[overall]}`}>
                {VERDICT_TEXT[overall]}
              </span>
            )}
          </div>
          <Button
            variant="ghost"
            size="icon"
            onClick={() => void load()}
            aria-label="Refresh system health"
          >
            <RefreshCw size={14} className={busy ? "animate-spin" : ""} />
          </Button>
        </div>
        {rows === null ? (
          <p className="text-xs text-shell-text-tertiary">Checking system health…</p>
        ) : (
          <ul className="divide-y divide-white/5" aria-label="System health checks">
            {rows.map((row) => (
              <HealthRowItem key={row.id} row={row} onOpen={openApp} />
            ))}
          </ul>
        )}
      </CardContent>
    </Card>
  );
}
