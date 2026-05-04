import { create } from "zustand";
import { persist } from "zustand/middleware";

export const SEARCH_ENGINES = {
  duckduckgo: "https://duckduckgo.com/?q=",
  google: "https://www.google.com/search?q=",
  bing: "https://www.bing.com/search?q=",
} as const;

export type SearchEngine = keyof typeof SEARCH_ENGINES;

/** Returns the full search URL for a given engine and query. */
export function searchUrlFor(engine: SearchEngine, query: string): string {
  return `${SEARCH_ENGINES[engine]}${encodeURIComponent(query)}`;
}

interface BrowserSettingsState {
  discardTimeoutMs: number;
  maxLiveTabs: number;
  searchEngine: SearchEngine;
  setDiscardTimeoutMs: (ms: number) => void;
  setMaxLiveTabs: (n: number) => void;
  setSearchEngine: (e: string) => void;
}

export const useBrowserSettingsStore = create<BrowserSettingsState>()(
  persist(
    (set) => ({
      discardTimeoutMs: 10 * 60 * 1000,
      maxLiveTabs: 12,
      searchEngine: "duckduckgo",

      setDiscardTimeoutMs(ms) {
        const clamped = Math.max(60_000, Math.min(60 * 60 * 1000, ms));
        set({ discardTimeoutMs: clamped });
      },

      setMaxLiveTabs(n) {
        const clamped = Math.max(1, Math.min(50, n));
        set({ maxLiveTabs: clamped });
      },

      setSearchEngine(e) {
        if (e in SEARCH_ENGINES) set({ searchEngine: e as SearchEngine });
      },
    }),
    { name: "taos-browser-settings" },
  ),
);
