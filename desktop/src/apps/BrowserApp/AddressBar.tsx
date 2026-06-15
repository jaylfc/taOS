/**
 * URL input for the active tab. Maintains its own input state separate
 * from the tab's URL so editing doesn't immediately navigate. Enter
 * commits; Esc reverts.
 *
 * Also hosts the AddressSuggest popover, which fetches local-only
 * autocomplete (debounced 150ms).
 *
 * Special prefix handling (PR 4 stubs; PR 5/6 wires real behavior):
 *  - "@..." → genuine no-op; commitNavigation early-returns (PR 6 will wire)
 *  - "!..." → genuine no-op; commitNavigation early-returns (PR 5 will wire)
 *  - text with no "." → search query, prepend search-engine URL
 *
 * Default search engine: DuckDuckGo. Per-user override lands in PR 5
 * Settings.
 */
import { useEffect, useRef, useState } from "react";
import { BookOpen, Star } from "lucide-react";
import { useBrowserStore } from "@/stores/browser-store";
import { useBrowserSettingsStore, searchUrlFor } from "@/stores/browser-settings-store";
import { fetchSuggestions, type Suggestion } from "@/lib/browser-suggest-api";
import { extractReadable } from "@/lib/browser-extract-api";
import { listBookmarks, addBookmark, removeBookmark } from "@/lib/browser-bookmarks-api";
import { AddressSuggest } from "./AddressSuggest";

const SUGGEST_DEBOUNCE_MS = 150;
export const READER_MIN_WORD_COUNT = 200;

interface AddressBarProps {
  windowId: string;
}

