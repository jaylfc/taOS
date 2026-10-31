import { useRef, useState, useCallback, useEffect, type ReactNode } from "react";
import { createPortal } from "react-dom";
import * as icons from "lucide-react";
import { X, Plus } from "lucide-react";
import { useProcessStore } from "@/stores/process-store";
import { getApp, getAllApps } from "@/registry/app-registry";
import {
  closeShellWindow,
  fetchShellSnapshot,
  focusShellWindow,
  isShellDevice,
  listShellWindows,
  type ShellWindow,
} from "@/lib/mobile-shell";
import { appRuntime, shellWindowRuntime, type AppRuntime } from "@/lib/app-runtime";
import { AppTypeBadge } from "@/components/AppTypeBadge";

const SHELL_POLL_MS = 2000;

interface Props {
  open: boolean;
  onClose: () => void;
  onSelectApp: (windowId: string) => void;
  onLaunchpad: () => void;
}

function resolveIcon(iconName: string): icons.LucideIcon {
  const key = iconName
    .split("-")
    .map((s) => s.charAt(0).toUpperCase() + s.slice(1))
    .join("") as keyof typeof icons;
  return (icons[key] as icons.LucideIcon) ?? icons.HelpCircle;
}

/** What a compositor window shows as: Firefox is "Browser", the rest use their title. */
function shellWindowName(w: ShellWindow): string {
  return w.app_id === "firefox" ? "Browser" : w.label || w.title || w.app_id;
}

function shellWindowIcon(w: ShellWindow): icons.LucideIcon {
  const app = getAllApps().find((a) => a.name === w.label);
  if (app) return resolveIcon(app.icon);
  return w.app_id === "firefox" ? icons.Globe : icons.AppWindow;
}

/**
 * A native window's last snapshot, as an object URL, or null while there is
 * none. Fetched once per con_id + snapshot mtime, never on every render; every
 * URL made here is revoked when replaced, on unmount, or (when the fetch lands
 * after the key changed or the card went away) as soon as it arrives.
 */
function useShellSnapshotUrl(conId: number, snapshot: number | null | undefined): string | null {
  const key = typeof snapshot === "number" ? `${conId}:${snapshot}` : null;
  const [loaded, setLoaded] = useState<{ key: string; url: string } | null>(null);

  useEffect(() => {
    if (key === null) return;
    let live = true;
    let url: string | null = null;
    void fetchShellSnapshot(conId).then((made) => {
      if (made === null) return;
      if (!live) {
        URL.revokeObjectURL(made);
        return;
      }
      url = made;
      setLoaded({ key, url: made });
    });
    return () => {
      live = false;
      if (url !== null) URL.revokeObjectURL(url);
    };
  }, [conId, key]);

  return loaded !== null && loaded.key === key ? loaded.url : null;
}

/** A native window card's face: its snapshot when there is one, else the icon. */
function ShellCardPreview({ window: w, fallback }: { window: ShellWindow; fallback: ReactNode }) {
  const url = useShellSnapshotUrl(w.con_id, w.snapshot);
  if (url === null) return <>{fallback}</>;
  return (
    <img
      src={url}
      alt=""
      draggable={false}
      className="absolute inset-0 w-full h-full object-cover object-top rounded-2xl pointer-events-none"
    />
  );
}

