import { useState, useEffect, useMemo, useCallback, useRef } from "react";
import { Search, Filter, X, Bell, BellOff, CheckCheck, Trash2, Archive } from "lucide-react";
import { useNotificationStore, type Notification } from "@/stores/notification-store";
import { useProcessStore } from "@/stores/process-store";
import { getApp } from "@/registry/app-registry";
import { markServerRead, markAllServerRead, mapRow, type ServerNotificationRow } from "@/lib/server-notifications";
import {
  getPushState,
  enableNotificationsPush,
  disableNotificationsPush,
  type PushState,
} from "@/lib/notifications-push";
import { SetupChecklist } from "@/components/SetupChecklist";
import { ConsentActions, consentPayload } from "@/components/ConsentActions";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui";

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

function PushToggle() {
  const [state, setState] = useState<PushState | "loading" | "working">("loading");

  useEffect(() => {
    let alive = true;
    getPushState()
      .then((s) => alive && setState(s))
      .catch(() => alive && setState("disabled"));
    return () => { alive = false; };
  }, []);

  if (state === "loading" || state === "unsupported") return null;

  if (state === "needs-install") {
    return (
      <div className="flex items-start gap-2 px-4 py-2.5 border-b border-white/5 text-[11px] text-shell-text-tertiary">
        <Bell size={13} className="mt-0.5 shrink-0" />
        <span>Add taOS to your Home Screen to get notifications on this device.</span>
      </div>
    );
  }

  if (state === "denied") {
    return (
      <div className="flex items-start gap-2 px-4 py-2.5 border-b border-white/5 text-[11px] text-shell-text-tertiary">
        <BellOff size={13} className="mt-0.5 shrink-0" />
        <span>Notifications are blocked. Enable them for taOS in your browser settings.</span>
      </div>
    );
  }

  const enabled = state === "enabled";
  const busy = state === "working";
  const toggle = async () => {
    setState("working");
    try {
      const next = enabled ? await disableNotificationsPush() : await enableNotificationsPush();
      setState(next);
    } catch {
      setState(await getPushState().catch(() => "disabled"));
    }
  };

  return (
    <div className="flex items-center justify-between gap-2 px-4 py-2.5 border-b border-white/5">
      <div className="flex items-center gap-2 min-w-0">
        {enabled ? (
          <Bell size={13} className="text-accent shrink-0" />
        ) : (
          <BellOff size={13} className="text-shell-text-tertiary shrink-0" />
        )}
        <span className="text-[11px] text-shell-text-secondary truncate">
          {enabled ? "Notifications on this device" : "Enable notifications on this device"}
        </span>
      </div>
      <button
        onClick={toggle}
        disabled={busy}
        className="text-[11px] font-medium text-accent hover:underline disabled:opacity-50 shrink-0"
      >
        {busy ? "..." : enabled ? "Turn off" : "Enable"}
      </button>
    </div>
  );
}

function NotificationItem({
  n,
  onDismiss,
  onResolveConsent,
  onItemClick,
}: {
  n: Notification;
  onDismiss: (id: string) => void;
  onResolveConsent: (id: string) => void;
  onItemClick: (n: Notification) => void;
}) {
  const consent = (n.source === "auth_requests" || n.source === "agent_scope_requests") ? consentPayload(n.data) : null;
  return (
    <div className={`border-b border-white/5 ${!n.read ? "bg-accent/5" : ""}`}>
      <div className="flex items-start gap-2">
        <button
          onClick={() => onItemClick(n)}
          className={`flex-1 min-w-0 text-left px-4 py-3 hover:bg-white/5 transition-colors ${n.action ? "cursor-pointer" : ""}`}
        >
          <div className="min-w-0 flex-1">
            <div className="flex items-center gap-2">
              {!n.read && <div className="w-1.5 h-1.5 rounded-full bg-accent shrink-0" />}
              <span className="text-xs font-medium text-shell-text truncate">{n.title}</span>
            </div>
            {n.body && <p className="text-xs text-shell-text-secondary mt-1 line-clamp-2">{n.body}</p>}
            <div className="flex items-center gap-2 mt-1">
              <span className="text-[10px] text-shell-text-tertiary">{formatTime(n.timestamp)}</span>
              <span className="text-[10px] text-shell-text-tertiary">.</span>
              <span className="text-[10px] text-shell-text-tertiary">{n.source}</span>
            </div>
          </div>
        </button>
        <button
          onClick={(e) => { e.stopPropagation(); onDismiss(n.id); }}
          className="p-0.5 rounded hover:bg-white/10 shrink-0"
          aria-label={`Dismiss: ${n.title}`}
        >
          <X size={12} className="text-shell-text-tertiary" />
        </button>
      </div>
      {consent && (
        <div className="px-4 pb-3">
          <ConsentActions
            requestId={consent.requestId}
            scopes={consent.scopes}
            requestedProjectId={consent.projectId}
            source={n.source}
            canonicalId={consent.canonicalId}
            onResolved={() => onResolveConsent(n.id)}
          />
        </div>
      )}
    </div>
  );
}

