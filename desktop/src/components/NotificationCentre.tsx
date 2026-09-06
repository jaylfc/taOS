import { useState, useEffect, useMemo } from "react";
import { X, Bell, BellOff, CheckCheck, Trash2, Archive } from "lucide-react";
import { useNotificationStore, type Notification } from "@/stores/notification-store";
import { useProcessStore } from "@/stores/process-store";
import { getApp } from "@/registry/app-registry";
import { markServerRead, markAllServerRead } from "@/lib/server-notifications";
import {
  getPushState,
  enableNotificationsPush,
  disableNotificationsPush,
  type PushState,
} from "@/lib/notifications-push";
import { SetupChecklist } from "./SetupChecklist";
import { ConsentActions, consentPayload } from "./ConsentActions";

const FALLBACK_SIZE = { w: 900, h: 640 };

// Cap the active Inbox list; older items are accessible via the archive window.
const INBOX_CAP = 10;

function formatTime(ts: number): string {
  const delta = Date.now() - ts;
  if (delta < 60_000) return "just now";
  if (delta < 3600_000) return `${Math.floor(delta / 60_000)}m ago`;
  if (delta < 86400_000) return `${Math.floor(delta / 3600_000)}h ago`;
  return `${Math.floor(delta / 86400_000)}d ago`;
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
  // Agent access-requests carry inline Allow/Deny actions. These nested buttons
  // live outside the clickable row to keep the markup valid.
  const consent = (n.source === "auth_requests" || n.source === "agent_scope_requests") ? consentPayload(n.data) : null;
  return (
    <div className={`border-b border-white/5 ${!n.read ? "bg-accent/5" : ""}`}>
      <button
        onClick={() => onItemClick(n)}
        className={`w-full text-left px-4 py-3 hover:bg-white/5 transition-colors ${n.action ? "cursor-pointer" : ""}`}
      >
        <div className="flex items-start justify-between gap-2">
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
          <button
            onClick={(e) => {
              e.stopPropagation();
              onDismiss(n.id);
            }}
            className="p-0.5 rounded hover:bg-white/10 shrink-0"
            aria-label={`Dismiss: ${n.title}`}
          >
            <X size={12} className="text-shell-text-tertiary" />
          </button>
        </div>
      </button>
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

// "Enable notifications on this device" control. Registers a PWA web-push
// subscription so OS notifications (e.g. agent access-requests) reach this
// device even when taOS is closed. On iOS the PWA must be added to the Home
// Screen before push can be enabled (Safari limitation).
function PushToggle() {
  const [state, setState] = useState<PushState | "loading" | "working">("loading");

  useEffect(() => {
    let alive = true;
    getPushState()
      .then((s) => alive && setState(s))
      .catch(() => alive && setState("disabled"));
    return () => {
      alive = false;
    };
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

export function NotificationCentre() {
  const {
    notifications,
    centreOpen,
    closeCentre,
    markRead,
    markAllRead,
    clearAll,
    dismiss,
    archiveRead,
  } = useNotificationStore();
  const openWindow = useProcessStore((s) => s.openWindow);
  const [checklistDismissed, setChecklistDismissed] = useState(false);

  const active = notifications.filter((n) => !n.archived);
  const archived = useMemo(
    () => notifications.filter((n) => n.archived).sort((a, b) => b.timestamp - a.timestamp),
    [notifications],
  );

  // Optimistic local mark-read, plus a best-effort backend write for server
  // items so the read state persists across reloads. Network never blocks the UI.
  const handleMarkRead = (id: string) => {
    markRead(id);
    void markServerRead(id);
  };

  // Clicking an actionable notification opens its target app (with any meta as
  // launch props), marks it read, and closes the centre. Action-less items just
  // get marked read in place.
  const handleItemClick = (n: Notification) => {
    if (n.action) {
      const size = getApp(n.action)?.defaultSize ?? FALLBACK_SIZE;
      const props = n.meta && Object.keys(n.meta).length ? n.meta : undefined;
      openWindow(n.action, size, props);
      handleMarkRead(n.id);
      closeCentre();
      return;
    }
    handleMarkRead(n.id);
  };

  const handleMarkAllRead = () => {
    markAllRead();
    void markAllServerRead();
  };

  if (!centreOpen) return null;

  const isMobile = typeof window !== "undefined" && window.innerWidth < 640;

  return (
    <>
      <div className="fixed inset-0 z-[10000]" onClick={closeCentre} />
      <div
        role="dialog"
        aria-modal="true"
        aria-label="Notifications"
        className={
          isMobile
            ? "fixed left-2 right-2 z-[10001] rounded-xl border border-white/10 overflow-hidden flex flex-col"
            : "fixed top-10 right-2 z-[10001] w-80 max-h-[70vh] rounded-xl border border-white/10 overflow-hidden flex flex-col"
        }
        style={
          isMobile
            ? {
                backgroundColor: "var(--color-dock-bg)",
                backdropFilter: "blur(20px)",
                boxShadow: "0 12px 48px rgba(0,0,0,0.5)",
                top: "calc(env(safe-area-inset-top, 0px) + 52px)",
                bottom: "calc(40px + env(safe-area-inset-bottom, 0px) * 0.35 + 16px)",
              }
            : {
                backgroundColor: "var(--color-dock-bg)",
                backdropFilter: "blur(20px)",
                boxShadow: "0 12px 48px rgba(0,0,0,0.5)",
              }
        }
      >
        {/* Header */}
        <div className="flex items-center justify-between px-4 py-3 border-b border-white/10">
          <div className="flex items-center gap-2">
            <Bell size={14} className="text-shell-text-secondary" />
            <span className="text-sm font-medium text-shell-text">Notifications</span>
          </div>
          <div className="flex items-center gap-1">
            {active.length > 0 && (
              <>
                <button onClick={handleMarkAllRead} className="p-1.5 rounded hover:bg-white/5" title="Mark all read">
                  <CheckCheck size={14} className="text-shell-text-tertiary" />
                </button>
                <button onClick={clearAll} className="p-1.5 rounded hover:bg-white/5" title="Clear all">
                  <Trash2 size={14} className="text-shell-text-tertiary" />
                </button>
              </>
            )}
            <button onClick={closeCentre} className="p-1.5 rounded hover:bg-white/5" aria-label="Close notifications">
              <X size={14} className="text-shell-text-tertiary" />
            </button>
          </div>
        </div>

        {/* List */}
        <div className="flex-1 overflow-y-auto">
          <PushToggle />
          {!checklistDismissed && (
            <SetupChecklist onDismissed={() => setChecklistDismissed(true)} />
          )}
          {active.length === 0 ? (
            <div className="px-4 py-12 text-center">
              <Bell size={24} className="mx-auto text-shell-text-tertiary mb-2" />
              <p className="text-xs text-shell-text-tertiary">No notifications</p>
            </div>
          ) : (
            <>
              {active.slice(0, INBOX_CAP).map((n) => (
                <NotificationItem
                  key={n.id}
                  n={n}
                  onDismiss={dismiss}
                  onResolveConsent={archiveRead}
                  onItemClick={handleItemClick}
                />
              ))}
              {active.length > INBOX_CAP && (
                <div className="px-3 py-2.5 border-t border-white/5">
                  <p className="text-[10px] text-shell-text-tertiary text-center">
                    +{active.length - INBOX_CAP} more in archive
                  </p>
                </div>
              )}
            </>
          )}
          {/* View archive footer */}
          <div className="border-t border-white/10 px-3 py-2">
            <button
              onClick={() => {
                const size = getApp("notifications")?.defaultSize ?? { w: 900, h: 600 };
                openWindow("notifications", size, { section: "archive" });
              }}
              className="w-full flex items-center justify-center gap-2 px-3 py-2 text-xs font-medium text-accent hover:bg-white/5 rounded transition-colors"
            >
              <Archive size={13} />
              View archive{archived.length > 0 ? ` (${archived.length})` : ""}
            </button>
          </div>
        </div>
      </div>
    </>
  );
}
