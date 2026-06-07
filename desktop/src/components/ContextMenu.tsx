import { useEffect, useRef } from "react";

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

  useEffect(() => {
    const handler = (e: MouseEvent) => {
      if (menuRef.current && !menuRef.current.contains(e.target as Node)) {
        onClose();
      }
    };
    const keyHandler = (e: KeyboardEvent) => {
      if (e.key === "Escape") onClose();
    };
    document.addEventListener("mousedown", handler);
    document.addEventListener("keydown", keyHandler);
    return () => {
      document.removeEventListener("mousedown", handler);
      document.removeEventListener("keydown", keyHandler);
    };
  }, [onClose]);

  // Ensure menu stays within viewport
  const adjustedX = Math.min(x, window.innerWidth - 220);
  const adjustedY = Math.min(y, window.innerHeight - items.length * 36 - 20);

  return (
    <div
      ref={menuRef}
      role="menu"
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
        return (
          <button
            key={i}
            role="menuitem"
            onClick={() => {
              if (!item.disabled && item.action) {
                item.action();
                onClose();
              }
            }}
            disabled={item.disabled}
            className={`w-full flex items-center gap-2.5 px-3 py-1.5 text-left text-sm transition-colors ${
              item.disabled
                ? "text-shell-text-tertiary cursor-default"
                : "text-shell-text hover:bg-white/8"
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
