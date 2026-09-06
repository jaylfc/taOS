import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { renderHook, act } from "@testing-library/react";
import { useDeviceMode } from "./use-device-mode";

function createMockMatchMedia(initialMatches: boolean) {
  let matches = initialMatches;
  const listeners: Array<(e: { matches: boolean }) => void> = [];
  return {
    get matches() { return matches; },
    set matches(v: boolean) { matches = v; },
    media: "(pointer: coarse)",
    onchange: null,
    addListener: vi.fn(),
    removeListener: vi.fn(),
    addEventListener: vi.fn(),
    removeEventListener: vi.fn(),
    dispatchEvent: vi.fn().mockReturnValue(false),
  } as unknown as MediaQueryList;
}

describe("useDeviceMode", () => {
  const originalMatchMedia = window.matchMedia;
  const originalInnerWidth = Object.getOwnPropertyDescriptor(window, "innerWidth");
  const originalMaxTouchPoints = Object.getOwnPropertyDescriptor(navigator, "maxTouchPoints");

  beforeEach(() => {
    Object.defineProperty(window, "innerWidth", {
      configurable: true,
      writable: true,
      value: 1024,
    });
    Object.defineProperty(navigator, "maxTouchPoints", {
      configurable: true,
      writable: true,
      value: 0,
    });
  });

  afterEach(() => {
    window.matchMedia = originalMatchMedia;
    if (originalInnerWidth) {
      Object.defineProperty(window, "innerWidth", originalInnerWidth);
    } else {
      delete (window as Record<string, unknown>)["innerWidth"];
    }
    if (originalMaxTouchPoints) {
      Object.defineProperty(navigator, "maxTouchPoints", originalMaxTouchPoints);
    } else {
      delete (navigator as Record<string, unknown>)["maxTouchPoints"];
    }
  });

  it("returns desktop on a wide viewport without touch", () => {
    Object.defineProperty(window, "innerWidth", { configurable: true, value: 1024 });
    Object.defineProperty(navigator, "maxTouchPoints", { configurable: true, value: 0 });
    window.matchMedia = vi.fn().mockReturnValue(createMockMatchMedia(false));

    const { result } = renderHook(() => useDeviceMode());
    expect(result.current).toBe("desktop");
  });

  it("returns mobile on a narrow viewport (width < 768)", () => {
    Object.defineProperty(window, "innerWidth", { configurable: true, value: 400 });
    Object.defineProperty(navigator, "maxTouchPoints", { configurable: true, value: 0 });
    window.matchMedia = vi.fn().mockReturnValue(createMockMatchMedia(false));

    const { result } = renderHook(() => useDeviceMode());
    expect(result.current).toBe("mobile");
  });

  it("returns tablet on a medium viewport with coarse pointer", () => {
    Object.defineProperty(window, "innerWidth", { configurable: true, value: 900 });
    Object.defineProperty(navigator, "maxTouchPoints", { configurable: true, value: 0 });
    window.matchMedia = vi.fn().mockReturnValue(createMockMatchMedia(true));

    const { result } = renderHook(() => useDeviceMode());
    expect(result.current).toBe("tablet");
  });

  it("returns tablet on a medium viewport with maxTouchPoints > 0", () => {
    Object.defineProperty(window, "innerWidth", { configurable: true, value: 900 });
    Object.defineProperty(navigator, "maxTouchPoints", { configurable: true, value: 5 });
    window.matchMedia = vi.fn().mockReturnValue(createMockMatchMedia(false));

    const { result } = renderHook(() => useDeviceMode());
    expect(result.current).toBe("tablet");
  });

  it("returns desktop on a medium viewport without touch", () => {
    Object.defineProperty(window, "innerWidth", { configurable: true, value: 900 });
    Object.defineProperty(navigator, "maxTouchPoints", { configurable: true, value: 0 });
    window.matchMedia = vi.fn().mockReturnValue(createMockMatchMedia(false));

    const { result } = renderHook(() => useDeviceMode());
    expect(result.current).toBe("desktop");
  });

  it("returns desktop on a wide viewport even with touch", () => {
    Object.defineProperty(window, "innerWidth", { configurable: true, value: 1200 });
    Object.defineProperty(navigator, "maxTouchPoints", { configurable: true, value: 5 });
    window.matchMedia = vi.fn().mockReturnValue(createMockMatchMedia(true));

    const { result } = renderHook(() => useDeviceMode());
    expect(result.current).toBe("desktop");
  });

  it("updates mode on resize from desktop to mobile", () => {
    Object.defineProperty(window, "innerWidth", { configurable: true, value: 1024 });
    Object.defineProperty(navigator, "maxTouchPoints", { configurable: true, value: 0 });
    window.matchMedia = vi.fn().mockReturnValue(createMockMatchMedia(false));

    const { result } = renderHook(() => useDeviceMode());
    expect(result.current).toBe("desktop");

    act(() => {
      Object.defineProperty(window, "innerWidth", { configurable: true, value: 500 });
      window.dispatchEvent(new Event("resize"));
    });

    expect(result.current).toBe("mobile");
  });

  it("updates mode on resize from mobile to desktop", () => {
    Object.defineProperty(window, "innerWidth", { configurable: true, value: 400 });
    Object.defineProperty(navigator, "maxTouchPoints", { configurable: true, value: 0 });
    window.matchMedia = vi.fn().mockReturnValue(createMockMatchMedia(false));

    const { result } = renderHook(() => useDeviceMode());
    expect(result.current).toBe("mobile");

    act(() => {
      Object.defineProperty(window, "innerWidth", { configurable: true, value: 1024 });
      window.dispatchEvent(new Event("resize"));
    });

    expect(result.current).toBe("desktop");
  });
});
