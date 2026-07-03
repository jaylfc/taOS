// desktop/src/apps/SandboxedAppWindow.tsx
import { useEffect, useRef } from "react";
import { ShieldAlert } from "lucide-react";
import { useThemeStore } from "@/stores/theme-store";
import { ALLOWED_TOKENS } from "@/theme/theme-config";
import { defaultProvenanceForTrust, type AppProvenance } from "@/lib/userspace-apps";

interface Props {
  windowId: string;
  appId: string;
  /** "container" apps run their own HTTP backend and are loaded via the
   * server-side proxy instead of the static bundle. Defaults to "web". */
  appType?: "web" | "container";
  trust?: "community" | "first-party";
  /** Where the app's code came from. Falls back to defaultProvenanceForTrust(trust)
   * when omitted, so a caller that only knows the legacy `trust` field still
   * gets a sensible classification. */
  provenance?: AppProvenance;
  /** Capabilities the user has explicitly granted this app -- drives the
   * network-blocked badge and mirrors what the broker actually enforces. */
  grantedCapabilities?: string[];
}

/** Mirrors tinyagentos/userspace/capabilities.py PROVENANCE_CEILINGS -- the
 * default (no-consent) capability ceiling per tier. Only used here to decide
 * what the badge shows; the backend broker is the actual enforcement point. */
const PROVENANCE_CEILINGS: Record<AppProvenance, readonly string[]> = {
  "first-party": ["app.kv", "app.table", "app.files", "app.notify", "app.window",
    "app.net", "app.agent", "app.llm", "app.memory"],
  "ai-generated": ["app.notify", "app.window"],
  "user-uploaded": ["app.notify", "app.window"],
  unknown: [],
};

const PROVENANCE_LABELS: Record<AppProvenance, string> = {
  "first-party": "Built-in",
  "ai-generated": "AI-generated",
  "user-uploaded": "User-uploaded",
  unknown: "Unknown origin",
};

/** The `a.b` namespace of a capability, e.g. `app.kv.get` -> `app.kv`.
 * Mirrors _namespace in tinyagentos/userspace/capabilities.py. */
function namespaceOf(capability: string): string {
  const parts = capability.split(".");
  return parts.length >= 2 ? parts.slice(0, 2).join(".") : capability;
}

/** True if `capability`'s namespace is free for this tier or was explicitly
 * granted -- the same namespace-based "ceiling OR grant" rule the backend's
 * capability_allowed enforces (so a sub-capability like `app.kv.get` matches
 * the `app.kv` ceiling entry, not just an exact string). */
function hasCapability(
  provenance: AppProvenance,
  capability: string,
  granted: readonly string[],
): boolean {
  const ns = namespaceOf(capability);
  return (
    PROVENANCE_CEILINGS[provenance].includes(ns) ||
    granted.includes(ns) ||
    granted.includes(capability)
  );
}

/**
 * The iframe `sandbox` attribute, derived from provenance. Every tier
 * resolves to the same minimal "allow-scripts" today: it is already the
 * tightest value that still lets the app's own script run, and adding tokens
 * like allow-forms/allow-popups/allow-same-origin would LOOSEN the existing
 * restriction for whichever tier got them (allow-same-origin in particular
 * would let a sandboxed app execute on the core origin -- never acceptable).
 * The function exists (rather than a bare constant) so the derivation is a
 * single, provenance-aware, testable place to tighten further per tier later.
 */
function computeSandboxAttr(_provenance: AppProvenance): string {
  return "allow-scripts";
}

/** Small corner badge showing an app's provenance + whether it can reach the
 * network. Skipped for first-party apps (built-in, nothing to warn about). */
function ProvenanceBadge({
  provenance,
  networkAllowed,
}: {
  provenance: AppProvenance;
  networkAllowed: boolean;
}) {
  if (provenance === "first-party") return null;
  return (
    <div
      data-testid="provenance-badge"
      data-provenance={provenance}
      className="pointer-events-none absolute right-2 top-2 z-10 flex items-center gap-1 rounded-full bg-black/60 px-2 py-1 text-[10px] font-semibold text-white/90 backdrop-blur-sm"
    >
      <ShieldAlert size={11} />
      <span>{PROVENANCE_LABELS[provenance]}</span>
      <span className="text-white/60">
        {networkAllowed ? "· Network allowed" : "· Network blocked"}
      </span>
    </div>
  );
}

