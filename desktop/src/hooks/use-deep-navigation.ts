import { useEffect } from "react";
import { resolveApp } from "@/registry/app-registry";

type OpenWindow = (
  appId: string,
  defaultSize: { w: number; h: number },
  props?: Record<string, unknown>,
) => string;

/**
 * Deep-navigation API for the desktop.
 *
 * Opens apps from a `?app=` URL param on load (handy for tests, screenshots,
 * and shareable links) and from a `taos:open-app` CustomEvent at runtime, so
 * the taOS agent can drive the desktop for the user without a reload.
 *
 * A token may be an app id, exact name, or alias ("activity" -> dashboard);
 * pass several comma-separated. Optional props deep-link into an app (e.g. a
 * Messages channel) via `?appProps=<urlencoded-json>` or the event detail.
 * Singleton apps are focused and re-receive props rather than duplicated
 * (handled by the process store's openWindow).
 */
export function useDeepNavigation(openWindow: OpenWindow): void {
  useEffect(() => {
    const openByToken = (token: string, props?: Record<string, unknown>) => {
      const app = resolveApp(token);
      if (app) openWindow(app.id, app.defaultSize, props);
    };

    const params = new URLSearchParams(window.location.search);
    const requested = params.get("app");
    if (requested) {
      let props: Record<string, unknown> | undefined;
      const rawProps = params.get("appProps");
      if (rawProps) {
        try {
          props = JSON.parse(rawProps);
        } catch {
          /* malformed props: open the app without them */
        }
      }
      for (const token of requested.split(",")) {
        if (token.trim()) openByToken(token, props);
      }
    }

    const onOpenApp = (e: Event) => {
      const detail = (e as CustomEvent).detail as
        | { app?: string; props?: Record<string, unknown> }
        | undefined;
      if (detail?.app) openByToken(detail.app, detail.props);
    };
    window.addEventListener("taos:open-app", onOpenApp);
    return () => window.removeEventListener("taos:open-app", onOpenApp);
  }, [openWindow]);
}
