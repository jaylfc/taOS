import { create } from "zustand";

/**
 * Layout preferences.
 *
 * fullscreenOnLaunch (default on, matching upstream taOS): when the user
 * clicks "Launch taOS", request browser fullscreen and show the "Return to
 * fullscreen" pill whenever the browser leaves it. Turn it off to keep taOS
 * as a normal browser tab.
 *
 * Persistence: localStorage first so the choice applies on the very first
 * paint (before any API call), mirrored to the server preference namespace
 * "layout" so it follows the user to other browsers once signed in.
 */

const FULLSCREEN_LAUNCH_KEY = "taos-layout-fullscreen-launch";
const PREF_URL = "/api/preferences/layout";

function loadFullscreenOnLaunch(): boolean {
  try {
    return localStorage.getItem(FULLSCREEN_LAUNCH_KEY) !== "off";
  } catch {
    return true;
  }
}

function persistLocal(on: boolean): void {
  try {
    localStorage.setItem(FULLSCREEN_LAUNCH_KEY, on ? "on" : "off");
  } catch {
    /* private mode or quota: in-memory value still applies */
  }
}

interface LayoutStore {
  fullscreenOnLaunch: boolean;
  setFullscreenOnLaunch: (on: boolean) => void;
  /** Pull the server-side preference once (best effort). */
  syncFromServer: () => Promise<void>;
}

export const useLayoutStore = create<LayoutStore>((set, get) => ({
  fullscreenOnLaunch: loadFullscreenOnLaunch(),

  setFullscreenOnLaunch(on) {
    set({ fullscreenOnLaunch: on });
    persistLocal(on);
    // Best effort: the server copy is a convenience, never a requirement.
    fetch(PREF_URL, {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ fullscreen_on_launch: on }),
    }).catch(() => {});
  },

  async syncFromServer() {
    try {
      const res = await fetch(PREF_URL, { headers: { Accept: "application/json" } });
      if (!res.ok) return;
      const data = (await res.json()) as { fullscreen_on_launch?: unknown } | null;
      if (data && typeof data.fullscreen_on_launch === "boolean" && data.fullscreen_on_launch !== get().fullscreenOnLaunch) {
        set({ fullscreenOnLaunch: data.fullscreen_on_launch });
        persistLocal(data.fullscreen_on_launch);
      }
    } catch {
      /* offline or unauthenticated: keep the local value */
    }
  },
}));
