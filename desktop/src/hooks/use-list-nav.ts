import { useState, useCallback, type KeyboardEvent } from "react";

export function computeNextIndex(current: number, total: number, key: string): number {
  if (total === 0) return -1;
  if (key === "ArrowDown") return (current + 1) % total;
  if (key === "ArrowUp") return (current - 1 + total) % total;
  if (key === "Home") return 0;
  if (key === "End") return total - 1;
  return current;
}

export function useListNav<T>(items: T[], onSelect: (item: T) => void) {
  const [selectedIndex, setSelectedIndex] = useState(0);

  const onKeyDown = useCallback(
    (e: KeyboardEvent) => {
      if (["ArrowDown", "ArrowUp", "Home", "End"].includes(e.key)) {
        e.preventDefault();
        setSelectedIndex((prev) => computeNextIndex(prev, items.length, e.key));
      } else if (e.key === "Enter" || e.key === " ") {
        e.preventDefault();
        if (items[selectedIndex]) onSelect(items[selectedIndex]);
      }
    },
    [items, selectedIndex, onSelect],
  );

  return { selectedIndex, setSelectedIndex, onKeyDown };
}
