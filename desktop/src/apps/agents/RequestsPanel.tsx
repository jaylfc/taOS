import { useState, useEffect, useCallback, useRef } from "react";
import { RefreshCw, AlertCircle, CheckCircle2, XCircle, Clock } from "lucide-react";
import { Button } from "@/components/ui";
import { withCsrf } from "@/lib/csrf";

/* ------------------------------------------------------------------ */
/*  Types                                                              */
/* ------------------------------------------------------------------ */

export interface ScopeRequestRow {
  id: string;
  canonical_id: string;
  requested_scopes: string[];
  project_id: string | null;
  reason: string;
  status: string;
  created_ts: string;
  agent_display_name: string;
}

/* ------------------------------------------------------------------ */
/*  RequestsPanel                                                      */
/* ------------------------------------------------------------------ */

const FILTER_OPTIONS = [
  { value: "pending", label: "Pending" },
  { value: "all", label: "All" },
] as const;

type Filter = (typeof FILTER_OPTIONS)[number]["value"];

export function RequestsPanel() {
  const [requests, setRequests] = useState<ScopeRequestRow[]>([]);
  const [loading, setLoading] = useState(true);
  const [err, setErr] = useState<string | null>(null);
  const [actionErr, setActionErr] = useState<string | null>(null);
  const [filter, setFilter] = useState<Filter>("pending");
  const [acting, setActing] = useState<string | null>(null);
  const loadSeq = useRef(0);

  const load = useCallback(async () => {
    const seq = ++loadSeq.current;
    setErr(null);
    setLoading(true);
    try {
      const res = await fetch(
        `/api/agents/scope-requests?status=${encodeURIComponent(filter)}`,
        { credentials: "include" },
      );
      if (seq !== loadSeq.current) return;
      if (!res.ok) {
        const data = await res.json().catch(() => ({}));
        throw new Error(
          (data as { detail?: string }).detail ?? `Failed to load requests (${res.status})`
        );
      }
      const data = (await res.json()) as { requests?: ScopeRequestRow[] };
      if (seq !== loadSeq.current) return;
      setRequests(Array.isArray(data.requests) ? data.requests : []);
      const pending = (Array.isArray(data.requests) ? data.requests : []).filter(
        (r) => r.status === "pending",
      ).length;
      window.dispatchEvent(
        new CustomEvent("taos:scope-requests-count", { detail: { count: pending } }),
      );
    } catch (e: unknown) {
      if (seq !== loadSeq.current) return;
      setErr(e instanceof Error ? e.message : "Network error");
    } finally {
      if (seq === loadSeq.current) setLoading(false);
    }
  }, [filter]);

  useEffect(() => {
    load();
    const refresh = () => {
      if (!document.hidden) void load();
    };
    document.addEventListener("visibilitychange", refresh);
    window.addEventListener("focus", refresh);
    return () => {
      document.removeEventListener("visibilitychange", refresh);
      window.removeEventListener("focus", refresh);
    };
  }, [load]);

  async function act(req: ScopeRequestRow, approve: boolean) {
    setActing(req.id);
    setActionErr(null);
    try {
      const base =
        `/api/agents/registry/${encodeURIComponent(req.canonical_id)}/scope-requests/${encodeURIComponent(req.id)}`;
      const url = `${base}/${approve ? "approve" : "deny"}`;
      const body = approve
        ? JSON.stringify({
            granted_scopes: req.requested_scopes,
            ...(req.project_id ? { project_id: req.project_id } : {}),
          })
        : undefined;
      const res = await fetch(
        url,
        withCsrf({
          method: "POST",
          headers: body ? { "Content-Type": "application/json" } : {},
          body,
          credentials: "include",
        }),
      );
      if (!res.ok) {
        const data = await res.json().catch(() => ({}));
        throw new Error(
          (data as { detail?: string }).detail ?? `Action failed (${res.status})`
        );
      }
      await load();
    } catch (e: unknown) {
      setActionErr(e instanceof Error ? e.message : "Network error");
    } finally {
      setActing(null);
    }
  }

  const pendingCount = requests.filter((r) => r.status === "pending").length;

  return (
    <section aria-label="Scope requests" className="flex flex-col h-full min-h-0">
      {/* Header with filter tabs */}
      <div className="flex items-center justify-between gap-2 px-4 py-3 border-b border-white/5 select-none">
        <div className="flex items-center gap-3" role="tablist" aria-label="Request filters">
          {FILTER_OPTIONS.map((opt) => (
            <button
              key={opt.value}
              role="tab"
              aria-selected={filter === opt.value}
              onClick={() => setFilter(opt.value)}
              className={`text-xs font-medium px-2.5 py-1 rounded-md transition-colors ${
                filter === opt.value
                  ? "bg-white/10 text-shell-text"
                  : "text-shell-text-secondary hover:text-shell-text hover:bg-white/5"
              }`}
            >
              {opt.label}
              {opt.value === "pending" && pendingCount > 0 && (
                <span className="ml-1.5 inline-flex items-center justify-center rounded-full bg-amber-500/20 text-amber-300 text-[10px] px-1.5 py-0.5 min-w-[18px]">
                  {pendingCount}
                </span>
              )}
            </button>
          ))}
        </div>
        <button
          type="button"
          onClick={load}
          disabled={loading}
          className="text-shell-text-secondary hover:text-shell-text disabled:opacity-50"
          aria-label="Refresh requests"
        >
          <RefreshCw size={14} className={loading ? "animate-spin" : ""} />
        </button>
      </div>

      {/* Content */}
      <div className="flex-1 overflow-auto">
        {err ? (
          <div className="p-4">
            <div
              className="flex items-start gap-3 rounded-lg border border-red-500/30 bg-red-500/10 p-4"
              role="alert"
            >
              <AlertCircle size={18} className="text-red-400 shrink-0 mt-0.5" />
              <div className="flex-1 min-w-0">
                <p className="text-sm text-red-300 font-medium">Failed to load requests</p>
                <p className="text-xs text-red-400 mt-1">{err}</p>
              </div>
              <Button
                size="sm"
                variant="outline"
                onClick={load}
                className="border-red-500/30 hover:bg-red-500/10 shrink-0"
              >
                Retry
              </Button>
            </div>
          </div>
        ) : loading ? (
          <div className="p-4 space-y-3" aria-busy="true" aria-label="Loading requests">
            {[0, 1, 2].map((i) => (
              <div
                key={i}
                className="h-16 rounded-lg border border-white/5 bg-white/3 taos-shimmer"
              />
            ))}
          </div>
        ) : requests.length === 0 ? (
          <div className="flex flex-col items-center justify-center py-12 px-4">
            <div className="w-12 h-12 rounded-lg flex items-center justify-center bg-shell-surface border border-shell-border mb-3">
              <Clock size={20} className="text-shell-text-tertiary" />
            </div>
            <p className="text-sm font-medium text-shell-text">
              {filter === "all" ? "No requests" : "No pending requests"}
            </p>
            <p className="text-xs text-shell-text-secondary mt-1">
              {filter === "all"
                ? "No scope requests have been submitted yet."
                : "All caught up — new requests will appear here automatically."}
            </p>
          </div>
        ) : (
          <>
            {actionErr && (
              <div className="p-4">
                <div
                  className="flex items-start gap-3 rounded-lg border border-red-500/30 bg-red-500/10 p-4"
                  role="alert"
                >
                  <AlertCircle size={18} className="text-red-400 shrink-0 mt-0.5" />
                  <div className="flex-1 min-w-0">
                    <p className="text-sm text-red-300 font-medium">Action failed</p>
                    <p className="text-xs text-red-400 mt-1">{actionErr}</p>
                  </div>
                  <Button
                    size="sm"
                    variant="outline"
                    onClick={() => setActionErr(null)}
                    className="border-red-500/30 hover:bg-red-500/10 shrink-0"
                    aria-label="Dismiss action error"
                  >
                    Dismiss
                  </Button>
                </div>
              </div>
            )}
            <ul className="divide-y divide-white/5" role="list" aria-label="Scope request list">
            {requests.map((req) => {
              const isPending = req.status === "pending";
              const actingThis = acting === req.id;
              const age = relativeTime(req.created_ts);
              return (
                <li
                  key={req.id}
                  className="px-4 py-3 hover:bg-white/3 transition-colors"
                >
                  <div className="flex items-start justify-between gap-3">
                    <div className="flex-1 min-w-0">
                      <div className="flex items-center gap-2 flex-wrap">
                        <span className="text-sm font-medium text-shell-text truncate">
                          {req.agent_display_name || req.canonical_id}
                        </span>
                        <span
                          className={`inline-flex items-center rounded-full px-2 py-0.5 text-[10px] font-medium ${
                            isPending
                              ? "bg-amber-500/20 text-amber-300"
                              : req.status === "accepted"
                                ? "bg-emerald-500/20 text-emerald-300"
                                : "bg-red-500/20 text-red-300"
                          }`}
                        >
                          {isPending ? "Pending" : req.status}
                        </span>
                      </div>
                      {req.reason && (
                        <p className="text-xs text-shell-text-secondary mt-0.5 line-clamp-1">
                          {req.reason}
                        </p>
                      )}
                      <div className="flex items-center gap-2 mt-1.5 flex-wrap">
                        {req.requested_scopes.map((scope) => (
                          <code
                            key={scope}
                            className="text-[10px] font-mono bg-white/5 border border-white/10 rounded px-1.5 py-0.5 text-shell-text-secondary"
                          >
                            {scope}
                          </code>
                        ))}
                      </div>
                      <div className="flex items-center gap-3 mt-1.5 text-[10px] text-shell-text-tertiary">
                        <span>Requested {age}</span>
                        {req.project_id && (
                          <span className="truncate">Project: {req.project_id}</span>
                        )}
                        <code className="font-mono truncate">{req.id}</code>
                      </div>
                    </div>
                    {isPending && (
                      <div className="flex items-center gap-1.5 shrink-0">
                        <Button
                          size="sm"
                          variant="ghost"
                          onClick={() => act(req, true)}
                          disabled={actingThis}
                          aria-label={`Approve request from ${req.agent_display_name || req.canonical_id} for ${req.requested_scopes.join(", ")}`}
                          className="text-emerald-400 hover:text-emerald-300 hover:bg-emerald-500/10"
                        >
                          <CheckCircle2 size={14} aria-hidden="true" />
                          <span className="hidden sm:inline">Approve</span>
                        </Button>
                        <Button
                          size="sm"
                          variant="ghost"
                          onClick={() => act(req, false)}
                          disabled={actingThis}
                          aria-label={`Deny request from ${req.agent_display_name || req.canonical_id} for ${req.requested_scopes.join(", ")}`}
                          className="text-red-400 hover:text-red-300 hover:bg-red-500/10"
                        >
                          <XCircle size={14} aria-hidden="true" />
                          <span className="hidden sm:inline">Deny</span>
                        </Button>
                      </div>
                    )}
                  </div>
                </li>
              );
            })}
           </ul>
        </>
      )}
    </div>
    </section>
  );
}

/* ------------------------------------------------------------------ */
/*  Helpers                                                            */
/* ------------------------------------------------------------------ */

function relativeTime(ts: string): string {
  const d = new Date(ts);
  if (isNaN(d.getTime())) return ts;
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
