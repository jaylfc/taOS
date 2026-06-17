// desktop/src/apps/SandboxedAppWindow.tsx
import { useEffect, useRef } from "react";
import { useThemeStore } from "@/stores/theme-store";
import { ALLOWED_TOKENS } from "@/theme/theme-config";

interface Props {
  windowId: string;
  appId: string;
  trust?: "community" | "first-party";
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

export function SandboxedAppWindow({ appId, trust = "community" }: Props) {
  const iframeRef = useRef<HTMLIFrameElement>(null);
  const isFirstParty = trust === "first-party";
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
    <iframe
      ref={iframeRef}
      title={appId}
      src={`/api/userspace-apps/${encodeURIComponent(appId)}/bundle/index.html?app=${encodeURIComponent(appId)}`}
      sandbox="allow-scripts"
      className="w-full h-full border-0 bg-white"
      onLoad={handleLoad}
    />
  );
}
