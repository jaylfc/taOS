import { create } from "zustand";

export interface Widget {
  id: string;
  type: string;
  x: number;
  y: number;
  w: number;
  h: number;
  minW?: number;
  minH?: number;
  config?: Record<string, unknown>;
}

interface WidgetStore {
  widgets: Widget[];
  showWidgets: boolean;
  hydrated: boolean;
  addWidget: (type: string) => void;
  removeWidget: (id: string) => void;
  updateLayout: (layouts: { id: string; x: number; y: number; w: number; h: number }[]) => void;
  toggleWidgets: () => void;
}

const WIDGET_DEFAULTS: Record<string, { w: number; h: number; minW?: number; minH?: number }> = {
  clock:          { w: 3, h: 2, minW: 2, minH: 2 },
  "agent-status": { w: 4, h: 3, minW: 2, minH: 2 },
  "quick-notes":  { w: 4, h: 4, minW: 2, minH: 2 },
  "system-stats": { w: 3, h: 3, minW: 2, minH: 2 },
  weather:        { w: 3, h: 3, minW: 2, minH: 2 },
};

let nextId = 1;

function makeId(): string {
  return `widget-${Date.now()}-${nextId++}`;
}

function findOpenSpot(widgets: Widget[], w: number, h: number): { x: number; y: number } {
  const cols = 12;
  for (let row = 0; row < 100; row++) {
    for (let col = 0; col <= cols - w; col++) {
      const overlaps = widgets.some(
        (wg) =>
          col < wg.x + wg.w &&
          col + w > wg.x &&
          row < wg.y + wg.h &&
          row + h > wg.y,
      );
      if (!overlaps) return { x: col, y: row };
    }
  }
  return { x: 0, y: 0 };
}

// Server-persistence wiring. Widget layout + visibility live in a single
// namespaced preference so they follow the user across devices. The
// localStorage mirror avoids a flash of default widgets on page load while
// the network round-trip is in flight.

const PREF_KEY = "widgets";
const CACHE_KEY = `taos-pref:${PREF_KEY}`;
const SAVE_DEBOUNCE_MS = 500;

const DEFAULT_WIDGETS: Widget[] = [
  { id: "default-clock", type: "clock", x: 0, y: 0, w: 3, h: 2 },
  { id: "default-agents", type: "agent-status", x: 3, y: 0, w: 4, h: 3 },
];

type PersistedShape = { widgets: Widget[]; showWidgets: boolean };

function readCache(): PersistedShape | null {
  try {
    const raw = localStorage.getItem(CACHE_KEY);
    if (!raw) return null;
    const parsed = JSON.parse(raw) as PersistedShape;
    if (Array.isArray(parsed?.widgets) && typeof parsed?.showWidgets === "boolean") {
      return parsed;
    }
    return null;
  } catch {
    return null;
  }
}

function writeCache(value: PersistedShape): void {
  try {
    localStorage.setItem(CACHE_KEY, JSON.stringify(value));
  } catch {
    // quota / private mode — not fatal, the server is authoritative
  }
}

const initial = readCache();

export const useWidgetStore = create<WidgetStore>((set, get) => ({
  widgets: initial?.widgets ?? DEFAULT_WIDGETS,
  // Off by default until the widgets are overhauled/redesigned. A user who has
  // already enabled them keeps their choice (persisted showWidgets is restored).
  showWidgets: initial?.showWidgets ?? false,
  hydrated: false,

  addWidget(type) {
    const defaults = WIDGET_DEFAULTS[type] ?? { w: 3, h: 2 };
    const pos = findOpenSpot(get().widgets, defaults.w, defaults.h);
    const widget: Widget = {
      id: makeId(),
      type,
      x: pos.x,
      y: pos.y,
      w: defaults.w,
      h: defaults.h,
      minW: defaults.minW,
      minH: defaults.minH,
    };
    set((s) => ({ widgets: [...s.widgets, widget] }));
  },

  removeWidget(id) {
    set((s) => ({ widgets: s.widgets.filter((w) => w.id !== id) }));
  },

  updateLayout(layouts) {
    set((s) => ({
      widgets: s.widgets.map((w) => {
        const update = layouts.find((l) => l.id === w.id);
        if (!update) return w;
        return { ...w, x: update.x, y: update.y, w: update.w, h: update.h };
      }),
    }));
  },

  toggleWidgets() {
    set((s) => ({ showWidgets: !s.showWidgets }));
  },
}));

// Hydrate from the server once on module load. The localStorage mirror
// renders instantly; the server fetch only patches state if the server
// genuinely has different data (e.g. the user changed things on another
// device). Replacing the array reference unnecessarily makes the grid
// re-layout visibly — every reload looked like a "reload" of the
// widgets, even when nothing had changed.
function shapesEqual(a: PersistedShape | null, b: Partial<PersistedShape>): boolean {
  if (!a || !Array.isArray(b.widgets)) return false;
  if (a.showWidgets !== b.showWidgets) return false;
  if (a.widgets.length !== b.widgets.length) return false;
  // Order-sensitive deep compare on the fields the grid uses for layout.
  for (let i = 0; i < a.widgets.length; i++) {
    const x = a.widgets[i];
    const y = b.widgets[i];
    if (!x || !y) return false;
    if (
      x.id !== y.id ||
      x.type !== y.type ||
      x.x !== y.x || x.y !== y.y ||
      x.w !== y.w || x.h !== y.h
    ) {
      return false;
    }
  }
  return true;
}

(async () => {
  try {
    const resp = await fetch(`/api/preferences/${PREF_KEY}`);
    if (!resp.ok) {
      useWidgetStore.setState({ hydrated: true });
      return;
    }
    const blob = (await resp.json()) as Partial<PersistedShape>;
    const hasServerLayout =
      blob && Array.isArray(blob.widgets) && typeof blob.showWidgets === "boolean";
    if (hasServerLayout) {
      const serverShape: PersistedShape = {
        widgets: blob.widgets!,
        showWidgets: blob.showWidgets!,
      };
      // Compare against whatever's currently in the store (cache OR
      // defaults). Only setState if the server actually differs — same
      // data should not trigger a grid re-layout.
      const current: PersistedShape = {
        widgets: useWidgetStore.getState().widgets,
        showWidgets: useWidgetStore.getState().showWidgets,
      };
      if (shapesEqual(current, serverShape)) {
        useWidgetStore.setState({ hydrated: true });
      } else {
        useWidgetStore.setState({
          widgets: serverShape.widgets,
          showWidgets: serverShape.showWidgets,
          hydrated: true,
        });
      }
      writeCache(serverShape);
    } else {
      useWidgetStore.setState({ hydrated: true });
    }
  } catch {
    useWidgetStore.setState({ hydrated: true });
  }
})();

// Debounced persistence. We subscribe to every store change after hydration
// and push the new shape to the server. localStorage is written immediately
// so a hard reload picks up the latest state even if the network PUT is
// still in flight.
let saveTimer: ReturnType<typeof setTimeout> | null = null;

useWidgetStore.subscribe((state) => {
  if (!state.hydrated) return; // don't write back the default shape before we've loaded
  const payload: PersistedShape = {
    widgets: state.widgets,
    showWidgets: state.showWidgets,
  };
  writeCache(payload);
  if (saveTimer !== null) clearTimeout(saveTimer);
  saveTimer = setTimeout(() => {
    saveTimer = null;
    fetch(`/api/preferences/${PREF_KEY}`, {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    }).catch(() => {
      // best-effort; the cache has the value and we'll try again on the
      // next change
    });
  }, SAVE_DEBOUNCE_MS);
});