export function CardSwitcher({ open, onClose, onSelectApp, onLaunchpad }: Props) {
  const windows = useProcessStore((s) => s.windows);
  // removeWindow, not closeWindow: closeWindow only marks a window closing for
  // the desktop Window's close animation, which the phone layout never mounts.
  const removeWindow = useProcessStore((s) => s.removeWindow);
  const [dismissing, setDismissing] = useState<Record<string, number>>({});
  const touchStartY = useRef<Record<string, number>>({});
  const [shellWindows, setShellWindows] = useState<ShellWindow[]>([]);
  const shellDevice = isShellDevice();

  const refreshShellWindows = useCallback(async () => {
    setShellWindows(await listShellWindows());
  }, []);

  // taOS apps on the handset are their own compositor windows, so the in-page
  // store cannot see them (or Firefox): ask the shell while the switcher is up.
  useEffect(() => {
    if (!open || !shellDevice) return;
    let cancelled = false;
    const poll = () =>
      listShellWindows().then((list) => {
        if (!cancelled) setShellWindows(list);
      });
    void poll();
    const timer = setInterval(() => void poll(), SHELL_POLL_MS);
    return () => {
      cancelled = true;
      clearInterval(timer);
    };
  }, [open, shellDevice]);

  const handleCardTouchStart = useCallback((windowId: string, clientY: number) => {
    touchStartY.current[windowId] = clientY;
    setDismissing((prev) => ({ ...prev, [windowId]: 0 }));
  }, []);

  const handleCardTouchMove = useCallback((windowId: string, clientY: number) => {
    const start = touchStartY.current[windowId];
    if (start === undefined) return;
    const delta = clientY - start;
    if (delta < 0) {
      setDismissing((prev) => ({ ...prev, [windowId]: delta }));
    }
  }, []);

  const handleCardTouchEnd = useCallback(
    (windowId: string, dismiss: () => void) => {
      const delta = dismissing[windowId] ?? 0;
      if (delta < -100) {
        setDismissing((prev) => ({ ...prev, [windowId]: -999 }));
        setTimeout(() => {
          dismiss();
          setDismissing((prev) => {
            const next = { ...prev };
            delete next[windowId];
            return next;
          });
        }, 250);
      } else {
        setDismissing((prev) => {
          const next = { ...prev };
          delete next[windowId];
          return next;
        });
      }
      delete touchStartY.current[windowId];
    },
    [dismissing]
  );

  const renderCard = (
    key: string,
    name: string,
    IconComponent: icons.LucideIcon,
    runtime: AppRuntime,
    onSelect: () => void,
    onDismiss: () => void,
    shellWindow?: ShellWindow,
  ) => {
    const offset = dismissing[key] ?? 0;
    const isFlying = offset === -999;
    const icon = (
      <span className="relative mb-3 inline-flex rounded-lg">
        <IconComponent size={40} className="text-white/60" />
        <AppTypeBadge runtime={runtime} size="lg" />
      </span>
    );

    return (
      <div
        key={key}
        className="flex-shrink-0 snap-center relative flex flex-col items-center justify-center rounded-2xl bg-white/8 border border-white/10"
        style={{
          width: "70vw",
          aspectRatio: "9 / 16",
          transform: isFlying
            ? "translateY(-120vh)"
            : offset < 0
              ? `translateY(${offset}px)`
              : undefined,
          transition: isFlying
            ? "transform 250ms ease-in"
            : offset === 0
              ? "transform 200ms ease-out"
              : undefined,
          opacity: isFlying ? 0 : 1,
        }}
        onTouchStart={(e) => handleCardTouchStart(key, e.touches[0]?.clientY ?? 0)}
        onTouchMove={(e) => handleCardTouchMove(key, e.touches[0]?.clientY ?? 0)}
        onTouchEnd={() => handleCardTouchEnd(key, onDismiss)}
        onClick={(e) => {
          e.stopPropagation();
          onSelect();
        }}
        onKeyDown={(e) => {
          // Only the card itself: Enter on the X button must not also switch.
          if (e.target !== e.currentTarget) return;
          if (e.key === "Enter" || e.key === " ") {
            e.preventDefault();
            onSelect();
          }
        }}
        role="button"
        aria-label={`Switch to ${name}`}
        tabIndex={0}
      >
        <button
          onClick={(e) => {
            e.stopPropagation();
            onDismiss();
          }}
          className="absolute top-3 right-3 z-10 flex items-center justify-center w-7 h-7 rounded-full bg-white/10 active:bg-white/20 transition-colors"
          aria-label={`Close ${name}`}
        >
          <X size={14} className="text-white/70" />
        </button>

        {shellWindow ? <ShellCardPreview window={shellWindow} fallback={icon} /> : icon}
        <span className={`relative z-10 text-white/80 text-sm font-medium${shellWindow ? " drop-shadow" : ""}`}>{name}</span>
      </div>
    );
  };

  if (!open) return null;

  const visibleShellWindows = shellDevice ? shellWindows : [];

  return createPortal(
    <div
      className="fixed inset-0 flex flex-col backdrop-blur-sm"
      style={{ zIndex: 9000, backgroundColor: "rgba(0, 0, 0, 0.6)" }}
      role="dialog"
      aria-label="App Switcher"
      onClick={onClose}
    >
      <div
        className="flex items-center justify-between px-4 pt-4 pb-2"
        onClick={(e) => e.stopPropagation()}
      >
        <h2 className="text-white text-lg font-medium">App Switcher</h2>
        <button
          onClick={onClose}
          className="flex items-center justify-center w-8 h-8 rounded-full bg-white/10 active:bg-white/20 transition-colors"
          aria-label="Close app switcher"
        >
          <X size={18} className="text-white/80" />
        </button>
      </div>

      {windows.length === 0 && visibleShellWindows.length === 0 ? (
        <div className="flex-1 flex flex-col items-center justify-center gap-4">
          <p className="text-white/60 text-base">No apps open</p>
          <button
            onClick={(e) => {
              e.stopPropagation();
              onLaunchpad();
              onClose();
            }}
            className="flex items-center gap-2 px-4 py-2 rounded-lg bg-white/10 active:bg-white/20 text-white/80 transition-colors"
            aria-label="Open Launchpad"
          >
            <Plus size={18} />
            Open Launchpad
          </button>
        </div>
      ) : (
        <div
          className="flex-1 flex items-center overflow-x-auto snap-x snap-mandatory"
          style={{
            scrollSnapType: "x mandatory",
            WebkitOverflowScrolling: "touch",
            paddingLeft: "calc(50vw - 35vw)",
            paddingRight: "calc(50vw - 35vw)",
            gap: "1rem",
          }}
          onClick={(e) => {
            if (e.target === e.currentTarget) onClose();
          }}
        >
          {windows.map((win) => {
            const app = getApp(win.appId);
            const IconComponent = app ? resolveIcon(app.icon) : icons.HelpCircle;
            return renderCard(
              win.id,
              app?.name ?? win.appId,
              IconComponent,
              app ? appRuntime(app) : "taos",
              () => {
                onSelectApp(win.id);
                onClose();
              },
              () => removeWindow(win.id),
            );
          })}
          {visibleShellWindows.map((w) =>
            renderCard(
              `shell-${w.con_id}`,
              shellWindowName(w),
              shellWindowIcon(w),
              shellWindowRuntime(w.app_id),
              () => {
                void focusShellWindow(w.con_id);
                onClose();
              },
              () => {
                void closeShellWindow(w.con_id).then(refreshShellWindows);
              },
              w,
            ),
          )}
        </div>
      )}
    </div>,
    document.body,
  );
}
