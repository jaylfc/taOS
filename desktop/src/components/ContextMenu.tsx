import { useEffect, useRef, useState } from "react";

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
    buttons?.[0]?.focus();
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

  // Ensure menu stays within viewport
  const adjustedX = Math.min(x, window.innerWidth - 220);
  const adjustedY = Math.min(y, window.innerHeight - items.length * 36 - 20);

  // Roving tabindex: track which navigable (non-separator, non-disabled) item is active
  let navigableCounter = -1;

  return (
    <div
      ref={menuRef}
      role="menu"
      aria-label="Context menu"
      onKeyDown={handleKeyDown}
      className="fixed z-[10001] min-w-[200px] py-1 rounded-lg border border-shell-border-strong overflow-hidden"
      style={{
        left: adjustedX,
        top: adjustedY,
        backgroundColor: "rgba(30, 31, 50, 0.95)",
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
    </div>
  );
}
