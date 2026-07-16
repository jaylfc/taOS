import { useEffect, useMemo, useState } from "react";
import { createPortal } from "react-dom";

const SCOPE_PRESETS: { value: string; label: string; defaultOn: boolean; disabled?: boolean; hint?: string }[] = [
  { value: "project_tasks", label: "project_tasks", defaultOn: true, disabled: true, hint: "required for project invites" },
  { value: "canvas_read", label: "canvas_read", defaultOn: true },
  { value: "canvas_write", label: "canvas_write", defaultOn: true },
];

const INTERVAL_PRESETS: { label: string; secs: number }[] = [
  { label: "5m", secs: 300 },
  { label: "15m", secs: 900 },
  { label: "30m", secs: 1800 },
  { label: "1h", secs: 3600 },
  { label: "6h", secs: 21600 },
];

interface InviteRecord {
  invite_id: string;
  scopes: string[];
  status: string;
  expires_ts: number;
  redeemed_by: string | null;
}

interface MintResult {
  invite_id: string;
  pin: string;
  expires_ts: number;
  scopes: string[];
  approval_mode: "auto" | "manual";
  check_interval_secs: number;
}

function inviteUrl(inviteId: string): string {
  return `${window.location.origin}/i/${inviteId}`;
}

function formatCountdown(remainingSecs: number): string {
  if (remainingSecs <= 0) return "expired";
  const m = Math.floor(remainingSecs / 60);
  const s = remainingSecs % 60;
  if (m >= 1) return `${m}m ${s}s`;
  return `${s}s`;
}

function statusChipLabel(status: string, redeemedBy: string | null): string {
  if (status === "redeemed" && redeemedBy) return `redeemed by ${redeemedBy}`;
  return status;
}

