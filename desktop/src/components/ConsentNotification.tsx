/**
 * ConsentNotification — non-dismissable consent overlay for external-agent access requests.
 *
 * Polls /api/agents/auth-requests for pending entries. When found, shows a
 * blocking overlay the user MUST answer before the desktop is usable. Supports
 * per-scope toggles per the Trust & Comms Layer spec (Phase 2).
 *
 * Only admins can approve/deny (the backend enforces this too).
 */

import { useState, useEffect, useCallback } from "react";
import {
  ShieldQuestion,
  CheckCircle,
  XCircle,
  Clock,
  Tag,
  Wrench,
} from "lucide-react";

/* ------------------------------------------------------------------ */
/*  Types                                                               */
/* ------------------------------------------------------------------ */

interface AuthRequest {
  id: string;
  identity_claim: string;
  framework: string;
  requested_scopes: string[];
  requested_skills: string[];
  reason: string;
  duration_secs: number | null;
  project_id: string | null;
  status: "pending" | "accepted" | "refused";
  created_ts: string;
}

/* ------------------------------------------------------------------ */
/*  Helpers                                                             */
/* ------------------------------------------------------------------ */

function durationLabel(secs: number | null): string | null {
  if (!secs) return null;
  if (secs < 3600) return `${Math.round(secs / 60)} min`;
  if (secs < 86400) return `${Math.round(secs / 3600)} h`;
  return `${Math.round(secs / 86400)} d`;
}

const SCOPE_DESCRIPTIONS: Record<string, string> = {
  memory_read: "Read from your memory store",
  memory_write: "Write to your memory store",
  a2a_send: "Send messages on the A2A bus",
  a2a_receive: "Receive messages on the A2A bus",
  files_read: "Read your files",
  files_write: "Write to your files",
  tools_execute: "Execute tools on your device",
};

/* ------------------------------------------------------------------ */
/*  ConsentModal                                                        */
/* ------------------------------------------------------------------ */

function ConsentModal({
  request,
  onDecide,
}: {
  request: AuthRequest;
  onDecide: (requestId: string, approved: boolean, scopes: string[]) => Promise<void>;
}) {
  const [toggledScopes, setToggledScopes] = useState<Set<string>>(
    () => new Set(request.requested_scopes),
  );
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);

  function toggleScope(scope: string) {
    setToggledScopes((prev) => {
      const next = new Set(prev);
      if (next.has(scope)) next.delete(scope);
      else next.add(scope);
      return next;
    });
  }

  async function decide(approved: boolean) {
    setBusy(true);
    setErr(null);
    try {
      await onDecide(request.id, approved, approved ? Array.from(toggledScopes) : []);
    } catch (e: unknown) {
      setErr(e instanceof Error ? e.message : String(e));
      setBusy(false);
    }
  }

  const dur = durationLabel(request.duration_secs);

  return (
    <div
      className="fixed inset-0 z-[9999] flex items-center justify-center bg-black/70 backdrop-blur-sm"
      role="dialog"
      aria-modal="true"
      aria-labelledby="consent-modal-title"
    >
      <div
        className="w-full max-w-md mx-4 rounded-2xl border border-white/10 bg-shell-bg shadow-2xl overflow-hidden"
        style={{ boxShadow: "0 32px 64px rgba(0,0,0,0.6), 0 0 0 1px rgba(255,255,255,0.07)" }}
      >
        {/* Header */}
        <div className="px-6 pt-6 pb-4 border-b border-white/5">
          <div className="flex items-start gap-3">
            <div className="mt-0.5 p-2 rounded-xl bg-amber-500/15 shrink-0">
              <ShieldQuestion size={20} className="text-amber-400" aria-hidden />
            </div>
            <div>
              <h2
                id="consent-modal-title"
                className="text-base font-semibold leading-tight"
              >
                Access request
              </h2>
              <p className="text-sm text-shell-text-secondary mt-0.5">
                An external agent is requesting access to your taOS resources.
              </p>
            </div>
          </div>
        </div>

        {/* Agent info */}
        <div className="px-6 py-4 space-y-3 border-b border-white/5">
          <div className="flex items-center gap-2">
            <Tag size={13} className="text-shell-text-tertiary shrink-0" aria-hidden />
            <div>
              <span className="text-xs text-shell-text-tertiary">Identity</span>
              <p className="text-sm font-medium break-all">{request.identity_claim}</p>
            </div>
          </div>

          <div className="flex items-center gap-4 flex-wrap">
            <div>
              <span className="text-xs text-shell-text-tertiary">Framework</span>
              <p className="text-sm">{request.framework}</p>
            </div>
            {dur && (
              <div className="flex items-center gap-1.5">
                <Clock size={12} className="text-shell-text-tertiary" aria-hidden />
                <div>
                  <span className="text-xs text-shell-text-tertiary">Duration</span>
                  <p className="text-sm">{dur}</p>
                </div>
              </div>
            )}
            {request.project_id && (
              <div>
                <span className="text-xs text-shell-text-tertiary">Project</span>
                <p className="text-sm font-mono text-xs">{request.project_id}</p>
              </div>
            )}
          </div>

          {request.reason && (
            <div>
              <span className="text-xs text-shell-text-tertiary">Reason</span>
              <p className="text-sm italic text-shell-text-secondary">
                "{request.reason}"
              </p>
            </div>
          )}
        </div>

        {/* Scopes */}
        {request.requested_scopes.length > 0 && (
          <div className="px-6 py-4 border-b border-white/5">
            <p className="text-xs font-medium text-shell-text-tertiary uppercase tracking-wider mb-3">
              Requested permissions
            </p>
            <ul className="space-y-2" aria-label="Requested permission scopes">
              {request.requested_scopes.map((scope) => (
                <li key={scope} className="flex items-center gap-3">
                  <button
                    role="checkbox"
                    aria-checked={toggledScopes.has(scope)}
                    onClick={() => toggleScope(scope)}
                    disabled={busy}
                    className={`w-10 h-6 rounded-full transition-colors flex items-center shrink-0 ${
                      toggledScopes.has(scope)
                        ? "bg-emerald-600 justify-end"
                        : "bg-white/10 justify-start"
                    } px-1`}
                    aria-label={`${toggledScopes.has(scope) ? "Revoke" : "Grant"} ${scope} permission`}
                  >
                    <span className="w-4 h-4 rounded-full bg-white shadow-sm block" />
                  </button>
                  <div className="flex-1 min-w-0">
                    <span className="text-sm font-mono text-shell-text-secondary">{scope}</span>
                    {SCOPE_DESCRIPTIONS[scope] && (
                      <p className="text-[11px] text-shell-text-tertiary">
                        {SCOPE_DESCRIPTIONS[scope]}
                      </p>
                    )}
                  </div>
                </li>
              ))}
            </ul>
          </div>
        )}

        {/* Skills */}
        {request.requested_skills.length > 0 && (
          <div className="px-6 py-3 border-b border-white/5">
            <div className="flex items-center gap-2 mb-2">
              <Wrench size={12} className="text-shell-text-tertiary" aria-hidden />
              <span className="text-xs font-medium text-shell-text-tertiary uppercase tracking-wider">
                Requested skills
              </span>
            </div>
            <ul className="flex flex-wrap gap-1.5" aria-label="Requested skills">
              {request.requested_skills.map((skill) => (
                <li
                  key={skill}
                  className="px-2 py-0.5 rounded bg-white/5 border border-white/10 text-xs font-mono"
                >
                  {skill}
                </li>
              ))}
            </ul>
          </div>
        )}

        {/* Actions */}
        <div className="px-6 py-4">
          {err && (
            <p className="text-xs text-red-400 mb-3" role="alert">{err}</p>
          )}
          <div className="flex gap-3">
            <button
              onClick={() => decide(false)}
              disabled={busy}
              aria-label="Deny access request"
              className="flex-1 flex items-center justify-center gap-2 px-4 py-2.5 rounded-xl text-sm font-medium bg-white/5 hover:bg-red-500/15 hover:text-red-300 border border-white/10 hover:border-red-500/30 transition-colors disabled:opacity-50"
            >
              <XCircle size={15} aria-hidden />
              Deny
            </button>
            <button
              onClick={() => decide(true)}
              disabled={busy || toggledScopes.size === 0}
              aria-label="Approve access request with selected scopes"
              className="flex-1 flex items-center justify-center gap-2 px-4 py-2.5 rounded-xl text-sm font-medium bg-emerald-600 hover:bg-emerald-500 text-white transition-colors disabled:opacity-50 disabled:cursor-not-allowed"
            >
              <CheckCircle size={15} aria-hidden />
              {busy ? "Approving…" : `Allow${toggledScopes.size < request.requested_scopes.length ? ` (${toggledScopes.size} scope${toggledScopes.size !== 1 ? "s" : ""})` : ""}`}
            </button>
          </div>
          <p className="text-[11px] text-shell-text-tertiary text-center mt-2">
            You must accept or deny before continuing
          </p>
        </div>
      </div>
    </div>
  );
}