function ArchiveNotificationItem({ n }: { n: Notification }) {
  return (
    <div className="border-b border-white/5 px-4 py-3">
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
        {n.data && typeof n.data === "object" && Object.keys(n.data).length > 0 && (
          <div className="text-right shrink-0">
            <div className="text-[10px] text-shell-text-tertiary max-w-[120px] truncate">
              {JSON.stringify(n.data)}
            </div>
          </div>
        )}
      </div>
    </div>
  );
}

export function NotificationsApp({ windowId: _windowId, section: initialSection }: { windowId: string; section?: string }) {
  const notifications = useNotificationStore((s) => s.notifications);
  const markRead = useNotificationStore((s) => s.markRead);
  const markAllRead = useNotificationStore((s) => s.markAllRead);
  const clearAll = useNotificationStore((s) => s.clearAll);
  const dismiss = useNotificationStore((s) => s.dismiss);
  const archiveRead = useNotificationStore((s) => s.archiveRead);
  const openWindow = useProcessStore((s) => s.openWindow);
  const [checklistDismissed, setChecklistDismissed] = useState(false);
  const [activeTab, setActiveTab] = useState(() => initialSection === "archive" ? "archive" : "notifications");

  useEffect(() => {
    setActiveTab(initialSection === "archive" ? "archive" : "notifications");
  }, [initialSection]);

  const active = useMemo(() => notifications.filter((n) => !n.archived), [notifications]);
  const archived = useMemo(
    () => notifications.filter((n) => n.archived).sort((a, b) => b.timestamp - a.timestamp),
    [notifications],
  );

  const handleMarkRead = (id: string) => {
    markRead(id);
    void markServerRead(id);
  };

  const handleItemClick = (n: Notification) => {
    if (n.action) {
      const size = getApp(n.action)?.defaultSize ?? { w: 800, h: 600 };
      const props = n.meta && Object.keys(n.meta).length ? n.meta : undefined;
      openWindow(n.action, size, props);
      handleMarkRead(n.id);
      return;
    }
    handleMarkRead(n.id);
  };

  const handleMarkAllRead = () => {
    markAllRead();
    void markAllServerRead();
  };

  return (
    <div className="flex flex-col h-full bg-shell-bg text-shell-text">
      <Tabs value={activeTab} onValueChange={(v) => setActiveTab(v)} className="flex flex-col h-full">
        <TabsList className="shrink-0 px-3 pt-2 border-b border-white/5 bg-transparent justify-start gap-1 h-auto pb-0">
          <TabsTrigger value="notifications" className="text-xs pb-1.5">Notifications</TabsTrigger>
          <TabsTrigger value="archive" className="text-xs pb-1.5">Archive</TabsTrigger>
        </TabsList>

        <TabsContent value="notifications" className="flex-1 overflow-hidden mt-0">
          <div className="flex flex-col h-full">
            <PushToggle />
            {!checklistDismissed && (
              <SetupChecklist onDismissed={() => setChecklistDismissed(true)} />
            )}
            <div className="flex-1 overflow-y-auto">
              {active.length === 0 ? (
                <div className="px-4 py-12 text-center">
                  <Bell size={24} className="mx-auto text-shell-text-tertiary mb-2" />
                  <p className="text-xs text-shell-text-tertiary">No notifications</p>
                </div>
              ) : (
                <>
                  {active.map((n) => (
                    <NotificationItem
                      key={n.id}
                      n={n}
                      onDismiss={dismiss}
                      onResolveConsent={archiveRead}
                      onItemClick={handleItemClick}
                    />
                  ))}
                </>
              )}
            </div>
            <div className="border-t border-white/10 px-3 py-2 flex items-center gap-2">
              {active.length > 0 && (
                <>
                  <button
                    onClick={handleMarkAllRead}
                    className="p-1.5 rounded hover:bg-white/5"
                    title="Mark all read"
                  >
                    <CheckCheck size={14} className="text-shell-text-tertiary" />
                  </button>
                  <button
                    onClick={clearAll}
                    className="p-1.5 rounded hover:bg-white/5"
                    title="Clear all"
                  >
                    <Trash2 size={14} className="text-shell-text-tertiary" />
                  </button>
                </>
              )}
              <button
                onClick={() => setActiveTab("archive")}
                className="flex-1 flex items-center justify-center gap-2 px-3 py-2 text-xs font-medium text-accent hover:bg-white/5 rounded transition-colors"
              >
                <Archive size={13} />
                View archive{archived.length > 0 ? ` (${archived.length})` : ""}
              </button>
            </div>
          </div>
        </TabsContent>

        <TabsContent value="archive" className="flex-1 overflow-hidden mt-0">
          <ArchiveTab archived={archived} />
        </TabsContent>
      </Tabs>
    </div>
  );
}

