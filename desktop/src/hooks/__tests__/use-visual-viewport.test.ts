import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { renderHook, act } from "@testing-library/react";
import { useVisualViewport } from "../use-visual-viewport";

describe("useVisualViewport", () => {
  const listeners = new Map<string, Set<() => void>>();
  let vv: { height: number; offsetTop: number; addEventListener: (t: string, l: () => void) => void; removeEventListener: (t: string, l: () => void) => void };

  beforeEach(() => {
    listeners.clear();
    vv = {
      height: 800,
      offsetTop: 0,
      addEventListener: (t, l) => {
        if (!listeners.has(t)) listeners.set(t, new Set());
        listeners.get(t)!.add(l);
      },
      removeEventListener: (t, l) => listeners.get(t)?.delete(l),
    };
    Object.defineProperty(window, "visualViewport", { value: vv, configurable: true });
    Object.defineProperty(window, "innerHeight", { value: 800, configurable: true });
  });

  afterEach(() => {
    // @ts-expect-error test cleanup
    delete window.visualViewport;
  });

  it("returns height + keyboardInset=0 when viewport matches window", () => {
    const { result } = renderHook(() => useVisualViewport());
    expect(result.current.height).toBe(800);
    expect(result.current.keyboardInset).toBe(0);
  });

  it("computes keyboardInset when viewport shrinks (keyboard open)", () => {
    const { result } = renderHook(() => useVisualViewport());
    act(() => {
      vv.height = 500;
      listeners.get("resize")?.forEach((l) => l());
    });
    expect(result.current.keyboardInset).toBe(300);
  });

  it("returns fallback when visualViewport is undefined", () => {
    // @ts-expect-error delete VV
    delete window.visualViewport;
    Object.defineProperty(window, "innerHeight", { value: 600, configurable: true });
    const { result } = renderHook(() => useVisualViewport());
    expect(result.current.height).toBe(600);
    expect(result.current.keyboardInset).toBe(0);
  });
});
