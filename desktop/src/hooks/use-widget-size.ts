import { useRef, useState, useEffect, RefObject } from "react";

export type SizeTier = "s" | "m" | "l";

export interface WidgetSize {
  width: number;
  height: number;
  tier: SizeTier;
}

// Grid constants (from WidgetLayer.tsx):  rowHeight=72, margin=[16,16]
// row pixel height: h*72 + (h-1)*16  →  h=2 → 160, h=3 → 248, h=4 → 336
const SMALL_MAX_H = 175;   // up to h=2 cells
const MEDIUM_MAX_H = 290;  // up to h=3 cells

function toTier(height: number): SizeTier {
  if (height <= SMALL_MAX_H) return "s";
  if (height <= MEDIUM_MAX_H) return "m";
  return "l";
}

export function useWidgetSize(): [RefObject<HTMLDivElement>, WidgetSize] {
  const ref = useRef<HTMLDivElement>(null);
  const [size, setSize] = useState<WidgetSize>({ width: 200, height: 200, tier: "m" });

  useEffect(() => {
    const el = ref.current;
    if (!el) return;

    const update = (w: number, h: number) => {
      setSize({ width: w, height: h, tier: toTier(h) });
    };

    // Initial measurement
    update(el.offsetWidth, el.offsetHeight);

    const observer = new ResizeObserver((entries) => {
      for (const entry of entries) {
        update(entry.contentRect.width, entry.contentRect.height);
      }
    });
    observer.observe(el);
    return () => observer.disconnect();
  }, []);

  return [ref as RefObject<HTMLDivElement>, size];
}
