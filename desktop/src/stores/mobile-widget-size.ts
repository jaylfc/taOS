import { useEffect, useState } from "react";

export type WidgetSize = "half" | "full";

export const SIDE_WIDGETS = ["clock", "system-stats", "weather"] as const;

export function isSideWidget(t: string): boolean {
  return (SIDE_WIDGETS as readonly string[]).includes(t);
}

const KEY = "taos-mobile-widget-size";
const EVENT = "taos-mobile-widget-size";

function readAll(): Record<string, unknown> {
  try {
    const raw = localStorage.getItem(KEY);
    if (!raw) return {};
    const parsed: unknown = JSON.parse(raw);
    if (parsed && typeof parsed === "object" && !Array.isArray(parsed)) {
      return parsed as Record<string, unknown>;
    }
  } catch {
    // storage unavailable or corrupt: fall back to defaults
  }
  return {};
}

export function getWidgetSize(t: string): WidgetSize {
  return readAll()[t] === "full" ? "full" : "half";
}

export function setWidgetSize(t: string, size: WidgetSize): void {
  try {
    localStorage.setItem(KEY, JSON.stringify({ ...readAll(), [t]: size }));
  } catch {
    // storage unavailable: the change simply does not persist
  }
  window.dispatchEvent(new Event(EVENT));
}

export function useWidgetSize(t: string): WidgetSize {
  const [size, setSize] = useState<WidgetSize>(() => getWidgetSize(t));
  useEffect(() => {
    const update = () => setSize(getWidgetSize(t));
    update();
    window.addEventListener(EVENT, update);
    window.addEventListener("storage", update);
    return () => {
      window.removeEventListener(EVENT, update);
      window.removeEventListener("storage", update);
    };
  }, [t]);
  return size;
}
