import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { renderHook, act } from "@testing-library/react";
import { useIsPwa } from "./use-is-pwa";

function createMockMatchMedia(initialMatches: boolean) {
  let matches = initialMatches;
  const listeners: Array<(e: { matches: boolean }) => void> = [];
  return {
    get matches() { return matches; },
    set matches(v: boolean) { matches = v; },
    media: "(display-mode: standalone)",
    onchange: null,
    addListener: vi.fn(),
    removeListener: vi.fn(),
    addEventListener: vi.fn().mockImplementation(
      (_: string, cb: (e: { matches: boolean }) => void) => { listeners.push(cb); },
    ),
    removeEventListener: vi.fn(),
    dispatchEvent: vi.fn().mockReturnValue(false),
    _fire(match: boolean) {
      matches = match;
      listeners.forEach((cb) => cb({ matches: match }));
    },
  } as unknown as MediaQueryList & { _fire: (m: boolean) => void };
}

describe("useIsPwa", () => {
  beforeEach(() => {
    Object.defineProperty(window, "navigator", {
      configurable: true,
      writable: true,
      value: { ...window.navigator, standalone: false },
    });
  });

  afterEach(() => {
    vi.restoreAllMocks();
  });

  it("returns false when not in standalone mode", () => {
    window.matchMedia = vi.fn().mockReturnValue(createMockMatchMedia(false));
    const { result } = renderHook(() => useIsPwa());
    expect(result.current).toBe(false);
  });

  it("returns true when matchMedia reports standalone", () => {
    window.matchMedia = vi.fn().mockReturnValue(createMockMatchMedia(true));
    const { result } = renderHook(() => useIsPwa());
    expect(result.current).toBe(true);
  });

  it("returns true when navigator.standalone is true", () => {
    Object.defineProperty(window, "navigator", {
      configurable: true,
      writable: true,
      value: { ...window.navigator, standalone: true },
    });
    window.matchMedia = vi.fn().mockReturnValue(createMockMatchMedia(false));
    const { result } = renderHook(() => useIsPwa());
    expect(result.current).toBe(true);
  });

  it("updates when matchMedia change event fires to standalone", () => {
    const mql = createMockMatchMedia(false);
    window.matchMedia = vi.fn().mockReturnValue(mql);

    const { result } = renderHook(() => useIsPwa());
    expect(result.current).toBe(false);

    act(() => { mql._fire(true); });
    expect(result.current).toBe(true);
  });

  it("updates when matchMedia change event fires from standalone to browser", () => {
    const mql = createMockMatchMedia(true);
    window.matchMedia = vi.fn().mockReturnValue(mql);

    const { result } = renderHook(() => useIsPwa());
    expect(result.current).toBe(true);

    act(() => { mql._fire(false); });
    expect(result.current).toBe(false);
  });
});
