/**
 * Modal popped on `capability-needed` event from an agent.
 *
 * The user picks a grant scope:
 *   - This page only         → host_pattern = full URL, expires in 1h
 *   - This site (this session) → host_pattern = hostname, expires in 24h
 *   - This site (always)     → host_pattern = hostname, expires_at = null
 *   - Deny                   → no grant; modal closes
 *
 * Triggered by a window event:
 *   window.dispatchEvent(new CustomEvent("taos-browser:capability-prompt", {
 *     detail: { profileId, agentId, agentName?, permission, host, fullUrl }
 *   }));
 *
 * Mounted at top of BrowserApp (or globally) so it catches the event regardless
 * of which window/tab triggered it.
 */
import { useEffect, useState } from "react";
import { grantCapability } from "@/lib/browser-capability-api";

export interface CapabilityPromptDetail {
  profileId: string;
  agentId: string;
  agentName?: string;
  permission: string;
  host: string;
  fullUrl: string;
}

export function CapabilityPromptModal() {
  const [detail, setDetail] = useState<CapabilityPromptDetail | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [pending, setPending] = useState(false);

  useEffect(() => {
    function handler(e: Event) {
      const ce = e as CustomEvent<CapabilityPromptDetail>;
      if (!ce.detail) return;
      setDetail(ce.detail);
      setError(null);
    }
    window.addEventListener("taos-browser:capability-prompt", handler);
    return () => window.removeEventListener("taos-browser:capability-prompt", handler);
  }, []);

  // Esc closes (treats as Deny without grant)
  useEffect(() => {
    if (!detail) return;
    function onKey(e: KeyboardEvent) {
      if (e.key === "Escape") setDetail(null);
    }
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [detail]);

  if (!detail) return null;

  async function applyGrant(scope: "page" | "session" | "always") {
    if (!detail) return;
    setPending(true);
    setError(null);
    try {
      let hostPattern: string;
      let expiresAt: string | null;
      if (scope === "page") {
        hostPattern = detail.fullUrl;
        expiresAt = isoOffset(60 * 60 * 1000); // 1h
      } else if (scope === "session") {
        hostPattern = detail.host;
        expiresAt = isoOffset(24 * 60 * 60 * 1000); // 24h
      } else {
        hostPattern = detail.host;
        expiresAt = null;
      }
      const result = await grantCapability(
        detail.profileId,
        detail.agentId,
        hostPattern,
        detail.permission,
        expiresAt,
      );
      if (result && "error" in result) {
        setError(result.error);
        return;
      }
      if (!result) {
        setError("Network error — please retry");
        return;
      }
      setDetail(null);
    } finally {
      setPending(false);
    }
  }

  function deny() { setDetail(null); }

  const label = detail.agentName ?? detail.agentId;

  return (
    <div
      role="dialog"
      aria-modal="true"
      aria-labelledby="capability-prompt-title"
      className="fixed inset-0 z-[80] flex items-center justify-center bg-black/40 p-4"
      onClick={(e) => { if (e.target === e.currentTarget) deny(); }}
    >
      <div className="bg-shell-surface border border-shell-border-subtle rounded-md max-w-md w-full p-4 shadow-2xl">
        <h2 id="capability-prompt-title" className="text-sm font-medium text-shell-text mb-1">
          {label} is asking to {prettyPermission(detail.permission)} on {detail.host}.
        </h2>
        <p className="text-xs text-shell-text-secondary mb-4">
          You can grant this permission for the current page, this site for the
          current session, or always. You can revoke it later in Settings.
        </p>
        {error && (
          <div role="alert" className="text-xs text-red-400 bg-red-500/10 border border-red-500/40 rounded px-2 py-1 mb-3">
            {error}
          </div>
        )}
        <div className="flex flex-col gap-2">
          <button
            type="button"
            disabled={pending}
            onClick={() => applyGrant("page")}
            className="text-left text-xs px-3 py-2 rounded border border-shell-border-subtle hover:bg-shell-hover disabled:opacity-50"
          >
            <strong>This page only</strong> · expires in 1 hour
          </button>
          <button
            type="button"
            disabled={pending}
            onClick={() => applyGrant("session")}
            className="text-left text-xs px-3 py-2 rounded border border-shell-border-subtle hover:bg-shell-hover disabled:opacity-50"
          >
            <strong>This site (this session)</strong> · expires in 24 hours
          </button>
          <button
            type="button"
            disabled={pending}
            onClick={() => applyGrant("always")}
            className="text-left text-xs px-3 py-2 rounded border border-shell-border-subtle hover:bg-shell-hover disabled:opacity-50"
          >
            <strong>This site (always)</strong> · no expiry; revoke in Settings
          </button>
          <button
            type="button"
            disabled={pending}
            onClick={deny}
            className="text-left text-xs px-3 py-2 rounded border border-shell-border-subtle hover:bg-shell-hover disabled:opacity-50"
          >
            <strong>Deny</strong> · agent can ask again later
          </button>
        </div>
      </div>
    </div>
  );
}

function isoOffset(deltaMs: number): string {
  return new Date(Date.now() + deltaMs).toISOString();
}

function prettyPermission(p: string): string {
  if (p === "drive") return "drive (click, type, scroll, focus)";
  if (p === "navigate") return "navigate";
  if (p === "see_cookies") return "read raw cookies";
  return p;
}
