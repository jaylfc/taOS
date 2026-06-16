import { useEffect, useRef, useState } from "react";
import { createPortal } from "react-dom";
import { useIsMobile } from "@/hooks/use-is-mobile";

export interface MenuItem {
  label: string;
  icon?: React.ReactNode;
  action?: () => void;
  separator?: boolean;
  disabled?: boolean;
  submenu?: MenuItem[];
}

interface Props {
  x: number;
  y: number;
  items: MenuItem[];
  onClose: () => void;
}

export function ContextMenu({ x, y, items, onClose }: Props) {
  const menuRef = useRef<HTMLDivElement>(null);
  const [activeIndex, setActiveIndex] = useState(0);

  // Close on outside click
  useEffect(() => {
    const handler = (e: MouseEvent) => {
      if (menuRef.current && !menuRef.current.contains(e.target as Node)) {
        onClose();
      }
    };
    document.addEventListener("mousedown", handler);
    return () => document.removeEventListener("mousedown", handler);
  }, [onClose]);

  // Focus the first enabled menuitem on open, and refocus if items change while open
  useEffect(() => {
    const buttons = menuRef.current?.querySelectorAll<HTMLButtonElement>('[role="menuitem"]:not([disabled])');
    if (buttons?.[0]) {
      buttons[0].focus();
      setActiveIndex(0);
    }
  }, [items]);

  const handleKeyDown = (e: React.KeyboardEvent<HTMLDivElement>) => {
    const buttons = Array.from(
      menuRef.current?.querySelectorAll<HTMLButtonElement>('[role="menuitem"]:not([disabled])') ?? [],
    );

    // If no enabled items, only allow Escape
    if (buttons.length === 0) {
      if (e.key === "Escape") {
        e.preventDefault();
        onClose();
      }
      return;
    }

    const idx = buttons.indexOf(document.activeElement as HTMLButtonElement);

    if (e.key === "ArrowDown") {
      e.preventDefault();
      // idx === -1 means focus is outside the list; treat as "before first"
      const next = idx === -1 ? 0 : (idx + 1) % buttons.length;
      buttons[next]?.focus();
      setActiveIndex(next);
    } else if (e.key === "ArrowUp") {
      e.preventDefault();
      // idx === -1: treat as "after last"
      const prev = idx === -1 ? buttons.length - 1 : (idx - 1 + buttons.length) % buttons.length;
      buttons[prev]?.focus();
      setActiveIndex(prev);
    } else if (e.key === "Home") {
      e.preventDefault();
      buttons[0]?.focus();
      setActiveIndex(0);
    } else if (e.key === "End") {
      e.preventDefault();
      buttons[buttons.length - 1]?.focus();
      setActiveIndex(buttons.length - 1);
    } else if (e.key === "Escape") {
      e.preventDefault();
      onClose();
    }
  };

  // Ensure menu stays within the viewport on both axes. Clamp the far edge
  // (so it never overflows right/bottom) AND the near edge (so a tap near the
  // left/top never produces a negative, off-screen position). On phones the
  // bottom dock occupies roughly 96px of the viewport, so reserve that plus
  // the home-indicator inset as the bottom margin. On desktop there is no
  // mobile dock, so only keep the small edge margin (a right-click near the
  // bottom should still open at the cursor, not jump up by 96px).
  const MENU_W = 220;
  const MARGIN = 8;
  const isMobile = useIsMobile();
  const BOTTOM_RESERVE = isMobile ? 96 : MARGIN; // dock + home-indicator on mobile; edge margin on desktop
  const menuH = items.length * 36 + 12;
  const maxX = window.innerWidth - MENU_W - MARGIN;
  const maxY = window.innerHeight - menuH - BOTTOM_RESERVE;
  const adjustedX = Math.max(MARGIN, Math.min(x, maxX));
  const adjustedY = Math.max(MARGIN, Math.min(y, maxY));

  // Roving tabindex: track which navigable (non-separator, non-disabled) item is active
  let navigableCounter = -1;

  // Portal to <body> so the menu is never trapped inside a transformed ancestor
  // (e.g. the dock's -translate-x-1/2). A transform on any ancestor makes a
  // containing block for position:fixed, which would offset the menu away from
  // the cursor. Rendered on body, its fixed coordinates are viewport-relative.
  return createPortal(
    <div
      ref={menuRef}
      role="menu"
      aria-label="Context menu"
      aria-orientation="vertical"
      onKeyDown={handleKeyDown}
      className="fixed z-[10001] min-w-[200px] py-1 rounded-lg border border-shell-border-strong overflow-hidden"
      style={{
        left: adjustedX,
        top: adjustedY,
        backgroundColor: "var(--color-dock-bg)",
        backdropFilter: "blur(20px)",
        boxShadow: "0 8px 32px rgba(0,0,0,0.5)",
      }}
    >
      {items.map((item, i) => {
        if (item.separator) {
          return (
            <div
              key={i}
              className="my-1 mx-2 border-t border-shell-border"
            />
          );
        }
        const isNavigable = !item.disabled;
        if (isNavigable) navigableCounter++;
        const tabIndex = isNavigable && navigableCounter === activeIndex ? 0 : -1;
        return (
          <button
            key={i}
            role="menuitem"
            tabIndex={tabIndex}
            onClick={() => {
              if (!item.disabled && item.action) {
                item.action();
                onClose();
              }
            }}
            disabled={item.disabled}
            className={`w-full flex items-center gap-2.5 px-3 py-1.5 text-left text-sm transition-colors focus:outline-none ${
              item.disabled
                ? "text-shell-text-tertiary cursor-default"
                : "text-shell-text hover:bg-white/8 focus:bg-white/8"
            }`}
          >
            {item.icon && (
              <span className="w-4 h-4 flex items-center justify-center text-shell-text-secondary">
                {item.icon}
              </span>
            )}
            <span>{item.label}</span>
          </button>
        );
      })}
    </div>,
    document.body,
  );
}
