import type { AppManifest } from "@/registry/app-registry";

/**
 * Event name emitted when the installed userspace-app list changes (install,
 * uninstall, or enable/disable). Mirrors APP_INSTALLED from app-event-bus.
 */
export const USERSPACE_APPS_CHANGED = "taos:userspace-apps-changed";

export interface UserspaceAppRow {
  app_id: string;
  name: string;
  icon: string;
  app_type: "web" | "container";
  version: string;
  enabled: number;
  permissions_requested: string[];
  permissions_granted: string[];
  trust?: "community" | "first-party";
}

export function toAppManifest(row: UserspaceAppRow): AppManifest {
  const trust = row.trust ?? "community";
  return {
    // Registry id is namespaced (mirrors "service:") so a community app cannot
    // shadow a built-in app id. The broker/bundle still use the raw app_id.
    id: `userspace:${row.app_id}`,
    name: row.name,
    icon: "layout-grid",
    category: "userspace",
    component: () =>
      import("@/apps/SandboxedAppWindow").then((m) => ({
        default: (props: { windowId: string }) =>
          m.SandboxedAppWindow({ ...props, appId: row.app_id, trust }),
      })),
    defaultSize: { w: 900, h: 600 },
    minSize: { w: 360, h: 280 },
    singleton: true,
    pinned: false,
    launchpadOrder: 100,
  };
}

export interface InstallResult {
  app_id: string;
  permissions_requested: string[];
  needs_consent: boolean;
  new_permissions: string[];
}

export async function installUserspaceApp(file: File): Promise<InstallResult> {
  const form = new FormData();
  form.append("package", file);
  const res = await fetch("/api/userspace-apps/install", {
    method: "POST",
    credentials: "include",
    body: form,
  });
  if (!res.ok) {
    let detail = `install failed (${res.status})`;
    try {
      const body = await res.json();
      if (body?.error) detail = body.error;
    } catch {
      // non-JSON error body; keep the status-based message
    }
    throw new Error(detail);
  }
  return (await res.json()) as InstallResult;
}

export async function grantUserspacePermissions(appId: string, granted: string[]): Promise<void> {
  const res = await fetch(`/api/userspace-apps/${encodeURIComponent(appId)}/permissions`, {
    method: "POST",
    credentials: "include",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ granted }),
  });
  if (!res.ok) throw new Error(`granting permissions failed (${res.status})`);
}

export async function fetchUserspaceApps(): Promise<AppManifest[]> {
  let rows: UserspaceAppRow[];
  try {
    const res = await fetch("/api/userspace-apps");
    if (!res.ok) return [];
    rows = (await res.json()) as UserspaceAppRow[];
  } catch {
    return [];
  }
  return rows.filter((r) => r.enabled).map(toAppManifest);
}
