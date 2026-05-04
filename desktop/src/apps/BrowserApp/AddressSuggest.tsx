/**
 * Popover anchored to AddressBar input. Renders local-only autocomplete
 * suggestions from /api/desktop/browser/suggest. Keyboard nav (arrows + Enter).
 */
import { Globe, History, Star } from "lucide-react";
import { useEffect, useRef } from "react";
import type { Suggestion } from "@/lib/browser-suggest-api";

interface AddressSuggestProps {
  suggestions: Suggestion[];
  selectedIndex: number;
  onSelect: (s: Suggestion) => void;
  onHighlight: (index: number) => void;
}

const SOURCE_ICON: Record<Suggestion["source"], typeof Globe> = {
  history: History,
  bookmark: Star,
  "open-tab": Globe,
};

export function AddressSuggest({
  suggestions,
  selectedIndex,
  onSelect,
  onHighlight,
}: AddressSuggestProps) {
  const containerRef = useRef<HTMLDivElement | null>(null);

  // Scroll the highlighted entry into view when it changes
  useEffect(() => {
    if (selectedIndex < 0) return;
    const el = containerRef.current?.querySelector(
      `[data-suggest-index="${selectedIndex}"]`,
    ) as HTMLElement | null;
    el?.scrollIntoView({ block: "nearest" });
  }, [selectedIndex]);

  if (suggestions.length === 0) return null;

  return (
    <div
      role="listbox"
      ref={containerRef}
      aria-label="Address suggestions"
      className="absolute left-0 right-0 top-full mt-1 max-h-[280px] overflow-auto rounded border border-shell-border bg-shell-surface shadow-lg z-50"
    >
      {suggestions.map((s, i) => {
        const Icon = SOURCE_ICON[s.source] ?? Globe;
        const isSelected = i === selectedIndex;
        return (
          <div
            key={`${s.source}-${s.url}-${i}`}
            role="option"
            aria-selected={isSelected}
            data-suggest-index={i}
            tabIndex={-1}
            onMouseEnter={() => onHighlight(i)}
            onMouseDown={(e) => {
              // mousedown fires before blur on the input; preventDefault
              // keeps the input focused so the subsequent click still fires.
              e.preventDefault();
            }}
            onClick={() => onSelect(s)}
            className={[
              "w-full text-left px-2 py-1 flex items-center gap-2 text-xs cursor-pointer",
              isSelected
                ? "bg-shell-hover"
                : "hover:bg-shell-hover/50",
            ].join(" ")}
          >
            <Icon size={12} className="opacity-70 shrink-0" aria-hidden="true" />
            <span className="truncate flex-1">{s.title || s.url}</span>
            <span className="text-shell-text-tertiary text-[10px] truncate max-w-[200px]">
              {s.url}
            </span>
          </div>
        );
      })}
    </div>
  );
}
