import { renderHook, act } from "@testing-library/react";
import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { createBackendStatus } from "@/lib/backendStatus";
import { useHomeKey, HOME_KEY } from "@/hooks/use-home-key";

const press = (key: string) => window.dispatchEvent(new KeyboardEvent("keydown", { key }));

describe("Home action while banner shown (reconnecting)", () => {
  beforeEach(() => {
    vi.useFakeTimers();
  });

  afterEach(() => {
    vi.useRealTimers();
    vi.restoreAllMocks();
  });

  it("useHomeKey callback fires while backend status is reconnecting", async () => {
    // Create a backend status that stays in reconnecting
    const fetchMock = vi.fn().mockRejectedValue(new TypeError("network"));
    const bs = createBackendStatus({ healthUrl: "/api/health", fetchImpl: fetchMock });
    bs.start();

    // Advance to trigger first poll failure -> reconnecting
    await act(async () => {
      await vi.advanceTimersByTimeAsync(2000);
    });

    // Verify status is reconnecting
    expect(bs.getStatus()).toBe("reconnecting");

    // Now test that useHomeKey still works
    const onHome = vi.fn();
    renderHook(() => useHomeKey(true, onHome));

    press(HOME_KEY);

    expect(onHome).toHaveBeenCalledTimes(1);

    bs.stop();
  });

  it("presses F13 calls onHome while backend status is reconnecting", async () => {
    // Create a fresh controller with injected fetch mock
    const fetchMock = vi.fn().mockRejectedValue(new TypeError("network"));
    const bs = createBackendStatus({ healthUrl: "/api/health", fetchImpl: fetchMock });
    bs.start();

    // Advance to trigger first poll failure -> reconnecting
    await act(async () => {
      await vi.advanceTimersByTimeAsync(2000);
    });

    // Verify status is reconnecting
    expect(bs.getStatus()).toBe("reconnecting");

    // Test that pressing F13 calls onHome
    const onHome = vi.fn();
    renderHook(() => useHomeKey(true, onHome));

    press(HOME_KEY);

    expect(onHome).toHaveBeenCalledTimes(1);

    bs.stop();
  });
});