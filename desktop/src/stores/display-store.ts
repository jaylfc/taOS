import { create } from "zustand";

// DISPLAY SCALE, macOS "Displays > Scaled" style.
//
// PER DEVICE, NOT PER ACCOUNT (Jay, 2026-08-06). This is deliberately
// localStorage only and is NEVER sent to /api/desktop/settings or
// /api/preferences/themes, both of which theme-store uses for account-synced
// preferences. A phone and a 4K monitor want different scales for the same
// user, so syncing this to the account would actively fight the feature. If a
// future change adds account sync for appearance settings, this key must be
// excluded explicitly rather than by omission.
//
// Discrete steps rather than a free slider, again matching macOS: a slider
// invites values that land on fractional device pixels and blur text, and it
// gives no vocabulary for "the default one".

const SCALE_KEY = "taos.display.uiScale";

export interface ScaleStep {
  value: number;
  /** Short label for the control. */
  label: string;
  /** macOS-style end captions; only the extremes carry one. */
  caption?: string;
}

// Ordered smallest-content-first so the control reads left-to-right like the
// macOS pane: "Larger Text" on the left, "More Space" on the right.
export const SCALE_STEPS: ScaleStep[] = [
  { value: 1.25, label: "125%", caption: "Larger Text" },
  { value: 1.1, label: "110%" },
  { value: 1.0, label: "100%" },
  { value: 0.9, label: "90%" },
  { value: 0.8, label: "80%", caption: "More Space" },
];

export const DEFAULT_SCALE = 1.0;

const MIN_SCALE = 0.8;
const MAX_SCALE = 1.25;

/**
 * Read the persisted scale, clamped to the supported range.
 *
 * Anything unparseable, out of range, or absent falls back to DEFAULT_SCALE.
 * A stored value that is not a usable number is treated as "no preference"
 * rather than as zero: a zero or NaN scale would render an invisible or
 * collapsed desktop, and the recovery path for that is hard to find precisely
 * because the UI you would use to fix it is the one that broke.
 */
export function readStoredScale(): number {
  let raw: string | null = null;
  try {
    raw = localStorage.getItem(SCALE_KEY);
  } catch {
    // Private-mode / disabled storage: fall back to the default rather than throw.
    return DEFAULT_SCALE;
  }
  if (raw === null) return DEFAULT_SCALE;
  const parsed = Number(raw);
  if (!Number.isFinite(parsed)) return DEFAULT_SCALE;
  if (parsed < MIN_SCALE || parsed > MAX_SCALE) return DEFAULT_SCALE;
  return parsed;
}

/**
 * Apply the scale to the document root.
 *
 * Uses CSS `zoom` rather than `transform: scale()` on purpose. A transform
 * establishes a containing block for fixed-position descendants and shifts
 * pointer hit-testing, both of which a windowing shell depends on: menus,
 * the dock and drag handles would all land in the wrong place. `zoom` changes
 * the used value of lengths instead, so layout, hit-testing and scrollbars all
 * stay consistent.
 */
export function applyScale(scale: number): void {
  if (typeof document === "undefined") return;
  const root = document.documentElement;
  if (scale === DEFAULT_SCALE) {
    // Remove rather than set "1": an unset property keeps the cascade clean and
    // makes "is a scale in effect?" answerable by inspecting the element.
    root.style.removeProperty("zoom");
  } else {
    root.style.setProperty("zoom", String(scale));
  }
  // Exposed as a custom property so CSS and the effective-viewport helper have
  // a single source of truth rather than re-reading the inline style.
  root.style.setProperty("--taos-ui-scale", String(scale));
}

interface DisplayStore {
  uiScale: number;
  setUiScale: (scale: number) => void;
}

export const useDisplayStore = create<DisplayStore>((set) => ({
  uiScale: readStoredScale(),
  setUiScale: (scale: number) => {
    const clamped =
      Number.isFinite(scale) && scale >= MIN_SCALE && scale <= MAX_SCALE ? scale : DEFAULT_SCALE;
    try {
      localStorage.setItem(SCALE_KEY, String(clamped));
    } catch {
      // Storage failure must not block applying the scale for this session.
    }
    applyScale(clamped);
    set({ uiScale: clamped });
  },
}));

/**
 * Apply the stored scale at boot, before first paint where possible.
 *
 * Called from the app entry rather than from a component so the scale is in
 * effect for the initial layout: applying it after mount produces a visible
 * reflow on every load.
 */
export function initDisplayScale(): void {
  applyScale(readStoredScale());
}
