import { useState, useEffect, useMemo } from "react";
import { X, Bell, BellOff, CheckCheck, Trash2, History } from "lucide-react";
import { useNotificationStore, type Notification } from "@/stores/notification-store";
import { useProcessStore } from "@/stores/process-store";
import { getApp } from "@/registry/app-registry";
import { markServerRead, markAllServerRead, fetchServerNotifications } from "@/lib/server-notifications";
import {
  getPushState,
  enableNotificationsPush,
  disableNotificationsPush,
  type PushState,
} from "@/lib/notifications-push";
import { SetupChecklist } from "@/components/SetupChecklist";
import { ConsentActions, consentPayload } from "@/components/ConsentActions";
import { useOsEvents } from "@/hooks/use-os-events";

function formatTime(ts: number): string {
  const delta = Date.now() - ts;
  if (delta < 60_000) return "just now";
  if (delta < 3600_000) return `${Math.floor(delta / 60_000)}m ago`;
  if (delta < 86400_000) return `${Math.floor(delta / 3600_000)}h ago`;
  return `${Math.floor(delta / 86400_000)}d ago`;
}

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
            humanDuration={consent.humanDuration}
            harness={consent.harness}
            onResolved={() => onResolveConsent(n.id)}
          />
        </div>
      )}
    </div>
  );
}

export function NotificationsApp({ windowId: _windowId }: { windowId: string }) {
  const notifications = useNotificationStore((s) => s.notifications);
  const markRead = useNotificationStore((s) => s.markRead);
  const markAllRead = useNotificationStore((s) => s.markAllRead);
  const clearAll = useNotificationStore((s) => s.clearAll);
  const dismiss = useNotificationStore((s) => s.dismiss);
  const archiveRead = useNotificationStore((s) => s.archiveRead);
  const openWindow = useProcessStore((s) => s.openWindow);
  const [checklistDismissed, setChecklistDismissed] = useState(false);

  const active = useMemo(() => notifications.filter((n) => !n.archived), [notifications]);

  const { stale } = useOsEvents(["notification.added"], () => {
    void fetchServerNotifications().then((items) => {
      useNotificationStore.getState().mergeServerNotifications(items);
    });
  });

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

  const openHistory = () => {
    const size = getApp("notification-history")?.defaultSize ?? { w: 900, h: 600 };
    openWindow("notification-history", size);
  };

  return (
    <div className="flex flex-col h-full bg-shell-bg text-shell-text">
      {stale && (
        <div className="shrink-0 px-4 py-1 border-b border-white/5">
          <span className="text-[11px] text-shell-text-tertiary" aria-label="live updates paused">paused</span>
        </div>
      )}
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
          onClick={openHistory}
          aria-label="View history"
          className="flex-1 flex items-center justify-center gap-2 px-3 py-2 text-xs font-medium text-accent hover:bg-white/5 rounded transition-colors"
        >
          <History size={13} aria-hidden="true" />
          View history
        </button>
      </div>
    </div>
  );
}

