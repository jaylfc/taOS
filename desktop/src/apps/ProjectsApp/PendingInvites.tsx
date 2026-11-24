import { useEffect, useState } from "react";

/* ------------------------------------------------------------------ */
/*  Pending invites: shared type, fetch/revoke helpers and list UI.    */
/*                                                                     */
/*  Extracted from InviteAgentDialog (#2067): pending invites used to  */
/*  be visible only inside the mint dialog. The same list logic now    */
/*  backs the project Members screen and the Agents app registry, so   */
/*  an invite can be found (and cancelled) where people actually look. */
/* ------------------------------------------------------------------ */

export interface PendingInvite {
  invite_id: string;
  scopes: string[];
  status: string;
  expires_ts: number;
  redeemed_by?: string | null;
  kind?: string;
  created_ts?: number;
  pin_required?: boolean;
  display_name?: string | null;
  contact_id?: string | null;
  /** Owning project (aggregate view only); null for OS-level invites. */
  project_id?: string | null;
  project_name?: string | null;
}

export function formatCountdown(remainingSecs: number): string {
  if (remainingSecs <= 0) return "expired";
  const m = Math.floor(remainingSecs / 60);
  const s = remainingSecs % 60;
  if (m >= 1) return `${m}m ${s}s`;
  return `${s}s`;
}

export function statusChipLabel(status: string, redeemedBy: string | null): string {
  if (status === "redeemed" && redeemedBy) return `redeemed by ${redeemedBy}`;
  return status;
}

export function statusChipClass(status: string): string {
  switch (status) {
    case "pending":
      return "bg-sky-500/15 text-sky-300 border border-sky-500/25";
    case "redeemed":
      return "bg-emerald-500/15 text-emerald-300 border border-emerald-500/25";
    case "revoked":
      return "bg-red-500/15 text-red-300 border border-red-500/25";
    case "expired":
      return "bg-zinc-700/40 text-zinc-400 border border-zinc-600/40";
    default:
      return "bg-zinc-700/40 text-zinc-300 border border-zinc-600/40";
  }
}

export function kindChipLabel(kind: string | undefined): string {
  switch (kind) {
    case "collab":
      return "collab";
    case "cloud_agent":
      return "cloud agent";
    default:
      return "agent";
  }
}

export function kindChipClass(kind: string | undefined): string {
  switch (kind) {
    case "collab":
      return "bg-violet-500/15 text-violet-300 border border-violet-500/25";
    case "cloud_agent":
      return "bg-teal-500/15 text-teal-300 border border-teal-500/25";
    default:
      return "bg-sky-500/15 text-sky-300 border border-sky-500/25";
  }
}

/** Ticking clock (epoch seconds) for expiry countdowns. */
export function useNowSeconds(intervalMs = 1000): number {
  const [now, setNow] = useState(() => Math.floor(Date.now() / 1000));
  useEffect(() => {
    const t = setInterval(() => setNow(Math.floor(Date.now() / 1000)), intervalMs);
    return () => clearInterval(t);
  }, [intervalMs]);
  return now;
}

export async function fetchProjectInvites(projectId: string): Promise<PendingInvite[]> {
  try {
    const r = await fetch(`/api/projects/${projectId}/invites`, { credentials: "include" });
    if (!r.ok) return [];
    const rows = await r.json();
    return Array.isArray(rows) ? (rows as PendingInvite[]) : [];
  } catch {
    return [];
  }
}

export async function revokeProjectInvite(projectId: string, inviteId: string): Promise<boolean> {
  try {
    const r = await fetch(`/api/projects/${projectId}/invites/${inviteId}`, {
      method: "DELETE",
      credentials: "include",
    });
    return r.ok || r.status === 204;
  } catch {
    return false;
  }
}

/**
 * Cross-project aggregate of pending agent invites for the Agents app
 * (GET /api/agents/invites/pending, admin-gated). Returns null when the
 * caller cannot see it (e.g. 403 for non-admins) so the surface can hide
 * instead of erroring.
 */
