/**
 * Ship browser-side errors and logs to the controller (#106).
 *
 * A PWA has no devtools console the user (or we) can read, so a front-end crash
 * is otherwise invisible. reportClientLog POSTs to /api/client-logs; it is
 * strictly best-effort and never throws or blocks. installGlobalErrorReporting
 * wires window.onerror + unhandledrejection so uncaught failures are captured
 * too. The server bounds storage (ring buffer) and rate-limits per user; a small
 * client-side dedupe keeps a render loop from hammering the endpoint.
 */
import { withCsrf } from "./csrf";

export type ClientLogLevel = "fatal" | "error" | "warn" | "info" | "debug";

const MAX_MESSAGE = 4000;
const MAX_STACK = 16000;
const DEDUPE_MS = 3000;
const recent = new Map<string, number>();

export function reportClientLog(
  level: ClientLogLevel,
  message: string,
  extra?: { source?: string; stack?: string; url?: string },
): void {
  try {
    const msg = String(message ?? "").slice(0, MAX_MESSAGE);
    if (!msg) return;
    const source = extra?.source ?? "";
    const key = `${level}:${source}:${msg}`;
    const now = Date.now();
    const last = recent.get(key);
    if (last !== undefined && now - last < DEDUPE_MS) return;
    if (recent.size > 100) recent.clear();
    recent.set(key, now);

    const body = JSON.stringify({
      level,
      message: msg,
      source,
      url: extra?.url ?? (typeof location !== "undefined" ? location.pathname : ""),
      stack: (extra?.stack ?? "").slice(0, MAX_STACK),
    });
    const init = withCsrf({
      method: "POST",
      credentials: "include",
      headers: { "Content-Type": "application/json" },
      body,
    });
    void fetch("/api/client-logs", init).catch(() => {});
  } catch {
    // Logging must never break the app.
  }
}

let installed = false;

export function installGlobalErrorReporting(): void {
  if (installed || typeof window === "undefined") return;
  installed = true;
  window.addEventListener("error", (e) => {
    reportClientLog("error", e.message || "window.onerror", {
      source: e.filename || "window.error",
      stack: e.error?.stack,
    });
  });
  window.addEventListener("unhandledrejection", (e) => {
    const reason = (e as PromiseRejectionEvent).reason;
    const message = reason?.message ?? String(reason ?? "unhandledrejection");
    reportClientLog("error", message, { source: "unhandledrejection", stack: reason?.stack });
  });
}