/* ------------------------------------------------------------------ */
/*  ConsentNotification (global poller + renderer)                     */
/* ------------------------------------------------------------------ */

const POLL_INTERVAL_MS = 5_000;

export function ConsentNotification() {
  const [pendingRequests, setPendingRequests] = useState<AuthRequest[]>([]);
  const [isAdmin, setIsAdmin] = useState(false);

  const fetchPending = useCallback(async () => {
    try {
      const [statusResp, reqResp] = await Promise.all([
        fetch("/auth/status", { credentials: "include" }),
        fetch("/api/agents/auth-requests?status=pending", { credentials: "include" }),
      ]);
      if (statusResp.ok) {
        const s = await statusResp.json();
        setIsAdmin(!!s.user?.is_admin);
      }
      if (reqResp.ok) {
        const data = await reqResp.json();
        const requests = (data.requests ?? data ?? []) as AuthRequest[];
        setPendingRequests(requests.filter((r) => r.status === "pending"));
      }
    } catch { /* silent — user may not be logged in yet */ }
  }, []);

  useEffect(() => {
    fetchPending();
    const interval = setInterval(fetchPending, POLL_INTERVAL_MS);
    return () => clearInterval(interval);
  }, [fetchPending]);

  async function handleDecide(requestId: string, approved: boolean, scopes: string[]) {
    const method = "POST";
    const url = approved
      ? `/api/agents/auth-requests/${encodeURIComponent(requestId)}/approve`
      : `/api/agents/auth-requests/${encodeURIComponent(requestId)}/deny`;
    const body = approved ? JSON.stringify({ granted_scopes: scopes }) : undefined;

    const resp = await fetch(url, {
      method,
      headers: body ? { "Content-Type": "application/json" } : {},
      body,
      credentials: "include",
    });
    if (!resp.ok) {
      const err = await resp.json().catch(() => ({}));
      throw new Error((err as { detail?: string }).detail ?? `HTTP ${resp.status}`);
    }
    // Remove the decided request from the list and re-poll.
    setPendingRequests((prev) => prev.filter((r) => r.id !== requestId));
    await fetchPending();
  }

  if (!isAdmin || pendingRequests.length === 0) return null;

  // Show the oldest pending request first (FIFO).
  const current = pendingRequests[0]!;
  return <ConsentModal request={current} onDecide={handleDecide} />;
}
