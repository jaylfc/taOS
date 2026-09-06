import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { renderHook, act } from "@testing-library/react";
import { useVisualViewport } from "./use-visual-viewport";

function createMockVisualViewport(initialHeight: number, initialOffsetTop = 0) {
  let height = initialHeight;
  let offsetTop = initialOffsetTop;
  const listeners: Record<string, Array<() => void>> = {};
  return {
    get height() { return height; },
    set height(v: number) { height = v; },
    get offsetTop() { return offsetTop; },
    set offsetTop(v: number) { offsetTop = v; },
    addEventListener: vi.fn().mockImplementation((type: string, cb: () => void) => {
      (listeners[type] ||= []).push(cb);
    }),
    removeEventListener: vi.fn(),
    _fire(type: string) {
      (listeners[type] || []).forEach((cb) => cb());
    },
  } as unknown as VisualViewport & { _fire: (type: string) => void };
}

describe("useVisualViewport", () => {
  const originalVisualViewport = window.visualViewport;
  const originalInnerHeight = Object.getOwnPropertyDescriptor(window, "innerHeight");

  beforeEach(() => {
    Object.defineProperty(window, "innerHeight", {
      configurable: true,
      writable: true,
      value: 800,
    });
  });

  afterEach(() => {
    if (originalVisualViewport) {
      (window as unknown as Record<string, unknown>)["visualViewport"] = originalVisualViewport;
    } else {
      delete (window as Record<string, unknown>)["visualViewport"];
    }
    if (originalInnerHeight) {
      Object.defineProperty(window, "innerHeight", originalInnerHeight);
    } else {
      delete (window as Record<string, unknown>)["innerHeight"];
    }
  });

  it("returns visualViewport height when available", () => {
    const vv = createMockVisualViewport(600);
    (window as unknown as Record<string, unknown>)["visualViewport"] = vv;
    const { result } = renderHook(() => useVisualViewport());
    expect(result.current).toEqual({ height: 600, keyboardInset: 200 });
  });

  it("computes keyboardInset accounting for offsetTop", () => {
    const vv = createMockVisualViewport(500, 100);
    (window as unknown as Record<string, unknown>)["visualViewport"] = vv;
    const { result } = renderHook(() => useVisualViewport());
    expect(result.current).toEqual({ height: 500, keyboardInset: 200 });
  });

  it("falls back to innerHeight when visualViewport is null", () => {
    (window as unknown as Record<string, unknown>)["visualViewport"] = null;
    const { result } = renderHook(() => useVisualViewport());
    expect(result.current).toEqual({ height: 800, keyboardInset: 0 });
  });

  it("updates on resize event", () => {
    const vv = createMockVisualViewport(600);
    (window as unknown as Record<string, unknown>)["visualViewport"] = vv;
    const { result } = renderHook(() => useVisualViewport());
    expect(result.current).toEqual({ height: 600, keyboardInset: 200 });

    act(() => {
      vv.height = 500;
      vv._fire("resize");
    });
    expect(result.current).toEqual({ height: 500, keyboardInset: 300 });
  });

  it("updates on scroll event", () => {
    const vv = createMockVisualViewport(600);
    (window as unknown as Record<string, unknown>)["visualViewport"] = vv;
    const { result } = renderHook(() => useVisualViewport());
    expect(result.current).toEqual({ height: 600, keyboardInset: 200 });

    act(() => {
      vv.offsetTop = 50;
      vv._fire("scroll");
    });
    expect(result.current.keyboardInset).toBe(150);
  });
});
