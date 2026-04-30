import { useRef, useState, useCallback } from "react";
import { createPortal } from "react-dom";
import * as icons from "lucide-react";
import { X, Plus } from "lucide-react";
import { useProcessStore } from "@/stores/process-store";
import { getApp } from "@/registry/app-registry";

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

export function CardSwitcher({ open, onClose, onSelectApp, onLaunchpad }: Props) {
  const windows = useProcessStore((s) => s.windows);
  const closeWindow = useProcessStore((s) => s.closeWindow);
  const [dismissing, setDismissing] = useState<Record<string, number>>({});
  const touchStartY = useRef<Record<string, number>>({});

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
    (windowId: string) => {
      const delta = dismissing[windowId] ?? 0;
      if (delta < -100) {
        setDismissing((prev) => ({ ...prev, [windowId]: -999 }));
        setTimeout(() => {
          closeWindow(windowId);
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
    [dismissing, closeWindow]
  );

  if (!open) return null;

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

      {windows.length === 0 ? (
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
            const offset = dismissing[win.id] ?? 0;
            const isFlying = offset === -999;

            return (
              <div
                key={win.id}
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
                onTouchStart={(e) => handleCardTouchStart(win.id, e.touches[0]?.clientY ?? 0)}
                onTouchMove={(e) => handleCardTouchMove(win.id, e.touches[0]?.clientY ?? 0)}
                onTouchEnd={() => handleCardTouchEnd(win.id)}
                onClick={(e) => {
                  e.stopPropagation();
                  onSelectApp(win.id);
                  onClose();
                }}
                role="button"
                aria-label={`Switch to ${app?.name ?? "Unknown"}`}
                tabIndex={0}
              >
                <button
                  onClick={(e) => {
                    e.stopPropagation();
                    closeWindow(win.id);
                  }}
                  className="absolute top-3 right-3 flex items-center justify-center w-7 h-7 rounded-full bg-white/10 active:bg-white/20 transition-colors"
                  aria-label={`Close ${app?.name ?? "app"}`}
                >
                  <X size={14} className="text-white/70" />
                </button>

                <IconComponent size={40} className="text-white/60 mb-3" />
                <span className="text-white/80 text-sm font-medium">
                  {app?.name ?? win.appId}
                </span>
              </div>
            );
          })}
        </div>
      )}
    </div>,
    document.body,
  );
}
