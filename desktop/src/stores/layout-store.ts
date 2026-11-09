import { create } from "zustand";
import { withCsrf } from "@/lib/csrf";
import { useNotificationStore } from "@/stores/notification-store";

/**
 * Layout preferences.
 *
 * `fullscreenOnLaunch` (default on, matching upstream taOS): when the user
 * clicks "Launch taOS", request browser fullscreen and show the "Return to
 * fullscreen" pill whenever the browser leaves it. Turn it off to keep taOS
 * as a normal browser tab.
 *
 * Persistence: localStorage first so the choice applies on the very first
 * paint, mirrored to the server preference namespace "layout" so it follows
 * the user to other browsers. `App` calls `syncFromServer()` once auth is
 * ready, and `LoginScreen` waits (briefly) for that sync before deciding
 * whether to request fullscreen, so a fresh browser honours the saved choice.
 */

const FULLSCREEN_LAUNCH_KEY = "taos-layout-fullscreen-launch";
const PREF_URL = "/api/preferences/layout";
/** Upper bound on how long LoginScreen waits for the server sync. */
export const LAYOUT_SYNC_WAIT_MS = 1500;

/** Read the locally cached value; absent or unreadable storage means the default (on). */
function loadFullscreenOnLaunch(): boolean {
  try {
    return localStorage.getItem(FULLSCREEN_LAUNCH_KEY) !== "off";
  } catch {
    return true;
  }
}

/** Cache the value locally so the next page load applies it before any API call. */
function persistLocal(on: boolean): void {
  try {
    localStorage.setItem(FULLSCREEN_LAUNCH_KEY, on ? "on" : "off");
  } catch {
    /* private mode or quota: the in-memory value still applies */
  }
}

// Bumped on every local change. A server GET that started before a change
// must not overwrite the newer local choice when its response arrives.
let localVersion = 0;
// PUTs are chained so rapid toggles reach the server in order and the last
// choice is the one that stays persisted.
let saveChain: Promise<void> = Promise.resolve();
// In-flight GET, shared so concurrent callers (and the launch wait) reuse it.
let inflightSync: Promise<void> | null = null;
// Tell the user once per session if the server copy cannot be written.
let saveFailureReported = false;

/** Report a failed server save once; the local (per-browser) value still applies. */
function reportSaveFailure(detail: string): void {
  if (saveFailureReported) return;
  saveFailureReported = true;
  useNotificationStore.getState().addNotification({
    source: "system",
    title: "Preference saved for this browser only",
    body: `Could not store the layout preference on the server (${detail}). It still applies here.`,
    level: "warning",
  });
}

/** Append a serialized PUT of `body` to the save chain and surface failures. */
function pushToServer(body: Record<string, boolean>): Promise<void> {
  saveChain = saveChain
    .then(async () => {
      const res = await fetch(
        PREF_URL,
        withCsrf({
          method: "PUT",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(body),
        }),
      );
      if (!res.ok) reportSaveFailure(`HTTP ${res.status}`);
    })
    .catch((err: unknown) => {
      reportSaveFailure(err instanceof Error ? err.message : "network error");
    });
  return saveChain;
}

/** Sync lifecycle: idle (never asked), pending (GET in flight), done (answered or failed). */
export type LayoutSyncState = "idle" | "pending" | "done";

interface LayoutStore {
  fullscreenOnLaunch: boolean;
  syncState: LayoutSyncState;
  /** Set the preference: applies immediately, caches locally, mirrors to the server in order. */
  setFullscreenOnLaunch: (on: boolean) => void;
  /** Pull the server-side preference once per call (best effort, deduplicated while in flight). */
  syncFromServer: () => Promise<void>;
}

export const useLayoutStore = create<LayoutStore>((set, get) => ({
  fullscreenOnLaunch: loadFullscreenOnLaunch(),
  syncState: "idle",

  setFullscreenOnLaunch(on) {
    localVersion += 1;
    set({ fullscreenOnLaunch: on });
    persistLocal(on);
    void pushToServer({ fullscreen_on_launch: on });
  },

  syncFromServer() {
    if (inflightSync) return inflightSync;
    const startedAt = localVersion;
    set({ syncState: "pending" });
    inflightSync = (async () => {
      try {
        const res = await fetch(PREF_URL, { headers: { Accept: "application/json" } });
        if (!res.ok) return;
        const data = (await res.json()) as { fullscreen_on_launch?: unknown } | null;
        // A local change while the request was in flight wins over the server copy.
        if (localVersion !== startedAt) return;
        if (data && typeof data.fullscreen_on_launch === "boolean" && data.fullscreen_on_launch !== get().fullscreenOnLaunch) {
          set({ fullscreenOnLaunch: data.fullscreen_on_launch });
          persistLocal(data.fullscreen_on_launch);
        }
      } catch {
        /* offline or unauthenticated: keep the local value */
      } finally {
        set({ syncState: "done" });
        inflightSync = null;
      }
    })();
    return inflightSync;
  },
}));

/**
 * Resolve once the server sync is no longer pending, or after `timeoutMs`.
 * Used by LoginScreen so a fresh browser gets the saved choice without ever
 * blocking launch on a slow or unreachable API.
 */
export function waitForLayoutSync(timeoutMs = LAYOUT_SYNC_WAIT_MS): Promise<void> {
  if (useLayoutStore.getState().syncState !== "pending") return Promise.resolve();
  return new Promise((resolve) => {
    const timer = setTimeout(finish, timeoutMs);
    const unsubscribe = useLayoutStore.subscribe((s) => {
      if (s.syncState !== "pending") finish();
    });
    function finish() {
      clearTimeout(timer);
      unsubscribe();
      resolve();
    }
  });
}

/** Test hook: reset module-level sync/save bookkeeping between cases. */
export function __resetLayoutStoreForTests(): void {
  localVersion = 0;
  saveChain = Promise.resolve();
  inflightSync = null;
  saveFailureReported = false;
  useLayoutStore.setState({ fullscreenOnLaunch: loadFullscreenOnLaunch(), syncState: "idle" });
}