function statusChipClass(status: string): string {
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

export function InviteAgentDialog({
  projectId,
  onClose,
  onMinted,
}: {
  projectId: string;
  onClose: () => void;
  onMinted?: () => void;
}) {
  const [scopes, setScopes] = useState<Record<string, boolean>>(() => {
    const init: Record<string, boolean> = {};
    for (const s of SCOPE_PRESETS) init[s.value] = s.defaultOn;
    return init;
  });
  const [isLead, setIsLead] = useState(false);
  const [manualApproval, setManualApproval] = useState(false);
  const [intervalSecs, setIntervalSecs] = useState(1800);
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [result, setResult] = useState<MintResult | null>(null);
  const [pending, setPending] = useState<InviteRecord[]>([]);
  const [now, setNow] = useState(() => Math.floor(Date.now() / 1000));

  const refreshPending = () => {
    fetch(`/api/projects/${projectId}/invites`, { credentials: "include" })
      .then((r) => (r.ok ? r.json() : []))
      .then((rows) => setPending(Array.isArray(rows) ? (rows as InviteRecord[]) : []))
      .catch(() => setPending([]));
  };

  useEffect(() => {
    refreshPending();
  }, [projectId]);

  useEffect(() => {
    const t = setInterval(() => setNow(Math.floor(Date.now() / 1000)), 1000);
    return () => clearInterval(t);
  }, []);

  const buildScopes = (): string[] => {
    const out = new Set<string>();
    out.add("project_tasks");
    for (const s of SCOPE_PRESETS) {
      if (s.disabled) continue;
      if (scopes[s.value]) out.add(s.value);
    }
    if (isLead) out.add("lead");
    return [...out];
  };

  const onSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (submitting) return;
    setSubmitting(true);
    setError(null);
    try {
      const body = {
        scopes: buildScopes(),
        approval_mode: manualApproval ? ("manual" as const) : ("auto" as const),
        check_interval_secs: intervalSecs,
      };
      const r = await fetch(`/api/projects/${projectId}/invites`, {
        method: "POST",
        credentials: "include",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body),
      });
      if (!r.ok) {
        const text = await r.text().catch(() => r.statusText);
        throw new Error(`${r.status}: ${text}`);
      }
      const data = (await r.json()) as MintResult;
      setResult(data);
      refreshPending();
      onMinted?.();
    } catch (err) {
      setError(String(err));
    } finally {
      setSubmitting(false);
    }
  };

  const onRevoke = async (inviteId: string) => {
    try {
      const r = await fetch(`/api/projects/${projectId}/invites/${inviteId}`, {
        method: "DELETE",
        credentials: "include",
      });
      if (!r.ok && r.status !== 204) throw new Error(`${r.status}`);
      refreshPending();
    } catch (err) {
      setError(String(err));
    }
  };

  const copy = (text: string) => {
    navigator.clipboard?.writeText(text).catch(() => {});
  };

  const instruction = useMemo(() => {
    if (!result) return "";
    return `Fetch ${inviteUrl(result.invite_id)} and redeem with PIN ${result.pin}; follow the returned JSON instructions to join the taOS project.`;
  }, [result]);

  return createPortal(
    <div
      role="dialog"
      aria-modal="true"
      aria-label="Invite external agent"
      className="fixed inset-0 bg-black/50 flex items-center justify-center p-4"
    >
      <form
        onSubmit={onSubmit}
        className="bg-zinc-900 p-4 rounded shadow w-full max-w-lg space-y-3 max-h-[90vh] overflow-y-auto"
      >
        <h3 className="text-lg font-semibold">Invite external agent</h3>

        {result ? (
          <section className="space-y-3" aria-label="Invite result">
            <div className="space-y-1">
              <div className="text-xs text-zinc-400">Invite URL</div>
              <div className="flex items-center gap-2">
                <code className="text-sm break-all bg-zinc-800 rounded px-2 py-1 flex-1">
                  {inviteUrl(result.invite_id)}
                </code>
                <button
                  type="button"
                  onClick={() => copy(inviteUrl(result.invite_id))}
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
            <button
              type="button"
              onClick={() => {
                setResult(null);
                onClose();
              }}
              className="px-3 py-1 text-sm bg-zinc-800 rounded hover:bg-zinc-700"
            >
              Done
            </button>
          </section>
        ) : (
          <>
            <fieldset className="border border-zinc-800 p-2 rounded">
              <legend className="text-xs px-1">Scopes</legend>
              {SCOPE_PRESETS.map((s) => (
                <label key={s.value} className="flex items-center gap-2 text-sm py-0.5">
                  <input
                    type="checkbox"
                    checked={scopes[s.value]}
                    disabled={s.disabled}
                    onChange={(e) => setScopes((prev) => ({ ...prev, [s.value]: e.target.checked }))}
                    aria-label={`Scope ${s.label}`}
                  />
                  <span>{s.label}</span>
                  {s.disabled && s.hint && (
                    <span className="text-[10px] text-zinc-500">({s.hint})</span>
                  )}
                </label>
              ))}
              <label className="flex items-center gap-2 text-sm py-0.5 mt-1">
                <input
                  type="checkbox"
                  checked={isLead}
                  onChange={(e) => setIsLead(e.target.checked)}
                  aria-label="Make this agent the project lead"
                />
                <span>Make this agent the project lead</span>
              </label>
            </fieldset>

            <fieldset className="border border-zinc-800 p-2 rounded">
              <legend className="text-xs px-1">Check-in interval</legend>
              <div className="flex flex-wrap gap-2 mb-2">
                {INTERVAL_PRESETS.map((p) => (
                  <button
                    key={p.label}
                    type="button"
                    onClick={() => setIntervalSecs(p.secs)}
                    className={
                      intervalSecs === p.secs
                        ? "text-xs px-2 py-1 rounded bg-blue-600 text-white"
                        : "text-xs px-2 py-1 rounded bg-zinc-800 hover:bg-zinc-700"
                    }
                    aria-label={`Set interval ${p.label}`}
                  >
                    {p.label}
                  </button>
                ))}
              </div>
              <label className="block text-sm">
                Seconds
                <input
                  type="number"
                  min={0}
                  value={intervalSecs}
                  onChange={(e) => setIntervalSecs(Number(e.target.value) || 0)}
                  className="w-full mt-1 px-2 py-1 bg-zinc-800 rounded"
                  aria-label="Check-in interval in seconds"
                />
              </label>
            </fieldset>

            <label className="flex items-center gap-2 text-sm">
              <input
                type="checkbox"
                checked={manualApproval}
                onChange={(e) => setManualApproval(e.target.checked)}
                aria-label="Require manual approval"
              />
              <span>Require manual approval</span>
            </label>

            {error && <div role="alert" className="text-red-400 text-xs">{error}</div>}
            <div className="flex justify-end gap-2">
              <button
                type="button"
                onClick={onClose}
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
                {submitting ? "Minting…" : "Mint invite"}
              </button>
            </div>
          </>
        )}

        <section className="border-t border-zinc-800 pt-3" aria-label="Pending invites">
          <h4 className="text-sm font-medium text-zinc-300 mb-2">Pending invites</h4>
          {pending.length === 0 ? (
            <p className="text-xs text-zinc-500">No pending invites.</p>
          ) : (
            <ul className="space-y-1">
              {pending.map((inv) => {
                const remaining = Math.floor(inv.expires_ts) - now;
                return (
                  <li
                    key={inv.invite_id}
                    className="flex flex-col gap-1 text-xs bg-zinc-900/60 border border-zinc-800 rounded px-2 py-2 md:flex-row md:items-center md:justify-between md:gap-2"
                  >
                    <div className="min-w-0">
                      <span className="font-mono">{inv.invite_id}</span>
                      <span className="text-zinc-500 ml-2">{inv.scopes.join(", ")}</span>
                    </div>
                    <div className="flex items-center gap-2">
                      <span className="tabular-nums text-zinc-400" title="Time until expiry">
                        {formatCountdown(remaining)}
                      </span>
                      <span
                        className={`px-1.5 py-0.5 rounded text-[10px] uppercase tracking-wide ${statusChipClass(inv.status)}`}
                      >
                        {statusChipLabel(inv.status, inv.redeemed_by)}
                      </span>
                      <button
                        type="button"
                        onClick={() => onRevoke(inv.invite_id)}
                        className="text-xs text-red-400 hover:underline"
                        aria-label={`Revoke ${inv.invite_id}`}
                      >
                        Revoke
                      </button>
                    </div>
                  </li>
                );
              })}
            </ul>
          )}
        </section>
      </form>
    </div>,
    document.body,
  );
}
