// desktop/src/apps/SandboxedAppWindow.tsx
import { useEffect, useRef } from "react";

interface Props {
  windowId: string;
  appId: string;
}

interface BrokerRequest {
  taosApp: string;
  id: number;
  capability: string;
  args?: Record<string, unknown>;
}

export function SandboxedAppWindow({ appId }: Props) {
  const iframeRef = useRef<HTMLIFrameElement>(null);

  useEffect(() => {
    async function onMessage(e: MessageEvent) {
      const iframe = iframeRef.current;
      // Only handle messages from THIS app's sandboxed iframe.
      if (!iframe || e.source !== iframe.contentWindow) return;
      const msg = e.data as BrokerRequest;
      if (!msg || msg.taosApp !== appId || typeof msg.id !== "number" || !msg.capability) return;
      let result: Record<string, unknown>;
      try {
        const res = await fetch(`/api/userspace-apps/${encodeURIComponent(appId)}/broker`, {
          method: "POST",
          // Carry the taos_session cookie so the broker authenticates the
          // caller — matches every other SPA API call, and stays correct
          // under the Vite dev proxy (SPA :5173 → API :6969).
          credentials: "include",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ capability: msg.capability, args: msg.args || {} }),
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
    />
  );
}
