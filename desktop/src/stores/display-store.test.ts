import { beforeEach, afterEach, describe, expect, it, vi } from "vitest";
import {
  useDisplayStore,
  readStoredScale,
  applyScale,
  DEFAULT_SCALE,
  SCALE_STEPS,
} from "./display-store";

const SCALE_KEY = "taos.display.uiScale";

beforeEach(() => {
  localStorage.clear();
  document.documentElement.removeAttribute("style");
  useDisplayStore.setState({ uiScale: DEFAULT_SCALE });
});

afterEach(() => {
  vi.restoreAllMocks();
});

describe("display-store — persistence", () => {
  it("defaults to 100% when nothing is stored", () => {
    expect(readStoredScale()).toBe(DEFAULT_SCALE);
  });

  it("persists the chosen scale to localStorage", () => {
    useDisplayStore.getState().setUiScale(0.8);
    expect(localStorage.getItem(SCALE_KEY)).toBe("0.8");
    expect(useDisplayStore.getState().uiScale).toBe(0.8);
  });

  it("restores a previously stored scale", () => {
    localStorage.setItem(SCALE_KEY, "1.25");
    expect(readStoredScale()).toBe(1.25);
  });
});

describe("display-store — refuses bad stored values rather than rendering a broken desktop", () => {
  // A zero or NaN scale collapses the UI, and the control you would use to fix
  // it is the one that broke, so these must fall back rather than pass through.
  it.each([
    ["unparseable", "banana"],
    ["zero", "0"],
    ["negative", "-1"],
    ["absurdly large", "40"],
    ["empty", ""],
  ])("falls back to the default for a %s value", (_label, stored) => {
    localStorage.setItem(SCALE_KEY, stored);
    expect(readStoredScale()).toBe(DEFAULT_SCALE);
  });

  it("clamps an out-of-range value passed to setUiScale", () => {
    useDisplayStore.getState().setUiScale(99);
    expect(useDisplayStore.getState().uiScale).toBe(DEFAULT_SCALE);
  });

  it("survives localStorage throwing (private mode)", () => {
    vi.spyOn(Storage.prototype, "getItem").mockImplementation(() => {
      throw new Error("denied");
    });
    expect(readStoredScale()).toBe(DEFAULT_SCALE);
  });
});

describe("display-store — applying the scale", () => {
  it("sets zoom on the document root", () => {
    applyScale(0.8);
    expect(document.documentElement.style.zoom).toBe("0.8");
  });

  it("exposes the scale as a CSS custom property", () => {
    applyScale(0.9);
    expect(document.documentElement.style.getPropertyValue("--taos-ui-scale")).toBe("0.9");
  });

  it("removes zoom entirely at 100% rather than setting it to 1", () => {
    applyScale(0.8);
    applyScale(DEFAULT_SCALE);
    expect(document.documentElement.style.zoom).toBe("");
  });

  it("uses zoom and never transform, which would break fixed positioning and hit-testing", () => {
    applyScale(0.8);
    expect(document.documentElement.style.transform).toBe("");
  });
});

describe("display-store — PER DEVICE, never per account", () => {
  // Jay, 2026-08-06: scale is a per-device preference. theme-store syncs some
  // appearance settings to the account (PUT /api/desktop/settings,
  // /api/preferences/themes); this must never join them, or a phone and a 4K
  // monitor would fight over one value.
  it("never performs a network request when the scale changes", () => {
    const fetchSpy = vi.spyOn(globalThis, "fetch");
    useDisplayStore.getState().setUiScale(0.8);
    useDisplayStore.getState().setUiScale(1.25);
    expect(fetchSpy).not.toHaveBeenCalled();
  });

  it("stores the value only in localStorage", () => {
    useDisplayStore.getState().setUiScale(0.9);
    expect(localStorage.getItem(SCALE_KEY)).toBe("0.9");
  });
});

describe("display-store — steps", () => {
  it("offers macOS-style discrete steps including an unscaled default", () => {
    expect(SCALE_STEPS.map((s) => s.value)).toContain(DEFAULT_SCALE);
  });

  it("orders steps largest-text first so the control reads like the macOS pane", () => {
    const values = SCALE_STEPS.map((s) => s.value);
    expect(values).toEqual([...values].sort((a, b) => b - a));
  });

  it("captions only the two extremes", () => {
    const captioned = SCALE_STEPS.filter((s) => s.caption);
    expect(captioned).toHaveLength(2);
    expect(captioned[0]).toBe(SCALE_STEPS[0]);
    expect(captioned[1]).toBe(SCALE_STEPS[SCALE_STEPS.length - 1]);
  });
});
