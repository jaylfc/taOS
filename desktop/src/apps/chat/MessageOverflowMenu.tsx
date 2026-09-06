import { useEffect, useRef } from "react";

export interface MessageOverflowMenuProps {
  isOwn: boolean;
  isHuman: boolean;
  isPinned?: boolean;
  onEdit: () => void;
  onDelete: () => void;
  onCopyLink: () => void;
  onCopyText?: () => void;
  onPin: () => void;
  onMarkUnread: () => void;
  onClose?: () => void;
}

export function MessageOverflowMenu({
  isOwn, isHuman, isPinned = false,
  onEdit, onDelete, onCopyLink, onCopyText, onPin, onMarkUnread, onClose,
}: MessageOverflowMenuProps) {
  const containerRef = useRef<HTMLDivElement>(null);

  // Focus first menuitem on mount so keyboard users can immediately navigate.
  useEffect(() => {
    const first = containerRef.current?.querySelector<HTMLButtonElement>('[role="menuitem"]');
    first?.focus();
  }, []);

  const handleKeyDown = (e: React.KeyboardEvent<HTMLDivElement>) => {
    const items = Array.from(
      containerRef.current?.querySelectorAll<HTMLButtonElement>('[role="menuitem"]') || [],
    );
    const active = document.activeElement as HTMLButtonElement | null;
    const idx = active ? items.indexOf(active) : -1;
    if (e.key === "ArrowDown") {
      e.preventDefault();
      items[Math.min(items.length - 1, idx + 1)]?.focus();
    } else if (e.key === "ArrowUp") {
      e.preventDefault();
      items[Math.max(0, idx - 1)]?.focus();
    } else if (e.key === "Home") {
      e.preventDefault();
      items[0]?.focus();
    } else if (e.key === "End") {
      e.preventDefault();
      items[items.length - 1]?.focus();
    } else if (e.key === "Escape") {
      e.preventDefault();
      onClose?.();
    }
  };

  return (
    <div
      ref={containerRef}
      role="menu"
      aria-label="Message overflow menu"
      onKeyDown={handleKeyDown}
      className="bg-shell-surface border border-white/10 rounded-md shadow-lg py-1 min-w-[160px] text-sm"
    >
      {isOwn && (
        <button role="menuitem" onClick={onEdit}
          className="block w-full text-left px-3 py-1.5 hover:bg-white/5 focus:bg-white/5 focus:outline-none">Edit</button>
      )}
      {isOwn && (
        <button role="menuitem" onClick={onDelete}
          className="block w-full text-left px-3 py-1.5 hover:bg-white/5 focus:bg-white/5 focus:outline-none text-red-300">Delete</button>
      )}
      <button role="menuitem" onClick={onCopyLink}
        className="block w-full text-left px-3 py-1.5 hover:bg-white/5 focus:bg-white/5 focus:outline-none">Copy link</button>
      {onCopyText && <button role="menuitem" onClick={onCopyText}
        className="block w-full text-left px-3 py-1.5 hover:bg-white/5 focus:bg-white/5 focus:outline-none">Copy text</button>}
      {isHuman && (
        <button role="menuitem" onClick={onPin}
          className="block w-full text-left px-3 py-1.5 hover:bg-white/5 focus:bg-white/5 focus:outline-none">
          {isPinned ? "Unpin" : "Pin"}
        </button>
      )}
      <button role="menuitem" onClick={onMarkUnread}
        className="block w-full text-left px-3 py-1.5 hover:bg-white/5 focus:bg-white/5 focus:outline-none">Mark unread</button>
    </div>
  );
}
