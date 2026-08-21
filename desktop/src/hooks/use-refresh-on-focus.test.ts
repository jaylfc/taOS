import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { renderHook, act } from "@testing-library/react";
import { useRefreshOnFocus } from "./use-refresh-on-focus";

describe("useRefreshOnFocus", () => {
  beforeEach(() => {
    vi.useFakeTimers();
    Object.defineProperty(document, "hidden", {
      value: false,
      configurable: true,
    });
  });

  afterEach(() => {
    vi.useRealTimers();
  });

  it("focus event triggers refetch", async () => {
    const refetch = vi.fn().mockResolvedValue(undefined);
    let capturedFocus: (() => void) | null = null;
    let capturedVisibility: (() => void) | null = null;
    let focusHandler: (() => void) | null = null;
    let visibilityHandler: (() => void) | null = null;

    vi.spyOn(window, "addEventListener").mockImplementation((event, fn) => {
      if (event === "focus") {
        capturedFocus = fn as () => void;
        focusHandler = fn as () => void;
      }
    });
    vi.spyOn(document, "addEventListener").mockImplementation((event, fn) => {
      if (event === "visibilitychange") {
        capturedVisibility = fn as () => void;
        visibilityHandler = fn as () => void;
      }
    });

    renderHook(() => useRefreshOnFocus(refetch));

    expect(capturedFocus).toBeDefined();
    expect(capturedVisibility).toBeDefined();

    act(() => {
      capturedFocus!();
    });

    await act(async () => {
      vi.advanceTimersByTime(1000);
    });

    expect(refetch).toHaveBeenCalledTimes(1);
    expect(refetch).toHaveBeenCalledWith();
  });

  it("visibility change triggers refetch when becoming visible", async () => {
    const refetch = vi.fn().mockResolvedValue(undefined);
    let capturedVisibility: (() => void) | null = null;

    vi.spyOn(document, "addEventListener").mockImplementation((event, fn) => {
      if (event === "visibilitychange") capturedVisibility = fn as () => void;
    });

    Object.defineProperty(document, "hidden", { value: true, configurable: true });

    renderHook(() => useRefreshOnFocus(refetch));

    Object.defineProperty(document, "hidden", { value: false, configurable: true });
    act(() => {
      capturedVisibility!();
    });

    await act(async () => {
      vi.advanceTimersByTime(1000);
    });

    expect(refetch).toHaveBeenCalledTimes(1);
  });

  it("does not refetch on visibility change when hidden", async () => {
    const refetch = vi.fn().mockResolvedValue(undefined);
    let capturedVisibility: (() => void) | null = null;

    vi.spyOn(document, "addEventListener").mockImplementation((event, fn) => {
      if (event === "visibilitychange") capturedVisibility = fn as () => void;
    });

    Object.defineProperty(document, "hidden", { value: true, configurable: true });

    renderHook(() => useRefreshOnFocus(refetch));

    act(() => {
      capturedVisibility!();
    });

    await act(async () => {
      vi.advanceTimersByTime(1000);
    });

    expect(refetch).not.toHaveBeenCalled();
  });

  it("debounce coalesces a burst", async () => {
    const refetch = vi.fn().mockResolvedValue(undefined);
    let capturedFocus: (() => void) | null = null;

    vi.spyOn(window, "addEventListener").mockImplementation((event, fn) => {
      if (event === "focus") capturedFocus = fn as () => void;
    });

    renderHook(() => useRefreshOnFocus(refetch));

    act(() => {
      capturedFocus!();
      capturedFocus!();
      capturedFocus!();
    });

    await act(async () => {
      vi.advanceTimersByTime(1000);
    });

    expect(refetch).toHaveBeenCalledTimes(1);
  });

  it("cleans up listeners on unmount", () => {
    const removeFocusSpy = vi.spyOn(window, "removeEventListener");
    const removeVisibilitySpy = vi.spyOn(document, "removeEventListener");
    let focusHandler: (() => void) | null = null;
    let visibilityHandler: (() => void) | null = null;

    vi.spyOn(window, "addEventListener").mockImplementation((event, fn) => {
      if (event === "focus") focusHandler = fn as () => void;
    });
    vi.spyOn(document, "addEventListener").mockImplementation((event, fn) => {
      if (event === "visibilitychange") visibilityHandler = fn as () => void;
    });

    const { unmount } = renderHook(() => useRefreshOnFocus(vi.fn()));

    expect(focusHandler).toBeDefined();
    expect(visibilityHandler).toBeDefined();

    unmount();

    expect(removeFocusSpy).toHaveBeenCalledWith("focus", focusHandler);
    expect(removeVisibilitySpy).toHaveBeenCalledWith("visibilitychange", visibilityHandler);
  });
});