export async function fetchPendingAgentInvites(): Promise<PendingInvite[] | null> {
  try {
    const r = await fetch("/api/agents/invites/pending", { credentials: "include" });
    if (!r.ok) return null;
    const rows = await r.json();
    return Array.isArray(rows) ? (rows as PendingInvite[]) : null;
  } catch {
    return null;
  }
}

/** Cancel an invite from the aggregate view: the DELETE route depends on
 *  whether the invite is project-scoped or OS-level. */
export async function revokePendingAgentInvite(invite: PendingInvite): Promise<boolean> {
  if (invite.project_id) {
    return revokeProjectInvite(invite.project_id, invite.invite_id);
  }
  try {
    const r = await fetch(`/api/agents/invites/${invite.invite_id}`, {
      method: "DELETE",
      credentials: "include",
    });
    return r.ok || r.status === 204;
  } catch {
    return false;
  }
}

export function PendingInvitesList({
  invites,
  onRevoke,
  onOpenProject,
  emptyText = "No pending invites",
}: {
  invites: PendingInvite[];
  onRevoke: (invite: PendingInvite) => void;
  onOpenProject?: (invite: PendingInvite) => void;
  emptyText?: string;
}) {
  const now = useNowSeconds();
  if (invites.length === 0) {
    return <p className="text-xs text-zinc-500">{emptyText}</p>;
  }
  return (
    <ul className="space-y-1" aria-label="Pending invites">
      {invites.map((inv) => {
        const remaining = Math.floor(inv.expires_ts) - now;
        return (
          <li
            key={inv.invite_id}
            className="flex flex-col gap-1 text-xs bg-zinc-900/60 border border-zinc-800 rounded px-2 py-2 md:flex-row md:items-center md:justify-between md:gap-2"
          >
            <div className="min-w-0 flex flex-wrap items-center gap-1.5">
              <span className="font-mono">{inv.invite_id}</span>
              {inv.display_name && <span className="text-zinc-200">{inv.display_name}</span>}
              {inv.contact_id && <span className="text-zinc-400">{inv.contact_id}</span>}
              <span
                className={`px-1.5 py-0.5 rounded text-[10px] uppercase tracking-wide ${kindChipClass(inv.kind)}`}
              >
                {kindChipLabel(inv.kind)}
              </span>
              {inv.pin_required && (
                <span className="px-1.5 py-0.5 rounded text-[10px] uppercase tracking-wide bg-amber-500/15 text-amber-300 border border-amber-500/25">
                  PIN required
                </span>
              )}
              {inv.scopes.map((scope) => (
                <span
                  key={scope}
                  className="px-1.5 py-0.5 rounded text-[10px] bg-zinc-800 text-zinc-300 border border-zinc-700/60"
                >
                  {scope}
                </span>
              ))}
            </div>
            <div className="flex flex-wrap items-center gap-2">
              {typeof inv.created_ts === "number" && (
                <span
                  className="text-zinc-500"
                  title={new Date(inv.created_ts * 1000).toISOString()}
                >
                  Created {new Date(inv.created_ts * 1000).toLocaleString()}
                </span>
              )}
              <span className="tabular-nums text-zinc-400" title="Time until expiry">
                {formatCountdown(remaining)}
              </span>
              <span
                className={`px-1.5 py-0.5 rounded text-[10px] uppercase tracking-wide ${statusChipClass(inv.status)}`}
              >
                {statusChipLabel(inv.status, inv.redeemed_by ?? null)}
              </span>
              {inv.project_id ? (
                onOpenProject ? (
                  <button
                    type="button"
                    onClick={() => onOpenProject(inv)}
                    className="text-xs text-sky-300 hover:underline"
                    aria-label={`Open project ${inv.project_name || inv.project_id}`}
                  >
                    {inv.project_name || inv.project_id}
                  </button>
                ) : (
                  <span className="text-zinc-400">{inv.project_name || inv.project_id}</span>
                )
              ) : inv.project_id === null ? (
                <span className="text-zinc-500">OS-level</span>
              ) : null}
              <button
                type="button"
                onClick={() => onRevoke(inv)}
                className="text-xs text-red-400 hover:underline"
                aria-label={`Cancel invite ${inv.invite_id}`}
              >
                Cancel
              </button>
            </div>
          </li>
        );
      })}
    </ul>
  );
}
