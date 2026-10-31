import type { AppManifest, AppRuntime } from "@/registry/app-registry";

export { type AppRuntime };

/**
 * Resolve an app's runtime from its manifest.
 *
 * Priority: an explicit `app.runtime` wins; an id namespaced `service:` is a
 * container app; everything else (every built-in) is taOS-native. Built-in
 * manifests need no edit to opt in -- they default to "taos".
 */
export function appRuntime(app: AppManifest): AppRuntime {
  if (app.runtime) return app.runtime;
  if (app.id.startsWith("service:")) return "container";
  return "taos";
}

/**
 * Resolve the runtime for a taOSmobile compositor window id (see
 * mobile-shell.ts ShellWindow.app_id). Waydroid windows are Android; the
 * handset's own app windows (chrome-*) are taOS-native; anything else
 * (e.g. "firefox") is a Linux app.
 */
export function shellWindowRuntime(appId: string): AppRuntime {
  if (appId.startsWith("waydroid.")) return "android";
  if (appId.startsWith("chrome-")) return "taos";
  return "linux";
}