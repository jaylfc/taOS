import { X, Bell, CheckCheck, Trash2 } from "lucide-react";
import { useNotificationStore } from "@/stores/notification-store";

function formatTime(ts: number): string {
  const delta = Date.now() - ts;
  if (delta < 60_000) return "just now";
  if (delta < 3600_000) return `${Math.floor(delta / 60_000)}m ago`;
  if (delta < 86400_000) return `${Math.floor(delta / 3600_000)}h ago`;
  return `${Math.floor(delta / 86400_000)}d ago`;
}

export function NotificationCentre() {
  const { notifications, centreOpen, closeCentre, markRead, markAllRead, clearAll, dismiss } = useNotificationStore();

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
                backgroundColor: "rgba(26, 27, 46, 0.97)",
                backdropFilter: "blur(20px)",
                boxShadow: "0 12px 48px rgba(0,0,0,0.5)",
                top: "calc(env(safe-area-inset-top, 0px) + 52px)",
                bottom: "calc(40px + env(safe-area-inset-bottom, 0px) * 0.35 + 16px)",
              }
            : {
                backgroundColor: "rgba(26, 27, 46, 0.97)",
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
            {notifications.length > 0 && (
              <>
                <button onClick={markAllRead} className="p-1.5 rounded hover:bg-white/5" title="Mark all read">
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
          {notifications.length === 0 ? (
            <div className="px-4 py-12 text-center">
              <Bell size={24} className="mx-auto text-shell-text-tertiary mb-2" />
              <p className="text-xs text-shell-text-tertiary">No notifications</p>
            </div>
          ) : (
            notifications.map((n) => (
              <button
                key={n.id}
                onClick={() => markRead(n.id)}
                className={`w-full text-left px-4 py-3 border-b border-white/5 hover:bg-white/5 transition-colors ${!n.read ? "bg-accent/5" : ""}`}
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
                      <span className="text-[10px] text-shell-text-tertiary">·</span>
                      <span className="text-[10px] text-shell-text-tertiary">{n.source}</span>
                    </div>
                  </div>
                  <button
                    onClick={(e) => {
                      e.stopPropagation();
                      dismiss(n.id);
                    }}
                    className="p-0.5 rounded hover:bg-white/10 shrink-0"
                    aria-label={`Dismiss: ${n.title}`}
                  >
                    <X size={12} className="text-shell-text-tertiary" />
                  </button>
                </div>
              </button>
            ))
          )}
        </div>
      </div>
    </>
  );
}
