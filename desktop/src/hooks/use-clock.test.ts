import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { renderHook, act } from "@testing-library/react";
import { useClock } from "./use-clock";

describe("useClock", () => {
  beforeEach(() => {
    vi.useFakeTimers();
  });

  afterEach(() => {
    vi.useRealTimers();
  });

  it("returns a formatted date string on initial render", () => {
    const fixed = new Date(2025, 0, 15, 14, 30);
    vi.setSystemTime(fixed);

    const { result } = renderHook(() => useClock());

    expect(result.current).toBe("Wed 15 Jan  14:30");
  });

  it("updates the formatted time after the interval elapses", () => {
    const start = new Date(2025, 5, 20, 9, 0);
    vi.setSystemTime(start);

    const { result } = renderHook(() => useClock());
    expect(result.current).toBe("Fri 20 Jun  09:00");

    const later = new Date(2025, 5, 20, 9, 30);
    act(() => {
      vi.setSystemTime(later);
      vi.advanceTimersByTime(30_000);
    });

    expect(result.current).toBe("Fri 20 Jun  09:30");
  });

  it("does not update before the interval elapses", () => {
    const start = new Date(2025, 0, 1, 12, 0);
    vi.setSystemTime(start);

    const { result } = renderHook(() => useClock());
    expect(result.current).toBe("Wed 1 Jan  12:00");

    act(() => {
      vi.advanceTimersByTime(29_999);
    });

    expect(result.current).toBe("Wed 1 Jan  12:00");
  });
});