interface BrokerRequest {
  taosApp: string;
  id: number;
  capability: string;
  args?: Record<string, unknown>;
}

/** Read all ALLOWED_TOKENS CSS variables off :root and return as a plain object. */
function readThemeTokens(): Record<string, string> {
  if (typeof document === "undefined") return {};
  const style = getComputedStyle(document.documentElement);
  const tokens: Record<string, string> = {};
  for (const token of ALLOWED_TOKENS) {
    const value = style.getPropertyValue(token).trim();
    if (value) tokens[token] = value;
  }
  return tokens;
}

export function SandboxedAppWindow({
  appId,
  appType = "web",
  trust = "community",
  provenance,
  grantedCapabilities = [],
}: Props) {
  const iframeRef = useRef<HTMLIFrameElement>(null);
  const iframeSrc =
    appType === "container"
      ? `/api/userspace-apps/${encodeURIComponent(appId)}/proxy/`
      : `/api/userspace-apps/${encodeURIComponent(appId)}/bundle/index.html?app=${encodeURIComponent(appId)}`;
  const isFirstParty = trust === "first-party";
  const resolvedProvenance: AppProvenance = provenance ?? defaultProvenanceForTrust(trust);
  const networkAllowed = hasCapability(resolvedProvenance, "app.net", grantedCapabilities);
  // Subscribe to scheme changes (which fire on any applyThemeConfig call) so we
  // can push updated tokens when the theme changes. The selector is minimal to
  // avoid re-renders on unrelated store updates.
  const scheme = useThemeStore((s) => s.scheme);

  // Post theme tokens into the iframe for first-party apps.
  useEffect(() => {
    if (!isFirstParty) return;
    const iframe = iframeRef.current;
    if (!iframe?.contentWindow) return;
    iframe.contentWindow.postMessage({ taosTheme: readThemeTokens() }, "*");
  }, [isFirstParty, scheme]);

  // Also post tokens once when the iframe loads (the scheme effect may fire
  // before the frame is ready on the first render).
  function handleLoad() {
    if (!isFirstParty) return;
    const iframe = iframeRef.current;
    if (!iframe?.contentWindow) return;
    iframe.contentWindow.postMessage({ taosTheme: readThemeTokens() }, "*");
  }

  useEffect(() => {
    async function onMessage(e: MessageEvent) {
      const iframe = iframeRef.current;
      // Only handle messages from THIS app's sandboxed iframe.
      if (!iframe || e.source !== iframe.contentWindow) return;
      const msg = e.data as BrokerRequest;
      if (!msg || msg.taosApp !== appId || typeof msg.id !== "number" || !msg.capability) return;
      // Validate args: must be a plain object (not an array, null, or primitive).
      // Non-conforming values are coerced to {} rather than forwarded as-is into
      // backend capability handling.
      const rawArgs = msg.args;
      const safeArgs: Record<string, unknown> =
        rawArgs !== null && typeof rawArgs === "object" && !Array.isArray(rawArgs)
          ? rawArgs
          : {};
      let result: Record<string, unknown>;
      try {
        const res = await fetch(`/api/userspace-apps/${encodeURIComponent(appId)}/broker`, {
          method: "POST",
          // Carry the taos_session cookie so the broker authenticates the
          // caller -- matches every other SPA API call, and stays correct
          // under the Vite dev proxy (SPA :5173 -> API :6969).
          credentials: "include",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ capability: msg.capability, args: safeArgs }),
        });
        result = res.ok ? await res.json() : { error: `broker_${res.status}` };
      } catch {
        result = { error: "broker_unreachable" };
      }
      iframe.contentWindow?.postMessage({ taosAppReply: msg.id, ...result }, "*");
    }
    window.addEventListener("message", onMessage);
    return () => window.removeEventListener("message", onMessage);
  }, [appId]);

  return (
    <div className="relative h-full w-full">
      <ProvenanceBadge provenance={resolvedProvenance} networkAllowed={networkAllowed} />
      <iframe
        ref={iframeRef}
        title={appId}
        src={iframeSrc}
        sandbox={computeSandboxAttr(resolvedProvenance)}
        data-provenance={resolvedProvenance}
        className="w-full h-full border-0 bg-white"
        onLoad={handleLoad}
      />
    </div>
  );
}