function ArchiveTab({ archived: initialArchived }: { archived: Notification[] }) {
  const [search, setSearch] = useState("");
  const [sourceFilter, setSourceFilter] = useState("");
  const [levelFilter, setLevelFilter] = useState("");
  const [showFilters, setShowFilters] = useState(false);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [localArchived, setLocalArchived] = useState<Notification[]>(initialArchived);
  const abortRef = useRef<AbortController | null>(null);

  useEffect(() => {
    setLocalArchived((prev) => {
      const byId = new Map(prev.map((n) => [n.id, n]));
      for (const n of initialArchived) byId.set(n.id, n);
      return Array.from(byId.values()).sort((a, b) => b.timestamp - a.timestamp);
    });
  }, [initialArchived]);

  const fetchArchived = useCallback(async () => {
    if (abortRef.current) {
      abortRef.current.abort();
    }
    const controller = new AbortController();
    abortRef.current = controller;

    try {
      setLoading(true);
      setError(null);
      const res = await fetch("/api/notifications/archived", {
        headers: { Accept: "application/json" },
        signal: controller.signal,
      });
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      const ct = res.headers.get("content-type") ?? "";
      if (!ct.includes("application/json")) throw new Error("non-JSON response");
      const data = await res.json();
      if (!Array.isArray(data)) throw new Error("unexpected response shape");
      const fetched = (data as ServerNotificationRow[]).map((row) => ({ ...mapRow(row), archived: true }));
      setLocalArchived((prev) => {
        const byId = new Map(prev.map((n) => [n.id, n]));
        for (const n of fetched) {
          byId.set(n.id, n);
        }
        return Array.from(byId.values()).sort((a, b) => b.timestamp - a.timestamp);
      });
    } catch (err) {
      if ((err as Error).name === "AbortError") return;
      setError(err instanceof Error ? err.message : "Failed to load archive");
    } finally {
      if (abortRef.current === controller) {
        setLoading(false);
        abortRef.current = null;
      }
    }
  }, []);

  useEffect(() => {
    void fetchArchived();
    return () => {
      abortRef.current?.abort();
    };
  }, [fetchArchived]);

  const sources = useMemo(() => {
    const set = new Set(localArchived.map((n) => n.source));
    return Array.from(set).sort();
  }, [localArchived]);

  const filtered = useMemo(() => {
    return localArchived.filter((n) => {
      const s = search.toLowerCase();
      if (s && !n.title.toLowerCase().includes(s) && !(n.body && n.body.toLowerCase().includes(s))) {
        return false;
      }
      if (sourceFilter && n.source !== sourceFilter) return false;
      if (levelFilter && n.level !== levelFilter) return false;
      return true;
    });
  }, [localArchived, search, sourceFilter, levelFilter]);

  const clearFilters = () => { setSearch(""); setSourceFilter(""); setLevelFilter(""); };
  const activeFilterCount = [sourceFilter, levelFilter, search].filter(Boolean).length;

  return (
    <div className="flex flex-col h-full">
      <div className="flex items-center justify-between px-4 py-3 border-b border-white/10 shrink-0">
        <div>
          <h2 className="text-sm font-medium">Notification Archive</h2>
          <p className="text-[11px] text-shell-text-tertiary mt-0.5">
            {localArchived.length} archived notification{localArchived.length !== 1 ? "s" : ""}
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

      <div className="px-4 py-2.5 border-b border-white/5 shrink-0">
        <div className="relative">
          <Search size={13} className="absolute left-2.5 top-1/2 -translate-y-1/2 text-shell-text-tertiary" />
          <input
            type="text"
            placeholder="Search archive..."
            value={search}
            onChange={(e) => setSearch(e.target.value)}
            className="w-full pl-8 pr-3 py-1.5 text-xs bg-white/5 border border-white/10 rounded text-shell-text placeholder:text-shell-text-tertiary focus:outline-none focus:border-accent/50"
          />
        </div>
      </div>

      {showFilters && (
        <div className="px-4 py-3 border-b border-white/5 shrink-0 space-y-3 bg-white/[0.02]">
          <div className="flex items-center justify-between">
            <span className="text-[11px] font-medium text-shell-text-secondary">Filters</span>
            <button onClick={clearFilters} className="text-[11px] text-accent hover:underline">Clear all</button>
          </div>
          <div className="grid grid-cols-2 gap-3">
            <label className="flex flex-col gap-1">
              <span className="text-[10px] text-shell-text-tertiary">Source</span>
              <select
                value={sourceFilter}
                onChange={(e) => setSourceFilter(e.target.value)}
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
                value={levelFilter}
                onChange={(e) => setLevelFilter(e.target.value)}
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
              {localArchived.length === 0 ? "No archived notifications" : "No matching notifications"}
            </p>
          </div>
        ) : (
          filtered.map((n) => <ArchiveNotificationItem key={n.id} n={n} />)
        )}
      </div>

      <div className="px-4 py-2.5 border-t border-white/10 shrink-0 flex items-center justify-between">
        <span className="text-[10px] text-shell-text-tertiary">
          {filtered.length !== localArchived.length
            ? `${filtered.length} of ${localArchived.length} shown`
            : `${localArchived.length} total`}
        </span>
      </div>
    </div>
  );
}
