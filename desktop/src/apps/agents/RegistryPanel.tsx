import { useState, useEffect, useCallback, useRef } from "react";
import { createPortal } from "react-dom";
import {
  ChevronRight,
  ShieldCheck,
  CheckCircle,
  XCircle,
  PauseCircle,
  PlayCircle,
  ShieldOff,
  RefreshCw,
  ScrollText,
  ArrowRight,
  UserPlus,
} from "lucide-react";
import { Button, Card } from "@/components/ui";
import { projectsApi } from "@/lib/projects";
import { AssignAgentToProjectDialog } from "./AssignAgentToProjectDialog";
import { InviteAgentDialog } from "@/apps/ProjectsApp/InviteAgentDialog";

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

/** Strip a leading "@" for display only, "@" is bus-addressing syntax, not a name. */
export function stripAt(s: string): string {
  return s.startsWith("@") ? s.slice(1) : s;
}

/** True when two registry snapshots are identical by id and content (poll no-op). */
export function registryEntriesEqual(a: RegistryEntry[], b: RegistryEntry[]): boolean {
  if (a === b) return true;
  if (a.length !== b.length) return false;
  for (let i = 0; i < a.length; i++) {
    const x = a[i];
    const y = b[i];
    if (!x || !y) return false;
    if (
      x.canonical_id !== y.canonical_id ||
      x.framework !== y.framework ||
      x.display_name !== y.display_name ||
      x.user_id !== y.user_id ||
      x.origin !== y.origin ||
      x.handle !== y.handle ||
      x.role !== y.role ||
      x.status !== y.status ||
      x.registered_at !== y.registered_at ||
      x.updated_at !== y.updated_at ||
      x.revoked_at !== y.revoked_at
    ) {
      return false;
    }
    const xc = x.capabilities;
    const yc = y.capabilities;
    if (xc.length !== yc.length || xc.some((c, j) => c !== yc[j])) return false;
  }
  return true;
}

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
  onAssign,
}: {
  entry: RegistryEntry;
  isAdmin: boolean;
  currentUserId: string;
  onAction: (id: string, action: "approve" | "reject" | "suspend" | "reactivate" | "revoke") => Promise<void>;
  onAssign: (entry: RegistryEntry) => void;
}) {
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const isOwner = entry.user_id === currentUserId;
  const canRevoke = (isAdmin || isOwner) && (entry.status === "active" || entry.status === "suspended");
  const canAssign = (isAdmin || isOwner) && entry.status === "active";

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
              {stripAt(entry.display_name || entry.handle || entry.framework)}
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
                aria-label={`Approve ${stripAt(entry.display_name) || entry.canonical_id}`}
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
                aria-label={`Reject ${stripAt(entry.display_name) || entry.canonical_id}`}
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
              aria-label={`Suspend ${stripAt(entry.display_name) || entry.canonical_id}`}
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
              aria-label={`Reactivate ${stripAt(entry.display_name) || entry.canonical_id}`}
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
              aria-label={`Revoke ${stripAt(entry.display_name) || entry.canonical_id}`}
              title="Revoke"
            >
              <ShieldOff size={14} />
            </Button>
          )}
          {canAssign && (
            <Button
              variant="ghost"
              size="icon"
              className="h-7 w-7 hover:bg-blue-500/15 hover:text-blue-400"
              onClick={() => onAssign(entry)}
              disabled={busy}
              aria-label={`Assign ${stripAt(entry.display_name) || entry.canonical_id} to project`}
              title="Assign to project"
            >
              <UserPlus size={14} />
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
/*  GovernanceAuditPanel                                                */
/* ------------------------------------------------------------------ */

interface GovernanceEvent {
  id: string;
  ts: number;
  payload: {
    action: string;
    canonical_id: string;
    actor_user_id: string;
    before_status: string;
    after_status: string;
  };
}

const ACTION_LABELS: Record<string, string> = {
  approve: "approved",
  reject: "rejected",
  suspend: "suspended",
  reactivate: "reactivated",
  revoke: "revoked",
};

function GovernanceAuditPanel({ isAdmin }: { isAdmin: boolean }) {
  const [expanded, setExpanded] = useState(false);
  const [events, setEvents] = useState<GovernanceEvent[]>([]);
  const [loading, setLoading] = useState(false);
  const [err, setErr] = useState<string | null>(null);

  const load = useCallback(async () => {
    setLoading(true);
    setErr(null);
    try {
      const resp = await fetch(
        "/api/agents/taos-governance/trace?kind=governance&limit=50",
        { credentials: "include" },
      );
      if (resp.ok) {
        const data = await resp.json();
        setEvents((data.events ?? []) as GovernanceEvent[]);
      } else if (resp.status !== 404) {
        setErr(`Failed to load audit log (${resp.status})`);
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

  if (!isAdmin) return null;

  return (
    <section className="mt-3" aria-label="Governance audit log">
      <button
        onClick={() => setExpanded((v) => !v)}
        className="flex items-center gap-2 text-xs text-shell-text-tertiary hover:text-shell-text-secondary transition-colors mb-2 w-full"
        aria-expanded={expanded}
        aria-controls="governance-audit-panel"
      >
        <ChevronRight
          size={13}
          className={`transition-transform shrink-0 ${expanded ? "rotate-90" : ""}`}
          aria-hidden
        />
        <ScrollText size={12} aria-hidden />
        <span>Governance audit log</span>
        {events.length > 0 && (
          <span className="text-shell-text-tertiary">({events.length})</span>
        )}
      </button>

      <div
        id="governance-audit-panel"
        className={`space-y-1.5 ${expanded ? "" : "hidden"}`}
      >
        {loading ? (
          <div className="flex items-center gap-2 text-xs text-shell-text-tertiary py-1">
            <RefreshCw size={11} className="animate-spin" aria-hidden />
            Loading…
          </div>
        ) : err ? (
          <p className="text-xs text-red-400" role="alert">{err}</p>
        ) : events.length === 0 ? (
          <p className="text-xs text-shell-text-tertiary py-1">
            No governance events recorded yet.
          </p>
        ) : (
          events.map((ev) => {
            const p = ev.payload;
            const when = new Date(ev.ts * 1000).toLocaleString();
            const label = ACTION_LABELS[p.action] ?? p.action;
            return (
              <div
                key={ev.id}
                className="flex items-start gap-2 px-3 py-2 rounded bg-white/3 border border-white/5 text-[11px]"
                role="listitem"
              >
                <div className="flex-1 min-w-0">
                  <div className="flex items-center gap-1.5 flex-wrap">
                    <span className="font-medium">{p.actor_user_id}</span>
                    <span className="text-shell-text-tertiary">{label}</span>
                    <code
                      className="font-mono text-shell-text-secondary truncate max-w-[180px]"
                      title={p.canonical_id}
                    >
                      {p.canonical_id}
                    </code>
                  </div>
                  <div className="flex items-center gap-1 mt-0.5 text-shell-text-tertiary">
                    <span>{p.before_status}</span>
                    <ArrowRight size={10} aria-label="to" />
                    <span>{p.after_status}</span>
                    <span className="ml-2">{when}</span>
                  </div>
                </div>
              </div>
            );
          })
        )}
      </div>
    </section>
  );
}

/* ------------------------------------------------------------------ */
/*  InviteExternalAgentPicker                                           */
/* ------------------------------------------------------------------ */

/** OS-level (project-less) mint result: the redeemable URL + PIN. */
interface OsInviteResult {
  invite_id: string;
  pin: string;
}

function osInviteUrl(inviteId: string): string {
  return `${window.location.origin}/i/${inviteId}`;
}

function InviteExternalAgentPicker({
  onCancel,
  onPick,
}: {
  onCancel: () => void;
  onPick: (projectId: string) => void;
}) {
  const [projects, setProjects] = useState<import("@/lib/projects").Project[]>([]);
  const [loading, setLoading] = useState(true);
  const [err, setErr] = useState<string | null>(null);
  const [alias, setAlias] = useState("");
  // "" is the default "None" option: available in chat, assign to projects later.
  const [selected, setSelected] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [result, setResult] = useState<OsInviteResult | null>(null);

  useEffect(() => {
    let active = true;
    setLoading(true);
    setErr(null);
    projectsApi
      .list()
      .then((rows) => {
        if (!active) return;
        const list: import("@/lib/projects").Project[] = Array.isArray(rows) ? rows : [];
        setProjects(list);
      })
      .catch((e: unknown) => {
        if (active) setErr(e instanceof Error ? e.message : "Failed to load projects");
      })
      .finally(() => {
        if (active) setLoading(false);
      });
    return () => {
      active = false;
    };
  }, []);

  const copy = (text: string) => {
    navigator.clipboard?.writeText(text).catch(() => {});
  };

  const mintOsInvite = async () => {
    setSubmitting(true);
    setErr(null);
    try {
      const r = await fetch("/api/agents/invites", {
        method: "POST",
        credentials: "include",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          scopes: ["a2a_send", "a2a_receive"],
          approval_mode: "auto",
          check_interval_secs: 1800,
          display_name: alias.trim() || null,
        }),
      });
      if (!r.ok) {
        const text = await r.text().catch(() => r.statusText);
        throw new Error(`${r.status}: ${text}`);
      }
      const data = (await r.json()) as OsInviteResult;
      setResult(data);
    } catch (e) {
      setErr(String(e));
    } finally {
      setSubmitting(false);
    }
  };

  const onSubmit = (e: React.FormEvent) => {
    e.preventDefault();
    if (submitting) return;
    if (selected) {
      // A project was chosen: chain to the existing project-scoped mint UI.
      onPick(selected);
      return;
    }
    void mintOsInvite();
  };

  const instruction = result
    ? `Fetch ${osInviteUrl(result.invite_id)} and redeem with PIN ${result.pin}; follow the returned JSON instructions to join taOS as an external agent.`
    : "";

  return createPortal(
    <div
      role="dialog"
      aria-modal="true"
      aria-label="Invite external agent"
      className="fixed inset-0 z-[10001] bg-black/50 flex items-center justify-center p-4"
    >
      {result ? (
        <section
          className="bg-zinc-900 p-4 rounded shadow w-full max-w-lg space-y-3"
          aria-label="Invite result"
        >
          <h3 className="text-lg font-semibold">Invite external agent</h3>
          <div className="space-y-1">
            <div className="text-xs text-zinc-400">Invite URL</div>
            <div className="flex items-center gap-2">
              <code className="text-sm break-all bg-zinc-800 rounded px-2 py-1 flex-1">
                {osInviteUrl(result.invite_id)}
              </code>
              <button
                type="button"
                onClick={() => copy(osInviteUrl(result.invite_id))}
                className="text-xs px-2 py-1 bg-zinc-800 rounded hover:bg-zinc-700"
                aria-label="Copy invite URL"
              >
                Copy
              </button>
            </div>
          </div>
          <div className="space-y-1">
            <div className="text-xs text-zinc-400">PIN (shown once)</div>
            <div className="flex items-center gap-2">
              <div className="text-4xl font-bold tracking-widest tabular-nums">{result.pin}</div>
              <button
                type="button"
                onClick={() => copy(result.pin)}
                className="text-xs px-2 py-1 bg-zinc-800 rounded hover:bg-zinc-700"
                aria-label="Copy PIN"
              >
                Copy
              </button>
            </div>
          </div>
          <div className="space-y-1">
            <div className="text-xs text-zinc-400">Agent instruction</div>
            <div className="flex items-start gap-2">
              <code className="text-xs break-all bg-zinc-800 rounded px-2 py-1 flex-1">{instruction}</code>
              <button
                type="button"
                onClick={() => copy(instruction)}
                className="text-xs px-2 py-1 bg-zinc-800 rounded hover:bg-zinc-700 whitespace-nowrap"
                aria-label="Copy instruction"
              >
                Copy
              </button>
            </div>
          </div>
          <p className="text-xs text-zinc-500">
            This agent has no project yet. Assign it to projects later from its
            row in the registry.
          </p>
          <button
            type="button"
            onClick={onCancel}
            className="px-3 py-1 text-sm bg-zinc-800 rounded hover:bg-zinc-700"
          >
            Done
          </button>
        </section>
      ) : (
        <form
          onSubmit={onSubmit}
          className="bg-zinc-900 p-4 rounded shadow w-full max-w-md space-y-3"
        >
          <h3 className="text-lg font-semibold">Invite external agent</h3>
          <label className="block text-sm">
            <span className="text-zinc-400">Name / alias</span>
            <input
              type="text"
              value={alias}
              onChange={(e) => setAlias(e.target.value)}
              placeholder="e.g. Scout"
              className="w-full mt-1 px-2 py-1 bg-zinc-800 rounded"
              aria-label="Agent name or alias"
            />
          </label>
          {loading ? (
            <div className="text-xs text-zinc-500">Loading projects…</div>
          ) : (
            <label className="block text-sm">
              <span className="text-zinc-400">Project</span>
              <select
                value={selected}
                onChange={(e) => setSelected(e.target.value)}
                className="w-full mt-1 px-2 py-1 bg-zinc-800 rounded"
                aria-label="Project to invite into"
              >
                <option value="">None — available in chat, assign to projects later</option>
                {projects.map((p) => (
                  <option key={p.id} value={p.id}>
                    {p.name || p.slug}
                  </option>
                ))}
              </select>
            </label>
          )}
          {err && (
            <p role="alert" className="text-red-400 text-xs">{err}</p>
          )}
          <div className="flex justify-end gap-2">
            <button
              type="button"
              onClick={onCancel}
              disabled={submitting}
              className="px-3 py-1 text-sm disabled:opacity-50"
            >
              Cancel
            </button>
            <button
              type="submit"
              disabled={submitting}
              className="px-3 py-1 bg-blue-600 rounded text-sm disabled:opacity-50"
            >
              {selected ? "Continue" : submitting ? "Minting…" : "Mint invite"}
            </button>
          </div>
        </form>
      )}
    </div>,
    document.body,
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
  // Invite / assign dialogs (Feature: invite + assign external agents to projects).
  const [inviteProjectId, setInviteProjectId] = useState<string | null>(null);
  const [invitePickerOpen, setInvitePickerOpen] = useState(false);
  const [assignEntry, setAssignEntry] = useState<RegistryEntry | null>(null);
  // Monotonic counter, only the latest in-flight response is applied.
  const loadSeq = useRef(0);

  /** Load registry. quiet=true skips the loading spinner so polls do not unmount the list. */
  const load = useCallback(async ({ quiet = false }: { quiet?: boolean } = {}) => {
    const seq = ++loadSeq.current;
    if (!quiet) {
      setLoading(true);
      setErr(null);
    }
    try {
      const [statusResp, registryResp] = await Promise.all([
        fetch("/auth/status", { credentials: "include" }),
        fetch("/api/agents/registry", { credentials: "include" }),
      ]);
      if (seq !== loadSeq.current) return; // stale, a newer load fired; discard
      if (statusResp.ok) {
        const s = await statusResp.json();
        const nextAdmin = !!s.user?.is_admin;
        const nextUserId = (s.user?.id as string | undefined) ?? "";
        setIsAdmin((prev) => (prev === nextAdmin ? prev : nextAdmin));
        setCurrentUserId((prev) => (prev === nextUserId ? prev : nextUserId));
      }
      if (registryResp.ok) {
        const data = await registryResp.json();
        const next: RegistryEntry[] = Array.isArray(data) ? data : [];
        // Unchanged poll is a no-op so React keeps the scroll container mounted.
        setEntries((prev) => (registryEntriesEqual(prev, next) ? prev : next));
        if (quiet) setErr((prev) => (prev === null ? prev : null));
      } else if (registryResp.status !== 404) {
        const msg = `Failed to load registry (${registryResp.status})`;
        setErr((prev) => (prev === msg ? prev : msg));
      }
    } catch (e: unknown) {
      if (seq !== loadSeq.current) return;
      const msg = e instanceof Error ? e.message : "Network error";
      setErr((prev) => (prev === msg ? prev : msg));
    } finally {
      if (seq === loadSeq.current && !quiet) setLoading(false);
    }
  }, []);

  // Initial load when panel opens; polling while expanded + visible.
  useEffect(() => {
    if (!expanded) return;

    void load();

    let timer: ReturnType<typeof setInterval> | null = null;

    const startPolling = () => {
      // Quiet polls: no spinner, and setEntries is a no-op when data is unchanged.
      if (timer === null) timer = setInterval(() => void load({ quiet: true }), 5_000);
    };
    const stopPolling = () => {
      if (timer !== null) {
        clearInterval(timer);
        timer = null;
      }
    };
    const onVisibility = () => {
      if (document.hidden) {
        stopPolling();
      } else {
        void load({ quiet: true });
        startPolling();
      }
    };
    // Also refetch when the window regains focus while already visible
    // (covers alt-tab / dock-click without a tab switch).
    const onFocus = () => {
      if (!document.hidden) {
        void load({ quiet: true });
        startPolling();
      }
    };

    if (!document.hidden) startPolling();
    document.addEventListener("visibilitychange", onVisibility);
    window.addEventListener("focus", onFocus);
    return () => {
      stopPolling();
      document.removeEventListener("visibilitychange", onVisibility);
      window.removeEventListener("focus", onFocus);
    };
  }, [expanded, load]);

  async function handleAction(
    canonical_id: string,
    action: "approve" | "reject" | "suspend" | "reactivate" | "revoke",
  ) {
    await registryAction(canonical_id, action);
    await load({ quiet: true });
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
              onAssign={setAssignEntry}
            />
          ))
        )}
        <GovernanceAuditPanel isAdmin={isAdmin} />
      </div>

      {isAdmin && inviteProjectId && (
        <InviteAgentDialog
          projectId={inviteProjectId}
          onClose={() => setInviteProjectId(null)}
        />
      )}
      {isAdmin && invitePickerOpen && (
        <InviteExternalAgentPicker
          onCancel={() => setInvitePickerOpen(false)}
          onPick={(pid) => {
            setInvitePickerOpen(false);
            setInviteProjectId(pid);
          }}
        />
      )}
      {assignEntry && (
        <AssignAgentToProjectDialog
          entry={assignEntry}
          onClose={() => setAssignEntry(null)}
        />
      )}
      {isAdmin && (
        <button
          type="button"
          onClick={() => setInvitePickerOpen(true)}
          className="mt-2 px-3 py-1 text-xs bg-blue-600 rounded hover:bg-blue-500 transition-colors"
          aria-label="Invite external agent"
        >
          Invite external agent
        </button>
      )}
    </section>
  );
}
