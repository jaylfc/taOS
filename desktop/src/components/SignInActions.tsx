import { useState } from "react";
import { ExternalLink, KeyRound } from "lucide-react";
import { withCsrf } from "@/lib/csrf";

/**
 * Sign-in card actions for the sign-in broker (tsk-diqs7d).
 *
 * An agent that needs a Claude subscription posts the sign-in URL that
 * `claude setup-token` prints; the broker raises an `auth_broker` notification.
 * This card lets the user open that URL in the system browser (Firefox on
 * taOSmobile) and paste the code it shows back here. The code goes to the
 * broker, never to the agent directly, and only a user session may post it.
 *
 * The server only accepts allowlisted sign-in hosts, and this file checks the
 * same allowlist again before rendering an Open button: a card the user taps
 * must never open a site the agent chose.
 */

/** Must match ALLOWED_SIGNIN_HOSTS in tinyagentos/auth_broker.py. */
const ALLOWED_SIGNIN_HOSTS = new Set(["claude.ai", "console.anthropic.com"]);

function allowedSignInUrl(raw: string): string | null {
  let u: URL;
  try {
    u = new URL(raw);
  } catch {
    return null;
  }
  if (u.protocol !== "https:" || u.username || u.password) return null;
  if (u.port !== "" && u.port !== "443") return null;
  return ALLOWED_SIGNIN_HOSTS.has(u.hostname.toLowerCase()) ? u.hostname.toLowerCase() : null;
}

export function signInPayload(
  data: Record<string, unknown> | undefined,
): { requestId: string; url: string; host: string; agent: string; prompt: string } | null {
  if (!data) return null;
  const requestId = data.request_id;
  const url = data.url;
  if (typeof requestId !== "string" || typeof url !== "string") return null;
  const host = allowedSignInUrl(url);
  if (!host) return null;
  const agent = typeof data.agent === "string" ? data.agent : "An agent";
  const prompt = typeof data.prompt === "string" ? data.prompt : "";
  return { requestId, url, host, agent, prompt };
}

export function SignInActions({
  requestId,
  url,
  host,
  agent,
  onResolved,
}: {
  requestId: string;
  url: string;
  host: string;
  agent: string;
  onResolved?: () => void;
}) {
  const [code, setCode] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function sendCode() {
    const trimmed = code.trim();
    if (!trimmed) return;
    setBusy(true);
    setError(null);
    try {
      const res = await fetch(
        `/api/auth-broker/requests/${encodeURIComponent(requestId)}/code`,
        withCsrf({
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ code: trimmed }),
          credentials: "include",
        }),
      );
      if (!res.ok) {
        const data = await res.json().catch(() => ({}));
        setError((data as { detail?: string }).detail ?? `Request failed (${res.status})`);
        setBusy(false);
        return;
      }
      setCode("");
      onResolved?.();
    } catch (e) {
      setError(e instanceof Error ? e.message : "Network error");
      setBusy(false);
    }
  }

  return (
    <div className="mt-2 space-y-2">
      <p className="text-[11px] text-shell-text-secondary">
        <span className="font-medium text-shell-text">{agent}</span> needs you to sign in at{" "}
        <span className="font-medium text-shell-text">{host}</span>, then paste the code it shows.
      </p>
      <button
        type="button"
        onClick={() => window.open(url, "_blank", "noopener,noreferrer")}
        className="flex items-center gap-1 px-2.5 py-1 rounded-md text-xs font-medium bg-accent/20 text-accent hover:bg-accent/30 transition-colors"
        aria-label={`Open ${host} sign-in for ${agent}`}
      >
        <ExternalLink size={12} />
        Open
      </button>
      <form
        className="flex items-center gap-1.5"
        onSubmit={(e) => {
          e.preventDefault();
          void sendCode();
        }}
      >
        <input
          type="text"
          value={code}
          onChange={(e) => setCode(e.target.value)}
          autoComplete="off"
          spellCheck={false}
          placeholder="Paste code"
          aria-label={`Sign-in code for ${agent}`}
          className="flex-1 min-w-0 px-2 py-1 rounded-md text-xs bg-white/5 border border-white/10 text-shell-text placeholder:text-shell-text-tertiary"
        />
        <button
          type="submit"
          disabled={busy || !code.trim()}
          className="flex items-center gap-1 px-2.5 py-1 rounded-md text-xs font-medium bg-emerald-500/20 text-emerald-400 hover:bg-emerald-500/30 transition-colors disabled:opacity-50"
          aria-label={`Send code to ${agent}`}
        >
          <KeyRound size={12} />
          Send code
        </button>
      </form>
      {error && (
        <p role="alert" className="text-[11px] text-red-400">
          {error}
        </p>
      )}
    </div>
  );
}
