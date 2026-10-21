import { useState, useEffect, useMemo, useRef, useCallback, useId } from "react";
import { Search, History } from "lucide-react";
import type { Notification } from "@/stores/notification-store";
import { useNotificationStore } from "@/stores/notification-store";
import {
  fetchNotificationHistory,
  fetchNotificationHistorySources,
  type NotificationHistoryQuery,
} from "@/lib/server-notifications";

export const SEARCH_DEBOUNCE_MS = 250;
const PAGE_SIZE = 50;

function formatTime(ts: number): string {
  const delta = Date.now() - ts;
  if (delta < 60_000) return "just now";
  if (delta < 3600_000) return `${Math.floor(delta / 60_000)}m ago`;
  if (delta < 86400_000) return `${Math.floor(delta / 3600_000)}h ago`;
  return `${Math.floor(delta / 86400_000)}d ago`;
}

// The level palette the Notifications app's former Archive tab used
// (also in NotificationArchiveApp.tsx).
const LEVEL_COLORS: Record<Notification["level"], string> = {
  info: "text-shell-text-secondary",
  success: "text-green-400",
  warning: "text-amber-400",
  error: "text-red-400",
};

const LEVELS = Object.keys(LEVEL_COLORS) as Notification["level"][];

type RangeKey = "today" | "7d" | "30d" | "all" | "custom";
type StateKey = "all" | "unread" | "archived";

const RANGES: { key: RangeKey; label: string }[] = [
  { key: "today", label: "Today" },
  { key: "7d", label: "7 days" },
  { key: "30d", label: "30 days" },
  { key: "all", label: "All" },
  { key: "custom", label: "Custom" },
];

const STATES: { key: StateKey; label: string }[] = [
  { key: "all", label: "All" },
  { key: "unread", label: "Unread" },
  { key: "archived", label: "Archived" },
];

/** Parse a yyyy-mm-dd date input as local midnight, in unix seconds. */
function dayStartSeconds(value: string): number | undefined {
  if (!value) return undefined;
  const [y, m, d] = value.split("-").map(Number);
  if (!y || !m || !d) return undefined;
  return Math.floor(new Date(y, m - 1, d).getTime() / 1000);
}

/** Resolve the date range filter to since/until unix seconds. */
export function rangeBounds(
  range: RangeKey,
  customFrom: string,
  customTo: string,
): { since?: number; until?: number } {
  const nowSec = Math.floor(Date.now() / 1000);
  switch (range) {
    case "today": {
      const start = new Date();
      start.setHours(0, 0, 0, 0);
      return { since: Math.floor(start.getTime() / 1000) };
    }
    case "7d":
      return { since: nowSec - 7 * 86400 };
    case "30d":
      return { since: nowSec - 30 * 86400 };
    case "custom": {
      const since = dayStartSeconds(customFrom);
      // Parse customTo as y,m,d (same as dayStartSeconds) and set 
      // `until = Math.floor(new Date(y, m - 1, d + 1).getTime() / 1000) - 1` 
      // (next local midnight minus one second).
      // Keep `undefined` when customTo is empty or invalid.
      let until: number | undefined;
      if (customTo) {
        const [y, m, d] = customTo.split("-").map(Number);
        if (y && m && d) {
          until = Math.floor(new Date(y, m - 1, d + 1).getTime() / 1000) - 1;
        }
      }
      return { since, until };
    }
    default:
      return {};
  }
}

function toggle<T>(list: T[], value: T): T[] {
  return list.includes(value) ? list.filter((v) => v !== value) : [...list, value];
}

function HistoryRow({
  n,
  expanded,
  onToggle,
}: {
  n: Notification;
  expanded: boolean;
  onToggle: (id: string) => void;
}) {
  const bodyId = `${n.id}-body`;
  // Consent rows (auth_requests, agent_scope_requests) stay read-only here:
  // History never renders Allow / Deny actions, the live inbox owns those.
  return (
    <li className={`border-b border-white/5 ${!n.read ? "bg-accent/5" : ""}`}>
      <button
        type="button"
        onClick={() => onToggle(n.id)}
        aria-expanded={expanded}
        aria-controls={n.body ? bodyId : undefined}
        className="w-full text-left px-4 py-3 hover:bg-white/5 transition-colors"
      >
        <div className="flex items-center gap-2">
          {!n.read && <span className="w-1.5 h-1.5 rounded-full bg-accent shrink-0" aria-label="unread" />}
          <span className="text-xs font-medium text-shell-text truncate">{n.title}</span>
          {n.archived && (
            <span className="text-[10px] text-shell-text-tertiary shrink-0">archived</span>
          )}
        </div>
        {n.body && (
          <p
            id={bodyId}
            className={`text-xs text-shell-text-secondary mt-1 whitespace-pre-wrap break-words ${expanded ? "" : "line-clamp-2"}`}
          >
            {n.body}
          </p>
        )}
        <div className="flex items-center gap-2 mt-1">
          <span className="text-[10px] text-shell-text-tertiary">{formatTime(n.timestamp)}</span>
          <span className="text-[10px] text-shell-text-tertiary">.</span>
          <span className={`text-[10px] ${LEVEL_COLORS[n.level]}`}>{n.level}</span>
          <span className="text-[10px] text-shell-text-tertiary">.</span>
          <span className="text-[10px] text-shell-text-tertiary">{n.source}</span>
        </div>
      </button>
    </li>
  );
}

