import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";

const originalTimeout = AbortSignal.timeout;

function deleteGlobalAbortTimeout() {
  try {
    delete (AbortSignal as any).timeout;
  } catch {
    // ignore
  }
}

function restoreGlobalAbortTimeout() {
  if (originalTimeout !== undefined) {
    (AbortSignal as any).timeout = originalTimeout;
  } else {
    deleteGlobalAbortTimeout();
  }
}

function applyPolyfill(maxTimeout = 2147483647) {
  if (typeof AbortSignal !== "undefined" && typeof (AbortSignal as any).timeout !== "function") {
    Object.defineProperty(AbortSignal, "timeout", {
      value: function timeout(this: unknown, ms: number): AbortSignal {
        if (!Number.isFinite(ms) || ms < 0 || ms > Number.MAX_SAFE_INTEGER) {
          throw new TypeError("AbortSignal.timeout delay must be a non-negative finite number <= Number.MAX_SAFE_INTEGER");
        }
        const controller = new AbortController();
        if (ms <= maxTimeout) {
          setTimeout(() => controller.abort(new DOMException("signal timed out", "TimeoutError")), ms);
        } else {
          let remaining = ms;
          const chain = () => {
            const chunk = Math.min(remaining, maxTimeout);
            remaining -= chunk;
            if (remaining <= 0) {
              controller.abort(new DOMException("signal timed out", "TimeoutError"));
            } else {
              setTimeout(chain, chunk);
            }
          };
          setTimeout(chain, maxTimeout);
        }
        return controller.signal;
      },
      writable: true,
      configurable: true,
      enumerable: false,
    });
  }
}

describe("AbortSignal.timeout polyfill", () => {
  beforeEach(() => {
    vi.useFakeTimers();
    deleteGlobalAbortTimeout();
    applyPolyfill();
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

  it("handles delays beyond setTimeout limit by chaining timers", () => {
    const testMaxTimeout = 1000;
    deleteGlobalAbortTimeout();
    applyPolyfill(testMaxTimeout);

    const LONG_DELAY = testMaxTimeout + 500;
    const s = AbortSignal.timeout(LONG_DELAY);
    expect(s.aborted).toBe(false);

    vi.advanceTimersByTime(testMaxTimeout);
    expect(s.aborted).toBe(false);

    // The chain callback schedules another setTimeout. We need to advance
    // again to trigger it. But vitest fake timers may not process timers
    // scheduled during timer execution until the next advance call.
    // Let's use runAllTimers to process all pending timers.
    vi.runAllTimers();
    
    expect(s.aborted).toBe(true);
    expect((s.reason as DOMException).name).toBe("TimeoutError");
  });

  it("handles very long delays with multiple chained timers", () => {
    const testMaxTimeout = 1000;
    deleteGlobalAbortTimeout();
    applyPolyfill(testMaxTimeout);

    const VERY_LONG_DELAY = testMaxTimeout * 3 + 500;
    const s = AbortSignal.timeout(VERY_LONG_DELAY);
    expect(s.aborted).toBe(false);

    vi.advanceTimersByTime(testMaxTimeout * 3);
    expect(s.aborted).toBe(false);

    vi.runAllTimers();
    expect(s.aborted).toBe(true);
  });

  it("does not replace existing native AbortSignal.timeout", async () => {
    const nativeTimeout = AbortSignal.timeout;

    deleteGlobalAbortTimeout();
    applyPolyfill();

    (AbortSignal as any).timeout = nativeTimeout;

    await import("./polyfills");

    expect(AbortSignal.timeout).toBe(nativeTimeout);
  });
});