import { useState, useEffect, useCallback } from "react";
import {
  ChevronRight,
  ShieldCheck,
  CheckCircle,
  XCircle,
  PauseCircle,
  PlayCircle,
  ShieldOff,
  RefreshCw,
} from "lucide-react";
import { Button, Card } from "@/components/ui";

/* ------------------------------------------------------------------ */
/*  Types                                                               */
/* ------------------------------------------------------------------ */

export type RegistryStatus =
  | "pending"
  | "active"
  | "suspended"
  | "rejected"
  | "revoked";

export interface RegistryEntry {
  canonical_id: string;
  framework: string;
  display_name: string;
  user_id: string;
  origin: string;
  handle: string;
  role: string | null;
  capabilities: string[];
  status: RegistryStatus;
  registered_at: string;
  updated_at: string | null;
  revoked_at: string | null;
}

/* ------------------------------------------------------------------ */
/*  Helpers                                                             */
/* ------------------------------------------------------------------ */

function relativeTime(ts: string | null): string {
  if (!ts) return "—";
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

const STATUS_STYLES: Record<RegistryStatus, string> = {
  pending: "bg-amber-500/20 text-amber-300 border-amber-500/30",
  active: "bg-emerald-500/20 text-emerald-300 border-emerald-500/30",
  suspended: "bg-orange-500/20 text-orange-300 border-orange-500/30",
  rejected: "bg-red-500/20 text-red-300 border-red-500/30",
  revoked: "bg-white/5 text-shell-text-tertiary border-white/10",
};

async function registryAction(
  canonical_id: string,
  action: "approve" | "reject" | "suspend" | "reactivate" | "revoke",
): Promise<RegistryEntry> {
  const method = action === "revoke" ? "DELETE" : "POST";
  const url =
    action === "revoke"
      ? `/api/agents/registry/${encodeURIComponent(canonical_id)}`
      : `/api/agents/registry/${encodeURIComponent(canonical_id)}/${action}`;
  const resp = await fetch(url, { method, credentials: "include" });
  if (!resp.ok) {
    const err = await resp.json().catch(() => ({}));
    throw new Error((err as { error?: string }).error ?? `HTTP ${resp.status}`);
  }
  return resp.json();
}

/* ------------------------------------------------------------------ */
/*  RegistryEntryRow                                                    */
/* ------------------------------------------------------------------ */

function RegistryEntryRow({
  entry,
  isAdmin,
  currentUserId,
  onAction,
}: {
  entry: RegistryEntry;
  isAdmin: boolean;
  currentUserId: string;
  onAction: (id: string, action: "approve" | "reject" | "suspend" | "reactivate" | "revoke") => Promise<void>;
}) {
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const isOwner = entry.user_id === currentUserId;
  const canRevoke = (isAdmin || isOwner) && (entry.status === "active" || entry.status === "suspended");

  async function act(action: "approve" | "reject" | "suspend" | "reactivate" | "revoke") {
    setBusy(true);
    setErr(null);
    try {
      await onAction(entry.canonical_id, action);
    } catch (e: unknown) {
      setErr(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  }

  return (
    <Card className="flex flex-col gap-2 px-4 py-3">
      <div className="flex items-center gap-3 flex-wrap">
        <div className="flex-1 min-w-0">
          <div className="flex items-center gap-2 flex-wrap">
            <span className="font-medium text-sm truncate">
              {entry.display_name || entry.handle || entry.framework}
            </span>
            <span
              className={`inline-flex items-center px-1.5 py-0.5 rounded text-[10px] font-medium border ${STATUS_STYLES[entry.status] ?? STATUS_STYLES.revoked}`}
              aria-label={`Status: ${entry.status}`}
            >
              {entry.status}
            </span>
            <span className="text-[11px] text-shell-text-tertiary">{entry.framework}</span>
          </div>
          <div className="flex items-center gap-3 mt-0.5 flex-wrap">
            <code
              className="text-[10px] text-shell-text-tertiary font-mono truncate max-w-[220px]"
              title={entry.canonical_id}
            >
              {entry.canonical_id}
            </code>
            <span className="text-[11px] text-shell-text-tertiary">
              registered {relativeTime(entry.registered_at)}
            </span>
            {isAdmin && (
              <span className="text-[11px] text-shell-text-tertiary">
                by {entry.user_id}
              </span>
            )}
          </div>
        </div>

        <div className="flex items-center gap-1 shrink-0" role="group" aria-label="Registry actions">
          {entry.status === "pending" && isAdmin && (
            <>
              <Button
                variant="ghost"
                size="icon"
                className="h-7 w-7 hover:bg-emerald-500/15 hover:text-emerald-400"
                onClick={() => act("approve")}
                disabled={busy}
                aria-label={`Approve ${entry.display_name || entry.canonical_id}`}
                title="Approve"
              >
                <CheckCircle size={14} />
              </Button>
              <Button
                variant="ghost"
                size="icon"
                className="h-7 w-7 hover:bg-red-500/15 hover:text-red-400"
                onClick={() => act("reject")}
                disabled={busy}
                aria-label={`Reject ${entry.display_name || entry.canonical_id}`}
                title="Reject"
              >
                <XCircle size={14} />
              </Button>
            </>
          )}
          {entry.status === "active" && isAdmin && (
            <Button
              variant="ghost"
              size="icon"
              className="h-7 w-7 hover:bg-orange-500/15 hover:text-orange-400"
              onClick={() => act("suspend")}
              disabled={busy}
              aria-label={`Suspend ${entry.display_name || entry.canonical_id}`}
              title="Suspend"
            >
              <PauseCircle size={14} />
            </Button>
          )}
          {entry.status === "suspended" && isAdmin && (
            <Button
              variant="ghost"
              size="icon"
              className="h-7 w-7 hover:bg-emerald-500/15 hover:text-emerald-400"
              onClick={() => act("reactivate")}
              disabled={busy}
              aria-label={`Reactivate ${entry.display_name || entry.canonical_id}`}
              title="Reactivate"
            >
              <PlayCircle size={14} />
            </Button>
          )}
          {canRevoke && (
            <Button
              variant="ghost"
              size="icon"
              className="h-7 w-7 hover:bg-red-500/15 hover:text-red-400"
              onClick={() => act("revoke")}
              disabled={busy}
              aria-label={`Revoke ${entry.display_name || entry.canonical_id}`}
              title="Revoke"
            >
              <ShieldOff size={14} />
            </Button>
          )}
        </div>
      </div>
      {err && (
        <p className="text-[11px] text-red-400" role="alert">{err}</p>
      )}
    </Card>
  );
}

/* ------------------------------------------------------------------ */
/*  RegistryPanel                                                       */
/* ------------------------------------------------------------------ */

export function RegistryPanel() {
  const [expanded, setExpanded] = useState(false);
  const [entries, setEntries] = useState<RegistryEntry[]>([]);
  const [isAdmin, setIsAdmin] = useState(false);
  const [currentUserId, setCurrentUserId] = useState("");
  const [loading, setLoading] = useState(false);
  const [err, setErr] = useState<string | null>(null);

  const load = useCallback(async () => {
    setLoading(true);
    setErr(null);
    try {
      const [statusResp, registryResp] = await Promise.all([
        fetch("/auth/status", { credentials: "include" }),
        fetch("/api/agents/registry", { credentials: "include" }),
      ]);
      if (statusResp.ok) {
        const s = await statusResp.json();
        setIsAdmin(!!s.user?.is_admin);
        setCurrentUserId(s.user?.id ?? "");
      }
      if (registryResp.ok) {
        const data = await registryResp.json();
        setEntries(Array.isArray(data) ? data : []);
      } else if (registryResp.status !== 404) {
        setErr(`Failed to load registry (${registryResp.status})`);
      }
    } catch (e: unknown) {
      setErr(e instanceof Error ? e.message : "Network error");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    if (expanded) load();
  }, [expanded, load]);

  async function handleAction(
    canonical_id: string,
    action: "approve" | "reject" | "suspend" | "reactivate" | "revoke",
  ) {
    await registryAction(canonical_id, action);
    await load();
  }

  const pendingCount = entries.filter((e) => e.status === "pending").length;

  return (
    <section className="mt-4" aria-label="Agent registry">
      <button
        onClick={() => setExpanded((v) => !v)}
        className="flex items-center gap-2 text-xs text-shell-text-secondary hover:text-shell-text transition-colors mb-2 w-full"
        aria-expanded={expanded}
        aria-controls="agent-registry-panel"
      >
        <ChevronRight
          size={14}
          className={`transition-transform shrink-0 ${expanded ? "rotate-90" : ""}`}
          aria-hidden
        />
        <ShieldCheck size={13} aria-hidden />
        <span>Agent Registry</span>
        {entries.length > 0 && (
          <span className="text-shell-text-tertiary">({entries.length})</span>
        )}
        {pendingCount > 0 && (
          <span
            className="ml-1 px-1.5 py-0.5 rounded-full text-[10px] font-medium bg-amber-500/25 text-amber-300 border border-amber-500/30"
            aria-label={`${pendingCount} pending approval`}
          >
            {pendingCount} pending
          </span>
        )}
      </button>

      <div
        id="agent-registry-panel"
        className={`space-y-2 ${expanded ? "" : "hidden"}`}
      >
        {loading ? (
          <div className="flex items-center gap-2 text-xs text-shell-text-tertiary py-2">
            <RefreshCw size={12} className="animate-spin" aria-hidden />
            Loading registry…
          </div>
        ) : err ? (
          <p className="text-xs text-red-400" role="alert">{err}</p>
        ) : entries.length === 0 ? (
          <p className="text-xs text-shell-text-tertiary py-1">
            No registered agents yet.
          </p>
        ) : (
          entries.map((entry) => (
            <RegistryEntryRow
              key={entry.canonical_id}
              entry={entry}
              isAdmin={isAdmin}
              currentUserId={currentUserId}
              onAction={handleAction}
            />
          ))
        )}
      </div>
    </section>
  );
}
