import { useRef, useCallback } from "react";
import { ChevronLeft, Bell, Search } from "lucide-react";
import { useNotificationStore } from "@/stores/notification-store";

interface Props {
  onHome: () => void;
  onCardSwitcher: () => void;
  onBack: () => void;
  onSearch?: () => void;
}

export function PillBar({ onHome, onCardSwitcher, onBack, onSearch }: Props) {
  const startY = useRef<number | null>(null);
  const pillRef = useRef<HTMLDivElement>(null);
  const unreadCount = useNotificationStore((s) => s.notifications.filter((n) => !n.read).length);
  const toggleCentre = useNotificationStore((s) => s.toggleCentre);

  const handleTouchStart = useCallback((e: React.TouchEvent) => {
    e.preventDefault();
    startY.current = e.touches[0]?.clientY ?? null;
    if (pillRef.current) {
      pillRef.current.style.transform = "scale(1.15)";
    }
  }, []);

  const handleTouchMove = useCallback((e: React.TouchEvent) => {
    e.preventDefault();
  }, []);

  const handleTouchEnd = useCallback(
    (e: React.TouchEvent) => {
      if (pillRef.current) {
        pillRef.current.style.transform = "scale(1)";
      }
      if (startY.current === null) return;

      const endY = e.changedTouches[0]?.clientY;
      if (endY === undefined) {
        startY.current = null;
        return;
      }
      const deltaY = endY - startY.current;
      startY.current = null;

      if (deltaY < -60) {
        onCardSwitcher();
      } else {
        onHome();
      }
    },
    [onCardSwitcher, onHome]
  );

  return (
    <div
      className="fixed left-0 right-0 z-[9999] flex flex-col"
      style={{
        bottom: 0,
        // 40px interactive content + 35% of the iOS safe area (home indicator clearance)
        height: "calc(40px + env(safe-area-inset-bottom, 0px) * 0.35)",
        paddingBottom: "calc(env(safe-area-inset-bottom, 0px) * 0.35)",
        backgroundColor: "#14142a",
        borderTop: "1px solid rgba(255,255,255,0.12)",
        boxShadow: "0 40px 0 0 #14142a, 0 -2px 10px rgba(0,0,0,0.3)",
      }}
    >
      {/* Interactive row — icons vertically centred in the 28px content area */}
      <div className="flex-1 flex items-center px-3">
        {/* Left — back + search */}
        <div className="flex-1 flex justify-start gap-1">
          <button
            onClick={onBack}
            className="flex items-center justify-center w-10 h-10 rounded-md active:bg-white/10 transition-colors"
            aria-label="Go back"
          >
            <ChevronLeft size={22} className="text-white/60" />
          </button>
          {onSearch && (
            <button
              onClick={onSearch}
              className="flex items-center justify-center w-10 h-10 rounded-md active:bg-white/10 transition-colors"
              aria-label="Search"
            >
              <Search size={20} className="text-white/60" />
            </button>
          )}
        </div>

        {/* Centre — pill handle */}
        <div className="flex-1 flex justify-center">
          <div
            ref={pillRef}
            className="cursor-pointer select-none"
            style={{
              width: 100,
              height: 4,
              borderRadius: 9999,
              backgroundColor: "rgba(255, 255, 255, 0.35)",
              transition: "transform 150ms ease, background-color 150ms ease",
              touchAction: "none",
            }}
            onTouchStart={handleTouchStart}
            onTouchMove={handleTouchMove}
            onTouchEnd={handleTouchEnd}
            onClick={onHome}
            role="button"
            aria-label="Home. Swipe up for app switcher."
            tabIndex={0}
          />
        </div>

        {/* Right — notifications */}
        <div className="flex-1 flex justify-end">
          <button
            onClick={toggleCentre}
            className="relative flex items-center justify-center w-10 h-10 rounded-md active:bg-white/10 transition-colors"
            aria-label={`Notifications${unreadCount > 0 ? ` (${unreadCount} unread)` : ""}`}
          >
            <Bell size={22} className="text-white/60" />
            {unreadCount > 0 && (
              <span className="absolute top-0.5 right-0.5 w-1.5 h-1.5 bg-red-500 rounded-full" />
            )}
          </button>
        </div>
      </div>
    </div>
  );
}