export function NotificationHistoryApp({ windowId: _windowId }: { windowId: string }) {
  const idPrefix = useId();
  const [searchInput, setSearchInput] = useState("");
  const [q, setQ] = useState("");
  const [sources, setSources] = useState<string[]>([]);
  const [levels, setLevels] = useState<string[]>([]);
  const [range, setRange] = useState<RangeKey>("all");
  const [customFrom, setCustomFrom] = useState("");
  const [customTo, setCustomTo] = useState("");
  const [stateFilter, setStateFilter] = useState<StateKey>("all");
  const [availableSources, setAvailableSources] = useState<string[]>([]);
  const notifications = useNotificationStore((s) => s.notifications);

  const [items, setItems] = useState<Notification[]>([]);
  const [nextBefore, setNextBefore] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [loadingMore, setLoadingMore] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [reloadKey, setReloadKey] = useState(0);
  const [expanded, setExpanded] = useState<Set<string>>(() => new Set());
  const abortRef = useRef<AbortController | null>(null);

  // Debounce the search box so each keystroke does not hit the backend.
  useEffect(() => {
    const t = setTimeout(() => setQ(searchInput), SEARCH_DEBOUNCE_MS);
    return () => clearTimeout(t);
  }, [searchInput]);

  useEffect(() => {
    const controller = new AbortController();
    fetchNotificationHistorySources(controller.signal)
      .then((s) => setAvailableSources(s))
      .catch(() => {
        // Non-fatal: the source filter simply has no options.
      });
    return () => controller.abort();
  }, []);

  const query = useMemo<NotificationHistoryQuery>(() => {
    const { since, until } = rangeBounds(range, customFrom, customTo);
    return { q, sources, levels, since, until, state: stateFilter, limit: PAGE_SIZE };
  }, [q, sources, levels, range, customFrom, customTo, stateFilter]);

  const localArchived = useMemo(() => {
    const { since, until } = rangeBounds(range, customFrom, customTo);
    const search = q.trim().toLowerCase();
    return notifications
      .filter(n => n.archived && !n.id.startsWith("srv-"))
      .filter(n => {
        // Apply text search
        if (search) {
          const title = n.title.toLowerCase();
          const body = n.body.toLowerCase();
          if (!title.includes(search) && !body.includes(search)) {
            return false;
          }
        }
        // Apply sources filter
        if (sources.length > 0 && !sources.includes(n.source)) {
          return false;
        }
        // Apply levels filter
        if (levels.length > 0 && !levels.includes(n.level)) {
          return false;
        }
        // Apply range bounds
        const tsSec = n.timestamp / 1000;
        if (since !== undefined && tsSec < since) {
          return false;
        }
        if (until !== undefined && tsSec > until) {
          return false;
        }
        // Apply stateFilter
        switch (stateFilter) {
          case "all":
            return true;
          case "unread":
            return !n.read;
          case "archived":
            // We already filtered by archived === true, so this is always true.
            // But to be consistent with the server's meaning, we return true.
            return true;
        }
      })
      .sort((a, b) => b.timestamp - a.timestamp);
  }, [notifications, q, sources, levels, range, customFrom, customTo, stateFilter]);

  // First page: refetch from the top whenever any filter changes.
  useEffect(() => {
    abortRef.current?.abort();
    const controller = new AbortController();
    abortRef.current = controller;
    setLoading(true);
    setLoadingMore(false);
    setError(null);
    fetchNotificationHistory(query, controller.signal)
      .then((page) => {
        if (controller.signal.aborted) return;
        setItems(page.items);
        setNextBefore(page.nextBefore);
      })
      .catch((err) => {
        if (controller.signal.aborted || (err as Error).name === "AbortError") return;
        setItems([]);
        setNextBefore(null);
        setError(err instanceof Error ? err.message : "Failed to load history");
      })
      .finally(() => {
        if (abortRef.current === controller) setLoading(false);
      });
    return () => controller.abort();
  }, [query, reloadKey]);

  const loadMore = useCallback(() => {
    if (!nextBefore || loadingMore) return;
    abortRef.current?.abort();
    const controller = new AbortController();
    abortRef.current = controller;
    setLoadingMore(true);
    fetchNotificationHistory({ ...query, before: nextBefore }, controller.signal)
      .then((page) => {
        if (controller.signal.aborted) return;
        setItems((prev) => {
          const seen = new Set(prev.map((n) => n.id));
          return [...prev, ...page.items.filter((n) => !seen.has(n.id))];
        });
        setNextBefore(page.nextBefore);
      })
      .catch((err) => {
        if (controller.signal.aborted || (err as Error).name === "AbortError") return;
        setError(err instanceof Error ? err.message : "Failed to load more");
      })
      .finally(() => {
        if (abortRef.current === controller) setLoadingMore(false);
      });
  }, [nextBefore, loadingMore, query]);

  const toggleExpanded = useCallback((id: string) => {
    setExpanded((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  }, []);

  const filtersActive =
    q.trim() !== "" || sources.length > 0 || levels.length > 0 || range !== "all" || stateFilter !== "all";

  const clearFilters = () => {
    setSearchInput("");
    setQ("");
    setSources([]);
    setLevels([]);
    setRange("all");
    setCustomFrom("");
    setCustomTo("");
    setStateFilter("all");
  };

  const statusText = loading
    ? "Loading history"
    : error
      ? "Failed to load history"
      : `${items.length + localArchived.length}${nextBefore ? "+" : ""} notification${items.length + localArchived.length !== 1 ? "s" : ""}`;

  return (
    <div className="flex flex-col h-full bg-shell-bg text-shell-text">
      <div className="flex items-center justify-between px-4 py-3 border-b border-white/10 shrink-0">
        <div className="flex items-center gap-2">
          <History size={15} className="text-shell-text-secondary" aria-hidden="true" />
          <div>
            <h2 className="text-sm font-medium">Notification History</h2>
            <p className="text-[11px] text-shell-text-tertiary mt-0.5" aria-live="polite">
              {statusText}
            </p>
          </div>
        </div>
        {filtersActive && (
          <button
            type="button"
            onClick={clearFilters}
            className="text-[11px] text-accent hover:underline"
            aria-label="Clear all filters"
          >
            Clear filters
          </button>
        )}
      </div>

      <div className="px-4 py-2.5 border-b border-white/5 shrink-0">
        <div className="relative">
          <Search
            size={13}
            className="absolute left-2.5 top-1/2 -translate-y-1/2 text-shell-text-tertiary"
            aria-hidden="true"
          />
          <input
            type="search"
            placeholder="Search notifications..."
            aria-label="Search notification history"
            value={searchInput}
            onChange={(e) => setSearchInput(e.target.value)}
            className="w-full pl-8 pr-3 py-1.5 text-xs bg-white/5 border border-white/10 rounded text-shell-text placeholder:text-shell-text-tertiary focus:outline-none focus:border-accent/50"
          />
        </div>
      </div>

      <div
        role="group"
        aria-label="Notification history filters"
        className="px-4 py-3 border-b border-white/5 shrink-0 flex flex-wrap gap-x-6 gap-y-3 bg-white/[0.02]"
      >
        <fieldset className="min-w-0">
          <legend className="text-[10px] text-shell-text-tertiary mb-1">Source</legend>
          {availableSources.length === 0 ? (
            <p className="text-[11px] text-shell-text-tertiary">No sources</p>
          ) : (
            <div className="flex flex-wrap gap-x-3 gap-y-1 max-h-20 overflow-y-auto">
              {availableSources.map((s) => (
                <label key={s} className="flex items-center gap-1 text-[11px] text-shell-text-secondary">
                  <input
                    type="checkbox"
                    checked={sources.includes(s)}
                    onChange={() => setSources((prev) => toggle(prev, s))}
                    aria-label={`Source ${s}`}
                  />
                  {s}
                </label>
              ))}
            </div>
          )}
        </fieldset>

        <fieldset>
          <legend className="text-[10px] text-shell-text-tertiary mb-1">Level</legend>
          <div className="flex flex-wrap gap-x-3 gap-y-1">
            {LEVELS.map((l) => (
              <label key={l} className={`flex items-center gap-1 text-[11px] ${LEVEL_COLORS[l]}`}>
                <input
                  type="checkbox"
                  checked={levels.includes(l)}
                  onChange={() => setLevels((prev) => toggle(prev, l))}
                  aria-label={`Level ${l}`}
                />
                {l}
              </label>
            ))}
          </div>
        </fieldset>

        <fieldset>
          <legend className="text-[10px] text-shell-text-tertiary mb-1">Date</legend>
          <div className="flex flex-wrap items-center gap-2">
            <select
              value={range}
              onChange={(e) => setRange(e.target.value as RangeKey)}
              aria-label="Date range"
              className="text-xs bg-white/5 border border-white/10 rounded px-2 py-1 text-shell-text focus:outline-none focus:border-accent/50"
            >
              {RANGES.map((r) => (
                <option key={r.key} value={r.key}>{r.label}</option>
              ))}
            </select>
            {range === "custom" && (
              <>
                <label htmlFor={`${idPrefix}-from`} className="text-[10px] text-shell-text-tertiary">From</label>
                <input
                  id={`${idPrefix}-from`}
                  type="date"
                  value={customFrom}
                  onChange={(e) => setCustomFrom(e.target.value)}
                  className="text-xs bg-white/5 border border-white/10 rounded px-2 py-1 text-shell-text focus:outline-none focus:border-accent/50"
                />
                <label htmlFor={`${idPrefix}-to`} className="text-[10px] text-shell-text-tertiary">To</label>
                <input
                  id={`${idPrefix}-to`}
                  type="date"
                  value={customTo}
                  onChange={(e) => setCustomTo(e.target.value)}
                  className="text-xs bg-white/5 border border-white/10 rounded px-2 py-1 text-shell-text focus:outline-none focus:border-accent/50"
                />
              </>
            )}
          </div>
        </fieldset>

        <fieldset>
          <legend className="text-[10px] text-shell-text-tertiary mb-1">State</legend>
          <div role="radiogroup" aria-label="State" className="flex items-center gap-1">
            {STATES.map((s) => (
              <button
                key={s.key}
                type="button"
                role="radio"
                aria-checked={stateFilter === s.key}
                onClick={() => setStateFilter(s.key)}
                className={`text-[11px] px-2 py-1 rounded ${stateFilter === s.key ? "bg-accent/20 text-accent" : "text-shell-text-secondary hover:bg-white/5"}`}
              >
                {s.label}
              </button>
            ))}
          </div>
        </fieldset>
       </div>

       <div className="flex-1 overflow-y-auto">
         {localArchived.length > 0 && (
           <>
             <h3 className="text-[11px] font-medium text-shell-text-secondary mx-4 my-2">On this device</h3>
             <ul aria-label="Notifications on this device">
               {localArchived.map((n) => (
                 <HistoryRow key={n.id} n={n} expanded={expanded.has(n.id)} onToggle={toggleExpanded} />
               ))}
             </ul>
           </>
         )}
         {loading ? (
           <div className="px-4 py-16 text-center">
             <p className="text-xs text-shell-text-tertiary">Loading history...</p>
           </div>
         ) : error && items.length === 0 ? (
           <div className="px-4 py-16 text-center" role="alert">
             <p className="text-xs text-red-400 mb-2">{error}</p>
             <button
               type="button"
               onClick={() => setReloadKey((k) => k + 1)}
               className="text-xs text-accent hover:underline"
             >
               Retry
             </button>
           </div>
         ) : items.length === 0 && localArchived.length === 0 ? (
           <div className="px-4 py-16 text-center">
             <History size={24} className="mx-auto text-shell-text-tertiary mb-2" aria-hidden="true" />
             <p className="text-xs text-shell-text-tertiary">
               {filtersActive ? "No matching notifications" : "No notifications yet"}
             </p>
           </div>
         ) : (
           <>
             <ul aria-label="Notification history results">
               {items.map((n) => (
                 <HistoryRow key={n.id} n={n} expanded={expanded.has(n.id)} onToggle={toggleExpanded} />
               ))}
             </ul>
             {error && (
               <div className="px-4 py-2 text-center" role="alert">
                 <span className="text-[11px] text-red-400 mr-2">{error}</span>
                 <button
                   type="button"
                   onClick={() => { setError(null); loadMore(); }}
                   className="text-[11px] text-accent hover:underline"
                 >
                   Retry
                 </button>
               </div>
             )}
             {nextBefore && !error && (
               <div className="px-4 py-3 text-center">
                 <button
                   type="button"
                   onClick={loadMore}
                   disabled={loadingMore}
                   className="text-xs text-accent hover:underline disabled:opacity-50"
                 >
                   {loadingMore ? "Loading..." : "Load more"}
                 </button>
               </div>
             )}
           </>
         )}
       </div>
    </div>
  );
}
