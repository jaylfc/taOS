import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { renderHook, act } from "@testing-library/react";
import { useIsPwa } from "../use-is-pwa";

describe("useIsPwa", () => {
  let matchMediaListeners: Map<string, Set<(e: { matches: boolean }) => void>>;

  function createMql(matches: boolean) {
    return {
      matches,
      media: "(display-mode: standalone)",
      addEventListener: (event: string, handler: (e: { matches: boolean }) => void) => {
        if (!matchMediaListeners.has(event)) matchMediaListeners.set(event, new Set());
        matchMediaListeners.get(event)!.add(handler);
      },
      removeEventListener: (event: string, handler: (e: { matches: boolean }) => void) => {
        matchMediaListeners.get(event)?.delete(handler);
      },
      // Legacy API
      addListener: vi.fn(),
      removeListener: vi.fn(),
      onchange: null,
      dispatchEvent: vi.fn(),
    };
  }

  beforeEach(() => {
    matchMediaListeners = new Map();
    vi.stubGlobal("matchMedia", vi.fn((query: string) => createMql(false)));
  });

  afterEach(() => {
    vi.unstubAllGlobals();
    // Always reset navigator.standalone — doing it here (not inline after an
    // assertion) means a failing expect can't leak PWA state into later tests.
    Object.defineProperty(navigator, "standalone", { value: undefined, configurable: true });
  });

  it("returns false when display-mode is NOT standalone", () => {
    const { result } = renderHook(() => useIsPwa());
    expect(result.current).toBe(false);
  });

  it("returns true when display-mode is standalone", () => {
    vi.stubGlobal("matchMedia", vi.fn((query: string) => createMql(true)));
    const { result } = renderHook(() => useIsPwa());
    expect(result.current).toBe(true);
  });

  it("returns true when navigator.standalone is true (iOS PWA)", () => {
    Object.defineProperty(navigator, "standalone", {
      value: true,
      configurable: true,
      writable: true,
    });
    const { result } = renderHook(() => useIsPwa());
    expect(result.current).toBe(true);
  });

  it("updates when media query changes from non-standalone to standalone", () => {
    const mql = createMql(false);
    vi.stubGlobal("matchMedia", vi.fn(() => mql));

    const { result } = renderHook(() => useIsPwa());
    expect(result.current).toBe(false);

    act(() => {
      // Simulate the media query match changing
      mql.matches = true;
      matchMediaListeners.get("change")?.forEach((h) => h({ matches: true }));
    });
    expect(result.current).toBe(true);
  });

  it("updates when media query changes from standalone back to browser", () => {
    const mql = createMql(true);
    vi.stubGlobal("matchMedia", vi.fn(() => mql));

    const { result } = renderHook(() => useIsPwa());
    expect(result.current).toBe(true);

    act(() => {
      mql.matches = false;
      matchMediaListeners.get("change")?.forEach((h) => h({ matches: false }));
    });
    expect(result.current).toBe(false);
  });

  it("does NOT clobber iOS navigator.standalone when media query changes", () => {
    // iOS: navigator.standalone is true, but (display-mode: standalone) media
    // query is unreliable — must keep PWA detection even when mql fires "change".
    Object.defineProperty(navigator, "standalone", {
      value: true,
      configurable: true,
      writable: true,
    });

    const mql = createMql(false);  // media query says browser
    vi.stubGlobal("matchMedia", vi.fn(() => mql));

    const { result } = renderHook(() => useIsPwa());
    // Initial state: combined check says true (navigator.standalone)
    expect(result.current).toBe(true);

    // Media query change event fires — must NOT clobber navigator.standalone
    act(() => {
      matchMediaListeners.get("change")?.forEach((h) => h({ matches: false }));
    });
    expect(result.current).toBe(true);  // still PWA via navigator.standalone
  });
});
