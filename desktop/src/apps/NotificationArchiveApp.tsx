import { useState, useEffect, useMemo, useCallback } from "react";
import { Search, Filter } from "lucide-react";
import type { Notification } from "@/stores/notification-store";
import {
  mapRow,
  type ServerNotificationRow,
} from "@/lib/server-notifications";
import { useRefreshOnFocus } from "@/hooks/use-refresh-on-focus";

const ARCHIVE_POLL_MS = 120_000;

function formatTime(ts: number): string {
  const delta = Date.now() - ts;
  if (delta < 60_000) return "just now";
  if (delta < 3600_000) return `${Math.floor(delta / 60_000)}m ago`;
  if (delta < 86400_000) return `${Math.floor(delta / 3600_000)}h ago`;
  return `${Math.floor(delta / 86400_000)}d ago`;
}

const LEVEL_COLORS: Record<Notification["level"], string> = {
  info: "text-shell-text-secondary",
  success: "text-green-400",
  warning: "text-amber-400",
  error: "text-red-400",
};

interface Filters {
  search: string;
  source: string;
  level: string;
}

export function NotificationArchiveApp({ windowId: _windowId }: { windowId: string }) {
  const [archived, setArchived] = useState<Notification[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [filters, setFilters] = useState<Filters>({ search: "", source: "", level: "" });
  const [showFilters, setShowFilters] = useState(false);

  const fetchArchived = useCallback(async () => {
    try {
      const res = await fetch("/api/notifications/archived", {
        headers: { Accept: "application/json" },
      });
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      const ct = res.headers.get("content-type") ?? "";
      if (!ct.includes("application/json")) throw new Error("non-JSON response");
      const data = await res.json();
      if (!Array.isArray(data)) throw new Error("unexpected response shape");
      setArchived((data as ServerNotificationRow[]).map(mapRow));
      setError(null);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to load archive");
    } finally {
      setLoading(false);
    }
  }, []);

  useRefreshOnFocus(fetchArchived);

  useEffect(() => {
    void fetchArchived();
    const interval = setInterval(() => void fetchArchived(), ARCHIVE_POLL_MS);
    return () => clearInterval(interval);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [fetchArchived]);

  const sources = useMemo(() => {
    const set = new Set(archived.map((n) => n.source));
    return Array.from(set).sort();
  }, [archived]);

  const filtered = useMemo(() => {
    return archived.filter((n) => {
      const search = filters.search.toLowerCase();
      if (search && !n.title.toLowerCase().includes(search) && !(n.body && n.body.toLowerCase().includes(search))) {
        return false;
      }
      if (filters.source && n.source !== filters.source) return false;
      if (filters.level && n.level !== filters.level) return false;
      return true;
    });
  }, [archived, filters]);

  const clearFilters = () => setFilters({ search: "", source: "", level: "" });
  const activeFilterCount = [filters.source, filters.level, filters.search].filter(Boolean).length;

  return (
    <div className="flex flex-col h-full bg-shell-bg text-shell-text">
      {/* Header */}
      <div className="flex items-center justify-between px-4 py-3 border-b border-white/10 shrink-0">
        <div>
          <h2 className="text-sm font-medium">Notification Archive</h2>
          <p className="text-[11px] text-shell-text-tertiary mt-0.5">
            {archived.length} archived notification{archived.length !== 1 ? "s" : ""}
          </p>
        </div>
        <div className="flex items-center gap-2">
          <button
            onClick={() => setShowFilters(!showFilters)}
            className={`p-1.5 rounded hover:bg-white/5 ${showFilters || activeFilterCount > 0 ? "text-accent" : "text-shell-text-tertiary"}`}
            title={`Filters${activeFilterCount > 0 ? ` (${activeFilterCount} active)` : ""}`}
          >
            <Filter size={15} />
          </button>
        </div>
      </div>

      {/* Search bar */}
      <div className="px-4 py-2.5 border-b border-white/5 shrink-0">
        <div className="relative">
          <Search size={13} className="absolute left-2.5 top-1/2 -translate-y-1/2 text-shell-text-tertiary" />
          <input
            type="text"
            placeholder="Search archive..."
            value={filters.search}
            onChange={(e) => setFilters((f) => ({ ...f, search: e.target.value }))}
            className="w-full pl-8 pr-3 py-1.5 text-xs bg-white/5 border border-white/10 rounded text-shell-text placeholder:text-shell-text-tertiary focus:outline-none focus:border-accent/50"
          />
        </div>
      </div>

      {/* Filter panel */}
      {showFilters && (
        <div className="px-4 py-3 border-b border-white/5 shrink-0 space-y-3 bg-white/[0.02]">
          <div className="flex items-center justify-between">
            <span className="text-[11px] font-medium text-shell-text-secondary">Filters</span>
            <button
              onClick={clearFilters}
              className="text-[11px] text-accent hover:underline"
            >
              Clear all
            </button>
          </div>
          <div className="grid grid-cols-2 gap-3">
            <label className="flex flex-col gap-1">
              <span className="text-[10px] text-shell-text-tertiary">Source</span>
              <select
                value={filters.source}
                onChange={(e) => setFilters((f) => ({ ...f, source: e.target.value }))}
                className="text-xs bg-white/5 border border-white/10 rounded px-2 py-1.5 text-shell-text focus:outline-none focus:border-accent/50"
              >
                <option value="">All sources</option>
                {sources.map((s) => (
                  <option key={s} value={s}>{s}</option>
                ))}
              </select>
            </label>
            <label className="flex flex-col gap-1">
              <span className="text-[10px] text-shell-text-tertiary">Level</span>
              <select
                value={filters.level}
                onChange={(e) => setFilters((f) => ({ ...f, level: e.target.value }))}
                className="text-xs bg-white/5 border border-white/10 rounded px-2 py-1.5 text-shell-text focus:outline-none focus:border-accent/50"
              >
                <option value="">All levels</option>
                <option value="info">Info</option>
                <option value="success">Success</option>
                <option value="warning">Warning</option>
                <option value="error">Error</option>
              </select>
            </label>
          </div>
        </div>
      )}

      {/* List */}
      <div className="flex-1 overflow-y-auto">
        {loading ? (
          <div className="px-4 py-16 text-center">
            <p className="text-xs text-shell-text-tertiary">Loading archive...</p>
          </div>
        ) : error ? (
          <div className="px-4 py-16 text-center">
            <p className="text-xs text-red-400 mb-2">{error}</p>
            <button
              onClick={() => { setLoading(true); void fetchArchived(); }}
              className="text-xs text-accent hover:underline"
            >
              Retry
            </button>
          </div>
        ) : filtered.length === 0 ? (
          <div className="px-4 py-16 text-center">
            <p className="text-xs text-shell-text-tertiary">
              {archived.length === 0 ? "No archived notifications" : "No matching notifications"}
            </p>
          </div>
        ) : (
          filtered.map((n) => (
            <div key={n.id} className="border-b border-white/5 px-4 py-3">
              <div className="flex items-start justify-between gap-2">
                <div className="min-w-0 flex-1">
                  <div className="flex items-center gap-2">
                    {n.read && (
                      <span className="text-[10px] text-shell-text-tertiary">read</span>
                    )}
                    <span className="text-xs font-medium text-shell-text truncate">{n.title}</span>
                  </div>
                  {n.body && (
                    <p className="text-xs text-shell-text-secondary mt-1">{n.body}</p>
                  )}
                  <div className="flex items-center gap-2 mt-1.5">
                    <span className="text-[10px] text-shell-text-tertiary">{formatTime(n.timestamp)}</span>
                    <span className="text-[10px] text-shell-text-tertiary">.</span>
                    <span className={`text-[10px] ${LEVEL_COLORS[n.level]}`}>{n.level}</span>
                    <span className="text-[10px] text-shell-text-tertiary">.</span>
                    <span className="text-[10px] text-shell-text-tertiary">{n.source}</span>
                  </div>
                  {n.action && (
                    <div className="mt-2 flex items-center gap-1">
                      <span className="text-[10px] text-shell-text-tertiary">Opens:</span>
                      <span className="text-[10px] text-accent">{n.action}</span>
                    </div>
                  )}
                </div>
                {/* Show outcome/decision data if present */}
                {n.data && typeof n.data === "object" && Object.keys(n.data).length > 0 && (
                  <div className="text-right shrink-0">
                    <div className="text-[10px] text-shell-text-tertiary max-w-[120px] truncate">
                      {JSON.stringify(n.data)}
                    </div>
                  </div>
                )}
              </div>
            </div>
          ))
        )}
      </div>

      {/* Footer */}
      <div className="px-4 py-2.5 border-t border-white/10 shrink-0 flex items-center justify-between">
        <span className="text-[10px] text-shell-text-tertiary">
          {filtered.length !== archived.length
            ? `${filtered.length} of ${archived.length} shown`
            : `${archived.length} total`}
        </span>
      </div>
    </div>
  );
}
