import { useState } from "react";
import { CheckCircle, XCircle } from "lucide-react";

/**
 * Inline Allow / Deny actions for an external-agent access request.
 *
 * Rendered in the compact consent surfaces — the bell notification and the
 * non-blocking toast (mirroring AgentPausedActions) and reused in the Decisions
 * app. The granted scope set is exactly the requested scopes; per-scope control
 * lives in the full request record / Decisions app, not these compact surfaces.
 */
export function ConsentActions({
  requestId,
  scopes,
  onResolved,
}: {
  requestId: string;
  scopes: string[];
  onResolved?: () => void;
}) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function decide(approved: boolean) {
    setBusy(true);
    setError(null);
    const url = approved
      ? `/api/agents/auth-requests/${encodeURIComponent(requestId)}/approve`
      : `/api/agents/auth-requests/${encodeURIComponent(requestId)}/deny`;
    const body = approved ? JSON.stringify({ granted_scopes: scopes }) : undefined;
    try {
      const res = await fetch(url, {
        method: "POST",
        headers: body ? { "Content-Type": "application/json" } : {},
        body,
        credentials: "include",
      });
      if (!res.ok) {
        const data = await res.json().catch(() => ({}));
        setError((data as { detail?: string }).detail ?? `Request failed (${res.status})`);
        setBusy(false);
        return;
      }
      onResolved?.();
    } catch (e) {
      setError(e instanceof Error ? e.message : "Network error");
      setBusy(false);
    }
  }

  return (
    <div className="mt-2" role="group" aria-label="Consent actions">
      <div className="flex flex-wrap gap-1.5">
        <button
          type="button"
          onClick={(e) => { e.stopPropagation(); decide(true); }}
          disabled={busy}
          className="flex items-center gap-1 px-2 py-1 rounded-md text-[11px] font-medium bg-emerald-500/15 hover:bg-emerald-500/25 text-emerald-400 border border-emerald-500/20 transition-colors disabled:opacity-50 disabled:cursor-not-allowed"
        >
          <CheckCircle size={11} />
          Allow
        </button>
        <button
          type="button"
          onClick={(e) => { e.stopPropagation(); decide(false); }}
          disabled={busy}
          className="flex items-center gap-1 px-2 py-1 rounded-md text-[11px] font-medium bg-white/5 hover:bg-red-500/15 hover:text-red-300 text-shell-text-secondary border border-white/10 transition-colors disabled:opacity-50 disabled:cursor-not-allowed"
        >
          <XCircle size={11} />
          Deny
        </button>
      </div>
      {error && (
        <p role="alert" className="mt-1.5 text-[11px] text-red-300">
          {error}
        </p>
      )}
    </div>
  );
}

/** Pull the consent payload (request id + requested scopes) out of a
 *  notification's `data` field. Returns null when the shape is missing. */
export function consentPayload(
  data: Record<string, unknown> | undefined,
): { requestId: string; scopes: string[] } | null {
  if (!data) return null;
  const requestId = data.request_id;
  if (typeof requestId !== "string") return null;
  const raw = data.requested_scopes;
  const scopes = Array.isArray(raw) ? raw.filter((s): s is string => typeof s === "string") : [];
  return { requestId, scopes };
}
