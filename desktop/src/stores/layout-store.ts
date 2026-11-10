import { create } from "zustand";
import { withCsrf } from "@/lib/csrf";
import { useNotificationStore } from "@/stores/notification-store";
import { useAuthReadyStore } from "@/stores/auth-ready-store";

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
 *
 * Save semantics: PUTs are serialized so rapid toggles reach the server in
 * order; each PUT is bounded by `SAVE_TIMEOUT_MS`; a failed or timed-out save
 * is reported once per session and leaves an "unsaved" marker so a later
 * sync re-pushes the local choice instead of overwriting it with the server's
 * older value. Saves queued before a logout are dropped, never replayed under
 * a different session.
 */

const FULLSCREEN_LAUNCH_KEY = "taos-layout-fullscreen-launch";
/**
 * Holds the latest local choice ("on"/"off") while it has not been confirmed
 * saved on the server; absent once confirmed or once the server has
 * permanently rejected the save (so syncs can resume).
 */
const UNSAVED_KEY = "taos-layout-fullscreen-launch-unsaved";
const PREF_URL = "/api/preferences/layout";
/** Upper bound on how long LoginScreen waits for the server sync. */
export const LAYOUT_SYNC_WAIT_MS = 1500;
/** Upper bound on a single preference PUT before it is aborted and reported. */
export const SAVE_TIMEOUT_MS = 10_000;

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

/** Serialize a boolean choice the way it is stored. */
function encode(on: boolean): "on" | "off" {
  return on ? "on" : "off";
}

/** Cache the value locally and record it as unsaved, atomically: if the cache
 * write succeeds but the marker write fails (quota), roll the cache back so a
 * reload cannot sync the older server value over an unmarked local choice. */
function persistLocalAndMarkUnsaved(on: boolean): void {
  try {
    localStorage.setItem(FULLSCREEN_LAUNCH_KEY, on ? "on" : "off");
  } catch {
    /* private mode or quota: nothing cached, nothing to mark */
    return;
  }
  try {
    localStorage.setItem(UNSAVED_KEY, encode(on));
  } catch {
    // Cache written but marker failed: roll back the cache so the marker and
    // cache stay in sync and a later sync does not overwrite the local choice.
    try {
      localStorage.removeItem(FULLSCREEN_LAUNCH_KEY);
    } catch {
      /* ignore */
    }
  }
}

/** The unsaved choice, or null when the server copy is authoritative again. */
function unsavedChoice(): boolean | null {
  try {
    const v = localStorage.getItem(UNSAVED_KEY);
    return v === "on" ? true : v === "off" ? false : null;
  } catch {
    return null;
  }
}

/**
 * Clear the unsaved marker, but only if it still refers to `on`: a confirmed
 * save of an older choice must not clear the marker for a newer one that is
 * still queued. `force` clears it regardless (permanent rejection).
 */
function clearUnsaved(on: boolean, force = false): void {
  try {
    if (force || localStorage.getItem(UNSAVED_KEY) === encode(on)) localStorage.removeItem(UNSAVED_KEY);
  } catch {
    /* ignore */
  }
}

/**
 * A 4xx other than timeout/rate-limit means this session is not allowed to
 * write (e.g. a non-admin member gets 403) or the request is malformed;
 * retrying the same save cannot succeed. Everything else is transient.
 */
function isPermanentRejection(status: number): boolean {
  return status >= 400 && status < 500 && status !== 408 && status !== 429;
}

// Bumped on every local change. A server GET that started before a change
// must not overwrite the newer local choice when its response arrives.
let localVersion = 0;
// PUTs are chained so rapid toggles reach the server in order and the last
// choice is the one that stays persisted.
let saveChain: Promise<void> = Promise.resolve();
// Bumped when the authenticated session ends; saves queued under an earlier
// generation are skipped rather than replayed under whoever signs in next.
let sessionGeneration = 0;
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

/** Outcome of one PUT attempt. */
type SaveOutcome = "saved" | "transient" | "permanent";

/** Perform one bounded PUT and classify the outcome. */
async function putOnce(body: Record<string, boolean>): Promise<SaveOutcome> {
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), SAVE_TIMEOUT_MS);
  try {
    const res = await fetch(
      PREF_URL,
      withCsrf({
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body),
        signal: controller.signal,
      }),
    );
    if (!res.ok) {
      reportSaveFailure(`HTTP ${res.status}`);
      return isPermanentRejection(res.status) ? "permanent" : "transient";
    }
    return "saved";
  } catch (err: unknown) {
    if (controller.signal.aborted) reportSaveFailure(`timed out after ${SAVE_TIMEOUT_MS / 1000}s`);
    else reportSaveFailure(err instanceof Error ? err.message : "network error");
    return "transient";
  } finally {
    clearTimeout(timer);
  }
}

/**
 * Append a serialized PUT of `body` to the save chain. The chain always
 * recovers (a failure never blocks later saves), a save enqueued under a
 * session that has since ended is skipped, and the unsaved marker is cleared
 * when the server confirms the latest choice, or when it permanently rejects
 * the write (the local value still applies here, but syncs resume so this
 * browser follows later server changes instead of re-pushing forever).
 */
function pushToServer(on: boolean): Promise<void> {
  const generation = sessionGeneration;
  saveChain = saveChain
    .then(async () => {
      if (generation !== sessionGeneration) return; // session ended while queued
      const outcome = await putOnce({ fullscreen_on_launch: on });
      if (outcome === "saved") clearUnsaved(on);
      else if (outcome === "permanent") clearUnsaved(on, true);
    })
    .catch(() => {
      /* putOnce reports its own failures; keep the chain alive */
    });
  return saveChain;
}

// Drop queued saves when the authenticated session ends (logout). The local
// value and its unsaved marker stay, so the next session's sync re-pushes it.
useAuthReadyStore.subscribe((s, prev) => {
  if (prev.ready && !s.ready) sessionGeneration += 1;
});

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
    persistLocalAndMarkUnsaved(on);
    void pushToServer(on);
  },

  syncFromServer() {
    if (inflightSync) return inflightSync;
    const startedAt = localVersion;
    set({ syncState: "pending" });
    let run: Promise<void> | undefined;
    run = (async () => {
      // Yield once so `inflightSync = run` below is assigned before any
      // synchronous early return reaches `finally` (otherwise the completed
      // promise would be stored and every later sync would short-circuit).
      await Promise.resolve();
      try {
        // An unsaved local choice outranks the server copy: re-push it
        // instead of letting an older server value overwrite it.
        const unsaved = unsavedChoice();
        if (unsaved !== null) {
          void pushToServer(unsaved);
          return;
        }
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
        if (inflightSync === run) inflightSync = null;
      }
    })();
    inflightSync = run;
    return run;
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
  sessionGeneration = 0;
  inflightSync = null;
  saveFailureReported = false;
  useLayoutStore.setState({ fullscreenOnLaunch: loadFullscreenOnLaunch(), syncState: "idle" });
}
