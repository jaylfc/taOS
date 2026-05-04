/**
 * BrowserApp v2 — SettingsPanel.
 *
 * Popover panel for browser-specific settings:
 *  - Discard timeout (slider 1–60 minutes)
 *  - Hard cap of live tabs (number input 1–50)
 *  - Default search engine (dropdown)
 *
 * Settings are persisted via useBrowserSettingsStore (localStorage).
 */
import { useEffect, useRef } from "react";
import { X } from "lucide-react";
import {
  useBrowserSettingsStore,
  SEARCH_ENGINES,
  type SearchEngine,
} from "@/stores/browser-settings-store";

interface SettingsPanelProps {
  onClose: () => void;
}

export function SettingsPanel({ onClose }: SettingsPanelProps) {
  const discardTimeoutMs = useBrowserSettingsStore((s) => s.discardTimeoutMs);
  const maxLiveTabs = useBrowserSettingsStore((s) => s.maxLiveTabs);
  const searchEngine = useBrowserSettingsStore((s) => s.searchEngine);
  const setDiscardTimeoutMs = useBrowserSettingsStore((s) => s.setDiscardTimeoutMs);
  const setMaxLiveTabs = useBrowserSettingsStore((s) => s.setMaxLiveTabs);
  const setSearchEngine = useBrowserSettingsStore((s) => s.setSearchEngine);
  const ref = useRef<HTMLDivElement | null>(null);

  const timeoutMinutes = Math.round(discardTimeoutMs / 60_000);

  // Click-outside dismiss
  useEffect(() => {
    const handler = (e: MouseEvent) => {
      if (!ref.current?.contains(e.target as Node)) onClose();
    };
    const id = setTimeout(() => window.addEventListener("mousedown", handler), 0);
    return () => {
      clearTimeout(id);
      window.removeEventListener("mousedown", handler);
    };
  }, [onClose]);

  // Escape key dismiss
  useEffect(() => {
    const handler = (e: KeyboardEvent) => {
      if (e.key === "Escape") onClose();
    };
    window.addEventListener("keydown", handler);
    return () => window.removeEventListener("keydown", handler);
  }, [onClose]);

  return (
    <div
      ref={ref}
      role="dialog"
      aria-label="Browser settings"
      className="absolute right-0 top-full mt-1 z-[60] w-72 rounded-lg border border-shell-border-subtle bg-shell-surface shadow-xl p-4 flex flex-col gap-4"
    >
      {/* Header */}
      <div className="flex items-center justify-between">
        <span className="text-sm font-medium text-shell-text">Browser Settings</span>
        <button
          type="button"
          aria-label="Close settings"
          onClick={onClose}
          className="p-1 rounded hover:bg-shell-hover text-shell-text-secondary"
        >
          <X size={14} />
        </button>
      </div>

      {/* Discard timeout slider */}
      <div className="flex flex-col gap-1.5">
        <label htmlFor="discard-timeout-slider" className="text-xs text-shell-text-secondary">
          Tab discard timeout
        </label>
        <div className="flex items-center gap-2">
          <input
            id="discard-timeout-slider"
            type="range"
            min={1}
            max={60}
            step={1}
            value={timeoutMinutes}
            aria-label="Discard timeout"
            aria-valuemin={1}
            aria-valuemax={60}
            aria-valuenow={timeoutMinutes}
            onChange={(e) => setDiscardTimeoutMs(Number(e.target.value) * 60_000)}
            className="flex-1 accent-accent"
          />
          <span className="text-xs text-shell-text w-16 text-right">
            {timeoutMinutes} {timeoutMinutes === 1 ? "minute" : "minutes"}
          </span>
        </div>
      </div>

      {/* Max live tabs number input */}
      <div className="flex flex-col gap-1.5">
        <label htmlFor="max-live-tabs-input" className="text-xs text-shell-text-secondary">
          Max live tabs
        </label>
        <input
          id="max-live-tabs-input"
          type="number"
          min={1}
          max={50}
          value={maxLiveTabs}
          onChange={(e) => setMaxLiveTabs(Number(e.target.value))}
          className="bg-shell-bg-deep text-shell-text text-xs px-2 py-1 rounded border border-shell-border-subtle focus:border-accent focus:outline-none w-20"
        />
      </div>

      {/* Search engine dropdown */}
      <div className="flex flex-col gap-1.5">
        <label htmlFor="search-engine-select" className="text-xs text-shell-text-secondary">
          Default search engine
        </label>
        <select
          id="search-engine-select"
          value={searchEngine}
          onChange={(e) => setSearchEngine(e.target.value)}
          className="bg-shell-bg-deep text-shell-text text-xs px-2 py-1 rounded border border-shell-border-subtle focus:border-accent focus:outline-none"
        >
          {(Object.keys(SEARCH_ENGINES) as SearchEngine[]).map((engine) => (
            <option key={engine} value={engine}>
              {ENGINE_LABELS[engine]}
            </option>
          ))}
        </select>
      </div>
    </div>
  );
}

const ENGINE_LABELS: Record<SearchEngine, string> = {
  duckduckgo: "DuckDuckGo",
  google: "Google",
  bing: "Bing",
};
