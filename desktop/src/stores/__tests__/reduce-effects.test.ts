import { describe, it, expect, beforeEach } from "vitest";
import { useThemeStore } from "../theme-store";

const KEY = "taos-reduce-effects";

beforeEach(() => {
  localStorage.removeItem(KEY);
  useThemeStore.setState({ reduceEffects: false } as never);
  document.documentElement.removeAttribute("data-perf");
});

describe("reduce-effects / performance mode (#58)", () => {
  it("setReduceEffects(true) flips state and persists 'on'", () => {
    useThemeStore.getState().setReduceEffects(true);
    expect(useThemeStore.getState().reduceEffects).toBe(true);
    expect(localStorage.getItem(KEY)).toBe("on");
  });

  it("setReduceEffects(false) clears state and persists 'off'", () => {
    useThemeStore.getState().setReduceEffects(true);
    useThemeStore.getState().setReduceEffects(false);
    expect(useThemeStore.getState().reduceEffects).toBe(false);
    expect(localStorage.getItem(KEY)).toBe("off");
  });

  it("setReduceEffects(true) persists and gates effects via data-perf", () => {
    useThemeStore.getState().setReduceEffects(true);
    expect(useThemeStore.getState().reduceEffects).toBe(true);
    expect(localStorage.getItem(KEY)).toBe("on");

    const root = document.documentElement;
    if (useThemeStore.getState().reduceEffects) {
      root.setAttribute("data-perf", "reduced");
    } else {
      root.removeAttribute("data-perf");
    }
    expect(root.getAttribute("data-perf")).toBe("reduced");

    useThemeStore.getState().setReduceEffects(false);
    if (useThemeStore.getState().reduceEffects) {
      root.setAttribute("data-perf", "reduced");
    } else {
      root.removeAttribute("data-perf");
    }
    expect(root.hasAttribute("data-perf")).toBe(false);
  });
});