export function AddressBar({ windowId }: AddressBarProps) {
  const win = useBrowserStore((s) => s.windows[windowId]);
  const navigateTab = useBrowserStore((s) => s.navigateTab);

  const activeTab = win?.tabs.find((t) => t.id === win?.activeTabId);

  const setTabReader = useBrowserStore((s) => s.setTabReader);

  const [inputValue, setInputValue] = useState(activeTab?.url ?? "");
  const [suggestions, setSuggestions] = useState<Suggestion[]>([]);
  const [selectedIndex, setSelectedIndex] = useState(-1);
  const [hasFocus, setHasFocus] = useState(false);
  const inputRef = useRef<HTMLInputElement | null>(null);
  const suggestTimeout = useRef<ReturnType<typeof setTimeout> | null>(null);
  // Guard against duplicate in-flight extract requests for the same URL
  const inflightUrlRef = useRef<string | null>(null);

  // Bookmark state: null = not bookmarked, { id } = bookmarked
  const [bookmarked, setBookmarked] = useState<{ id: string } | null>(null);
  // url → bookmark_id cache so tab switches don't re-fetch
  const bookmarksRef = useRef<Map<string, string>>(new Map());
  // Single-flight guard for toggleBookmark
  const pendingRef = useRef(false);

  // Focus the address bar when Cmd+L fires for this window
  useEffect(() => {
    const handler = (e: Event) => {
      const ce = e as CustomEvent<{ windowId: string }>;
      if (ce.detail?.windowId !== windowId) return;
      inputRef.current?.focus();
      inputRef.current?.select();
    };
    window.addEventListener("taos-browser:focus-address", handler);
    return () => window.removeEventListener("taos-browser:focus-address", handler);
  }, [windowId]);

  // Sync input when the active tab's URL changes from outside (back/forward,
  // tab switch). Only when the input isn't focused — don't clobber typing.
  useEffect(() => {
    if (!hasFocus && activeTab) setInputValue(activeTab.url);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [activeTab?.id, activeTab?.url]);

  // Debounced suggest fetch
  useEffect(() => {
    if (!hasFocus) return;
    if (suggestTimeout.current) clearTimeout(suggestTimeout.current);
    if (!inputValue.trim() || inputValue.startsWith("@") || inputValue.startsWith("!")) {
      setSuggestions([]);
      return;
    }
    suggestTimeout.current = setTimeout(async () => {
      const results = await fetchSuggestions(
        win?.profileId ?? "personal",
        inputValue,
      );
      setSuggestions(results);
      setSelectedIndex(-1);
    }, SUGGEST_DEBOUNCE_MS);
    return () => {
      if (suggestTimeout.current) clearTimeout(suggestTimeout.current);
    };
  }, [inputValue, hasFocus, win?.profileId]);

  // Populate (or repopulate on profile change) the bookmarks cache from the API.
  // Clears the map first so stale entries from the previous profile can't bleed through.
  useEffect(() => {
    if (!win?.profileId) return;
    bookmarksRef.current.clear();
    setBookmarked(null);
    let cancelled = false;
    listBookmarks(win.profileId).then((bms) => {
      if (cancelled) return;
      for (const bm of bms) {
        bookmarksRef.current.set(bm.url, bm.bookmark_id);
      }
      // Re-evaluate current tab's bookmark state
      if (activeTab?.url && activeTab.url !== "about:blank") {
        const id = bookmarksRef.current.get(activeTab.url);
        setBookmarked(id ? { id } : null);
      }
    });
    return () => { cancelled = true; };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [win?.profileId]);

  // Keep star in sync when active tab URL changes
  useEffect(() => {
    if (!activeTab?.url || activeTab.url === "about:blank") {
      setBookmarked(null);
      return;
    }
    const id = bookmarksRef.current.get(activeTab.url);
    setBookmarked(id ? { id } : null);
  }, [activeTab?.url]);

  // Cross-window bookmark sync: when another AddressBar instance toggles a
  // bookmark, propagate the change into this cache so the star stays accurate.
  useEffect(() => {
    if (!win?.profileId) return;
    const handler = (e: Event) => {
      const ce = e as CustomEvent<{ profileId: string; url: string; bookmarkId: string | null }>;
      if (ce.detail.profileId !== win?.profileId) return;
      if (ce.detail.bookmarkId) {
        bookmarksRef.current.set(ce.detail.url, ce.detail.bookmarkId);
      } else {
        bookmarksRef.current.delete(ce.detail.url);
      }
      if (activeTab?.url === ce.detail.url) {
        setBookmarked(ce.detail.bookmarkId ? { id: ce.detail.bookmarkId } : null);
      }
    };
    window.addEventListener("taos-browser:bookmark-changed", handler);
    return () => window.removeEventListener("taos-browser:bookmark-changed", handler);
  }, [win?.profileId, activeTab?.url]);

  if (!win || !activeTab) return null;

  async function toggleBookmark() {
    if (!activeTab?.url || activeTab.url === "about:blank" || !win?.profileId) return;
    if (pendingRef.current) return;
    pendingRef.current = true;
    try {
      if (bookmarked) {
        const ok = await removeBookmark(win.profileId, bookmarked.id);
        if (ok) {
          bookmarksRef.current.delete(activeTab.url);
          setBookmarked(null);
          window.dispatchEvent(new CustomEvent("taos-browser:bookmark-changed", {
            detail: { profileId: win.profileId, url: activeTab.url, bookmarkId: null },
          }));
        }
      } else {
        const id = await addBookmark(win.profileId, activeTab.url, activeTab.title || activeTab.url);
        if (id) {
          bookmarksRef.current.set(activeTab.url, id);
          setBookmarked({ id });
          window.dispatchEvent(new CustomEvent("taos-browser:bookmark-changed", {
            detail: { profileId: win.profileId, url: activeTab.url, bookmarkId: id },
          }));
        }
      }
    } finally {
      pendingRef.current = false;
    }
  }

  function commitNavigation(target: string) {
    const trimmed = target.trim();
    if (!trimmed) return;
    // @<agent> and !<profile> prefixes are reserved for PR 5/6.
    // PR 4 makes them genuine no-ops to prevent failed navigations
    // (and to defend against constructions like "@javascript:alert(1)").
    if (trimmed.startsWith("@") || trimmed.startsWith("!")) return;
    if (!activeTab) return;
    const finalUrl = resolveFinalUrl(trimmed);
    navigateTab(windowId, activeTab.id, finalUrl);
    setSuggestions([]);
    setSelectedIndex(-1);
  }

  return (
    <div className="relative flex-1">
      <input
        ref={inputRef}
        type="text"
        aria-label="Address"
        value={inputValue}
        onChange={(e) => setInputValue(e.target.value)}
        onFocus={(e) => {
          setHasFocus(true);
          // Select all on focus (Safari / Chrome behavior)
          e.currentTarget.select();
          // Lazy reader extract — fire once per URL when the address bar is opened
          if (
            activeTab &&
            activeTab.readerAvailable === undefined &&
            !activeTab.readerExtract &&
            /^https?:\/\//i.test(activeTab.url) &&
            inflightUrlRef.current !== activeTab.url
          ) {
            const targetUrl = activeTab.url;
            inflightUrlRef.current = targetUrl;
            extractReadable(win.profileId, targetUrl)
              .then((result) => {
                const currentTab = useBrowserStore
                  .getState()
                  .windows[windowId]
                  ?.tabs.find((t) => t.id === activeTab.id);
                if (!currentTab || currentTab.url !== targetUrl) return;
                if (result) {
                  setTabReader(windowId, activeTab.id, {
                    readerAvailable: result.word_count > READER_MIN_WORD_COUNT,
                    readerExtract: result,
                  });
                } else {
                  setTabReader(windowId, activeTab.id, { readerAvailable: false });
                }
              })
              .catch(() => {
                // Silent — match other browser-* api wrappers
              })
              .finally(() => {
                if (inflightUrlRef.current === targetUrl) {
                  inflightUrlRef.current = null;
                }
              });
          }
        }}
        onBlur={() => {
          setHasFocus(false);
          // Defer hide so click on suggestion fires first
          setTimeout(() => setSuggestions([]), 100);
        }}
        onKeyDown={(e) => {
          if (e.key === "Enter") {
            e.preventDefault();
            if (selectedIndex >= 0 && suggestions[selectedIndex]) {
              commitNavigation(suggestions[selectedIndex].url);
            } else {
              commitNavigation(inputValue);
            }
          } else if (e.key === "Escape") {
            setInputValue(activeTab.url);
            setSuggestions([]);
            (e.target as HTMLInputElement).blur();
          } else if (e.key === "ArrowDown") {
            e.preventDefault();
            setSelectedIndex((i) =>
              Math.min(i + 1, suggestions.length - 1),
            );
          } else if (e.key === "ArrowUp") {
            e.preventDefault();
            setSelectedIndex((i) => Math.max(i - 1, -1));
          }
        }}
        className={`w-full bg-transparent text-[13px] text-shell-text placeholder:text-shell-text-tertiary focus:outline-none ${
          activeTab?.readerAvailable && activeTab.url !== "about:blank"
            ? "pr-14"
            : activeTab?.readerAvailable || activeTab?.url !== "about:blank"
            ? "pr-7"
            : ""
        }`}
      />
      {activeTab?.url && activeTab.url !== "about:blank" && (
        <button
          type="button"
          aria-label={bookmarked ? "Remove bookmark" : "Add bookmark"}
          aria-pressed={!!bookmarked}
          onClick={toggleBookmark}
          className={`absolute top-1/2 -translate-y-1/2 p-0.5 rounded ${
            activeTab?.readerAvailable ? "right-7" : "right-1.5"
          } ${
            bookmarked
              ? "text-accent"
              : "text-shell-text-secondary hover:text-shell-text"
          }`}
        >
          <Star size={12} fill={bookmarked ? "currentColor" : "none"} />
        </button>
      )}
      {activeTab?.readerAvailable && (
        <button
          type="button"
          aria-label="Toggle Reader mode"
          aria-pressed={!!activeTab?.readerActive}
          onClick={() => {
            if (!activeTab) return;
            setTabReader(windowId, activeTab.id, {
              readerActive: !activeTab.readerActive,
            });
          }}
          className={`absolute right-1.5 top-1/2 -translate-y-1/2 p-0.5 rounded ${
            activeTab?.readerActive
              ? "text-accent"
              : "text-shell-text-secondary hover:text-shell-text"
          }`}
        >
          <BookOpen size={12} />
        </button>
      )}
      {hasFocus && (
        <AddressSuggest
          suggestions={suggestions}
          selectedIndex={selectedIndex}
          onSelect={(s) => commitNavigation(s.url)}
          onHighlight={setSelectedIndex}
        />
      )}
    </div>
  );
}

/**
 * Decide the final URL to navigate to.
 * - already a URL → as-is
 * - starts with @ or ! → no-op (PR 5/6 will wire)
 * - looks like a domain (has a dot) → prepend https://
 * - otherwise → treat as search query, prepend search engine URL
 */
function resolveFinalUrl(input: string): string {
  if (input.startsWith("@") || input.startsWith("!")) {
    // Defensive: commitNavigation should have early-returned for these.
    // If we get here it's a logic bug — return as-is to fail loudly downstream.
    return input;
  }
  if (/^https?:\/\//i.test(input)) return input;
  if (input.includes(".") && !input.includes(" ")) {
    return `https://${input}`;
  }
  return searchUrlFor(useBrowserSettingsStore.getState().searchEngine, input);
}
