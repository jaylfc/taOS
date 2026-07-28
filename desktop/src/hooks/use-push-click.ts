import { useEffect } from "react";
import { getApp } from "@/registry/app-registry";
import { sourceToTarget, targetToAction } from "@/lib/server-notifications";

const FALLBACK_SIZE = { w: 900, h: 640 };

type OpenWindow = (
  appId: string,
  defaultSize: { w: number; h: number },
  props?: Record<string, unknown>,
) => string;

export function usePushClickHandler(openWindow: OpenWindow): void {
  useEffect(() => {
    const handler = (e: Event) => {
      const msg = (e as MessageEvent).data;
      if (!msg || msg.type !== "taos-push:click") return;
      const payload = msg.data || {};
      const source = typeof payload.source === "string" ? payload.source : "";
      const route = targetToAction(payload.target);
      const { action, meta } = route.action ? route : sourceToTarget(source);
      if (!action) return;
      const size = getApp(action)?.defaultSize ?? FALLBACK_SIZE;
      const props = meta && Object.keys(meta).length ? meta : undefined;
      openWindow(action, size, props);
    };
    window.addEventListener("message", handler);
    return () => window.removeEventListener("message", handler);
  }, [openWindow]);
}
