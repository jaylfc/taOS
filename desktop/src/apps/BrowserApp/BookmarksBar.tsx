/**
 * BrowserApp v2 — BookmarksBar.
 *
 * Horizontal row of bookmark chips shown below the tab strip when the
 * active profile has at least one bookmark. Auto-hides when empty.
 *
 * Each chip:
 *  - Favicon (Google S2 service, 32px)
 *  - Title truncated to ~20 characters
 *  - Click → navigates active tab
 *  - Right-click → context menu with "Remove bookmark"
 */
import { useEffect, useRef, useState } from "react";
import { listBookmarks, removeBookmark, type Bookmark } from "@/lib/browser-bookmarks-api";
import { useBrowserStore } from "@/stores/browser-store";

interface BookmarksBarProps {
  windowId: string;
  profileId: string;
}

function faviconUrl(url: string): string {
  try {
    const { hostname } = new URL(url);
    return `https://www.google.com/s2/favicons?domain=${encodeURIComponent(hostname)}&sz=32`;
  } catch {
    return "";
  }
}

function truncate(text: string, max = 20): string {
  if (text.length <= max) return text;
  return text.slice(0, max) + "…";
}

interface ContextMenuState {
  x: number;
  y: number;
  bookmarkId: string;
}

export function BookmarksBar({ windowId, profileId }: BookmarksBarProps) {
  const navigateTab = useBrowserStore((s) => s.navigateTab);
  const win = useBrowserStore((s) => s.windows[windowId]);

  const [bookmarks, setBookmarks] = useState<Bookmark[]>([]);
  const [contextMenu, setContextMenu] = useState<ContextMenuState | null>(null);
  const menuRef = useRef<HTMLDivElement | null>(null);
  const loadSeqRef = useRef(0);

  async function load() {
    const seq = ++loadSeqRef.current;
    const list = await listBookmarks(profileId);
    if (seq !== loadSeqRef.current) return;
    setBookmarks(list);
  }

  useEffect(() => {
    load();
  }, [profileId]);

  // React to bookmark-changed events fired by other components (AddressBar star,
  // etc.) so the bar stays in sync. Skip events we dispatched ourselves.
  useEffect(() => {
    const handler = (e: Event) => {
      const ce = e as CustomEvent<{ profileId: string; url: string; bookmarkId: string | null; source?: string }>;
      if (ce.detail.profileId !== profileId) return;
      if (ce.detail.source === "bookmarks-bar") return;
      load();
    };
    window.addEventListener("taos-browser:bookmark-changed", handler);
    return () => window.removeEventListener("taos-browser:bookmark-changed", handler);
  }, [profileId]);

  // Dismiss context menu on outside click
  useEffect(() => {
    if (!contextMenu) return;
    const handler = (e: MouseEvent) => {
      if (!menuRef.current?.contains(e.target as Node)) {
        setContextMenu(null);
      }
    };
    window.addEventListener("mousedown", handler);
    return () => window.removeEventListener("mousedown", handler);
  }, [contextMenu]);

  if (bookmarks.length === 0) return null;

  const activeTabId = win?.activeTabId;

  function handleChipClick(url: string) {
    if (!activeTabId) return;
    navigateTab(windowId, activeTabId, url);
  }

  function handleChipContextMenu(e: React.MouseEvent, bookmarkId: string) {
    e.preventDefault();
    setContextMenu({ x: e.clientX, y: e.clientY, bookmarkId });
  }

  async function handleRemove(bookmarkId: string) {
    setContextMenu(null);
    const removed = bookmarks.find((b) => b.bookmark_id === bookmarkId);
    const ok = await removeBookmark(profileId, bookmarkId);
    if (ok) {
      // Update local state immediately — don't wait for a re-fetch
      setBookmarks((prev) => prev.filter((b) => b.bookmark_id !== bookmarkId));
      // Notify other components (AddressBar star icon, etc.) but mark the
      // event as originating here so our own listener doesn't trigger a reload
      window.dispatchEvent(
        new CustomEvent("taos-browser:bookmark-changed", {
          detail: {
            profileId,
            url: removed?.url ?? "",
            bookmarkId: null,
            source: "bookmarks-bar",
          },
        }),
      );
    }
  }

  return (
    <>
      <div
        role="toolbar"
        aria-label="Bookmarks bar"
        className="flex items-center gap-1 px-2 h-8 bg-shell-surface border-b border-shell-border-subtle overflow-x-auto"
        style={{ scrollbarWidth: "none" }}
      >
        {bookmarks.map((bm) => (
          <button
            key={bm.bookmark_id}
            type="button"
            aria-label={`Go to ${bm.title}`}
            onClick={() => handleChipClick(bm.url)}
            onContextMenu={(e) => handleChipContextMenu(e, bm.bookmark_id)}
            className="flex items-center gap-1 px-2 py-0.5 rounded bg-shell-bg-deep border border-shell-border-subtle text-xs hover:bg-shell-hover whitespace-nowrap flex-shrink-0"
          >
            {faviconUrl(bm.url) && (
              <img
                src={faviconUrl(bm.url)}
                alt=""
                aria-hidden="true"
                width={14}
                height={14}
                className="w-3.5 h-3.5 object-contain"
              />
            )}
            <span>{truncate(bm.title)}</span>
          </button>
        ))}
      </div>

      {contextMenu && (
        <div
          ref={menuRef}
          role="menu"
          aria-label="Bookmark actions"
          className="fixed z-[80] rounded border border-shell-border-subtle bg-shell-surface shadow-lg py-1 text-xs"
          style={{ top: contextMenu.y, left: contextMenu.x }}
        >
          <button
            type="button"
            role="menuitem"
            aria-label="Remove bookmark"
            onClick={() => handleRemove(contextMenu.bookmarkId)}
            className="w-full text-left px-3 py-1.5 hover:bg-shell-hover text-shell-text"
          >
            Remove bookmark
          </button>
        </div>
      )}
    </>
  );
}
