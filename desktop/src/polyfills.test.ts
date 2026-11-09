import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { installAbortSignalTimeout } from "./polyfills";

function deleteGlobalAbortTimeout() {
  try {
    delete (AbortSignal as any).timeout;
  } catch {
    // ignore
  }
}

function restoreGlobalAbortTimeout() {
  deleteGlobalAbortTimeout();
}

describe("AbortSignal.timeout polyfill", () => {
  beforeEach(() => {
    vi.useFakeTimers();
    deleteGlobalAbortTimeout();
    installAbortSignalTimeout(1000);
  });

  afterEach(() => {
    restoreGlobalAbortTimeout();
    vi.useRealTimers();
  });

  it("should exist and abort after the delay", () => {
    expect(typeof AbortSignal.timeout).toBe("function");

    const s = AbortSignal.timeout(50);
    expect(s.aborted).toBe(false);

    vi.advanceTimersByTime(50);

    expect(s.aborted).toBe(true);
    expect((s.reason as DOMException).name).toBe("TimeoutError");
  });

  it("throws TypeError for negative delay", () => {
    expect(() => AbortSignal.timeout(-1)).toThrow(TypeError);
    expect(() => AbortSignal.timeout(-1)).toThrow("non-negative finite number");
  });

  it("throws TypeError for NaN delay", () => {
    expect(() => AbortSignal.timeout(NaN)).toThrow(TypeError);
  });

  it("throws TypeError for Infinity delay", () => {
    expect(() => AbortSignal.timeout(Infinity)).toThrow(TypeError);
    expect(() => AbortSignal.timeout(-Infinity)).toThrow(TypeError);
  });

  it("throws TypeError for delay > Number.MAX_SAFE_INTEGER", () => {
    expect(() => AbortSignal.timeout(Number.MAX_SAFE_INTEGER + 1)).toThrow(TypeError);
    expect(() => AbortSignal.timeout(Number.MAX_SAFE_INTEGER + 100)).toThrow(TypeError);
  });

  it("accepts Number.MAX_SAFE_INTEGER as valid delay", () => {
    const s = AbortSignal.timeout(Number.MAX_SAFE_INTEGER);
    expect(s.aborted).toBe(false);
  });

  it("aborts exactly at the requested time for chained timeout (1500 ms)", () => {
    const s = AbortSignal.timeout(1500);
    expect(s.aborted).toBe(false);

    vi.advanceTimersByTime(1499);
    expect(s.aborted).toBe(false);

    vi.advanceTimersByTime(1);
    expect(s.aborted).toBe(true);
    expect((s.reason as DOMException).name).toBe("TimeoutError");
  });

  it("aborts exactly at the requested time for chained timeout (3500 ms)", () => {
    const s = AbortSignal.timeout(3500);
    expect(s.aborted).toBe(false);

    vi.advanceTimersByTime(3499);
    expect(s.aborted).toBe(false);

    vi.advanceTimersByTime(1);
    expect(s.aborted).toBe(true);
    expect((s.reason as DOMException).name).toBe("TimeoutError");
  });

  it("aborts exactly at the requested time for short timeout (50 ms)", () => {
    const s = AbortSignal.timeout(50);
    expect(s.aborted).toBe(false);

    vi.advanceTimersByTime(49);
    expect(s.aborted).toBe(false);

    vi.advanceTimersByTime(1);
    expect(s.aborted).toBe(true);
    expect((s.reason as DOMException).name).toBe("TimeoutError");
  });

  it("timeout(0) is not aborted synchronously and fires after timer advance", () => {
    const s = AbortSignal.timeout(0);
    expect(s.aborted).toBe(false);

    const listener = vi.fn();
    s.addEventListener("abort", listener);

    expect(listener).not.toHaveBeenCalled();

    vi.advanceTimersByTime(0);

    expect(s.aborted).toBe(true);
    expect(listener).toHaveBeenCalledTimes(1);
  });

  it("does not replace existing native AbortSignal.timeout", () => {
    deleteGlobalAbortTimeout();
    const stubTimeout = vi.fn((ms: number) => AbortSignal.timeout(ms));
    (AbortSignal as any).timeout = stubTimeout;

    installAbortSignalTimeout();

    expect(AbortSignal.timeout).toBe(stubTimeout);
  });
});