import type { Notification } from "@/stores/notification-store";
import { withCsrf } from "./csrf";

/**
 * Bridge between the persistent backend NotificationStore
 * (GET/POST /api/notifications) and the client-side notification store.
 *
 * The backend serves JSON when the request is NOT an htmx request, so we never
 * send the `hx-request` header. Rows are mapped into the frontend Notification
 * shape with an "srv-" id prefix so they never collide with client "notif-N"
 * ids, and second-precision timestamps are converted to milliseconds.
 */

export interface ServerNotificationRow {
  id: number;
  timestamp: number; // unix seconds
  level: string;
  title: string;
  message: string;
  read: boolean;
  source: string;
  data?: Record<string, unknown> | null;
}

const VALID_LEVELS: ReadonlySet<Notification["level"]> = new Set([
  "info",
  "success",
  "warning",
  "error",
]);

/**
 * A typed deep-link target carried on a notification's JSON `data.target`.
 * `kind` selects a route builder from TARGET_ROUTES; the remaining fields are
 * kind-specific (e.g. a `project_file` carries `project_id` and `path`).
 */
export interface NotificationTarget {
  kind: string;
  project_id?: string;
  path?: string;
  [key: string]: unknown;
}

/**
 * Registry mapping a target `kind` to the app route a click should open. Keyed
 * by kind rather than a hardcoded switch so new kinds (`project_task`,
 * `canvas_element`, ...) are purely additive: one entry each, no change to the
 * click handler. `action` is an app id understood by the process store; `meta`
 * carries the launch props the target app reads.
 */
const TARGET_ROUTES: Record<
  string,
  (t: NotificationTarget) => { action: string; meta: Record<string, string> }
> = {
  project_file: (t) => ({
    action: "projects",
    meta: {
      projectId: String(t.project_id ?? ""),
      tab: "files",
      filePath: String(t.path ?? ""),
    },
  }),
};

/**
 * Resolve a typed notification `target` to an app route via TARGET_ROUTES.
 * Returns an empty object when there is no target or its kind is unknown, so
 * the caller can fall back to the source-keyed switch.
 */
export function targetToAction(
  target: unknown,
): { action?: string; meta?: Record<string, string> } {
  if (!target || typeof target !== "object") return {};
  const kind = (target as { kind?: unknown }).kind;
  if (typeof kind !== "string") return {};
  const build = TARGET_ROUTES[kind];
  if (!build) return {};
  return build(target as NotificationTarget);
}

/**
 * Map a backend event `source` to the app a click on the notification should
 * open. Sources that have no relevant destination return an empty object, which
 * leaves the notification non-navigable. `action` is an app id understood by the
 * process store; `meta` carries an optional launch prop (e.g. a Settings section).
 */
export function sourceToTarget(
  source: string,
): { action?: string; meta?: Record<string, string> } {
  switch (source) {
    case "system.update":
    case "system.lifecycle":
      return { action: "settings", meta: { section: "updates" } };
    case "disk_quota":
      return { action: "settings", meta: { section: "storage" } };
    case "worker.join":
    case "worker.online":
    case "worker.leave":
    case "backend.up":
    case "backend.down":
      return { action: "cluster" };
    case "training.complete":
    case "training.failed":
      return { action: "agents" };
    case "app.installed":
    case "app.failed":
      return { action: "store" };
    case "agent_framework":
      return { action: "agents" };
    case "decisions":
    case "auth_requests":
      return { action: "decisions" };
    default:
      return {};
  }
}

export function mapRow(row: ServerNotificationRow): Notification {
  const level = VALID_LEVELS.has(row.level as Notification["level"])
    ? (row.level as Notification["level"])
    : "info";
  // A typed `data.target` takes precedence over the source-keyed switch, so a
  // row can point at a specific destination (e.g. a project file opens the doc
  // viewer) regardless of its source. Source routing stays as the fallback for
  // rows that only carry a source.
  const targetRoute = targetToAction(row.data?.target);
  const { action, meta } = targetRoute.action
    ? targetRoute
    : sourceToTarget(row.source);
  return {
    id: `srv-${row.id}`,
    source: row.source || "system",
    title: row.title,
    body: row.message,
    level,
    read: row.read,
    timestamp: row.timestamp * 1000,
    ...(action ? { action } : {}),
    ...(meta ? { meta } : {}),
    ...(row.data ? { data: row.data } : {}),
  };
}

/**
 * Fetch the backend notification feed as frontend Notifications. Any fetch or
 * parse failure resolves to an empty array; this never throws to the caller.
 */
export async function fetchServerNotifications(): Promise<Notification[]> {
  try {
    const res = await fetch("/api/notifications", {
      headers: { Accept: "application/json" },
    });
    if (!res.ok) return [];
    const ct = res.headers.get("content-type") ?? "";
    if (!ct.includes("application/json")) return [];
    const data = await res.json();
    if (!Array.isArray(data)) return [];
    return (data as ServerNotificationRow[]).map(mapRow);
  } catch {
    return [];
  }
}

/** Numeric backend id from a prefixed "srv-N" id, or null for other ids. */
function serverId(id: string): number | null {
  if (!id.startsWith("srv-")) return null;
  const n = Number(id.slice(4));
  return Number.isInteger(n) ? n : null;
}

/** Mark a single server-origin notification read on the backend. No-op for client ids. */
export async function markServerRead(id: string): Promise<void> {
  const n = serverId(id);
  if (n == null) return;
  try {
    await fetch(`/api/notifications/${n}/read`, withCsrf({ method: "POST" }));
  } catch {
    // best-effort; the optimistic local update already happened
  }
}

/** Archive a single server-origin notification on the backend. No-op for client ids. */
export async function archiveServerNotification(id: string): Promise<void> {
  const n = serverId(id);
  if (n == null) return;
  try {
    await fetch(`/api/notifications/${n}/archive`, withCsrf({ method: "POST" }));
  } catch {
    // best-effort; the optimistic local update already happened
  }
}

/** Mark all server-origin notifications read on the backend. */
export async function markAllServerRead(): Promise<void> {
  try {
    await fetch("/api/notifications/read-all", withCsrf({ method: "POST" }));
  } catch {
    // best-effort; the optimistic local update already happened
  }
}
