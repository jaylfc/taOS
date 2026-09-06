// @vitest-environment jsdom
import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { renderHook } from "@testing-library/react";
import { useTypingEmitter } from "../use-typing-emitter";

describe("useTypingEmitter", () => {
  beforeEach(() => {
    vi.useFakeTimers();
    global.fetch = vi.fn(() =>
      Promise.resolve({ ok: true, status: 200, json: () => Promise.resolve({ ok: true }) }),
    ) as unknown as typeof fetch;
  });
  afterEach(() => vi.useRealTimers());

  it("POSTs /typing on first call, debounces subsequent calls within 1s", async () => {
    const { result } = renderHook(() => useTypingEmitter("c1", "jay"));
    result.current();
    expect(fetch).toHaveBeenCalledTimes(1);
    result.current();
    result.current();
    expect(fetch).toHaveBeenCalledTimes(1); // still debounced
    vi.advanceTimersByTime(1100);
    result.current();
    expect(fetch).toHaveBeenCalledTimes(2);
  });

  it("does nothing when channelId is null", () => {
    const { result } = renderHook(() => useTypingEmitter(null, "jay"));
    result.current();
    expect(fetch).not.toHaveBeenCalled();
  });
});
