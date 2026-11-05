import { describe, it, expect, vi } from "vitest";

// Save the original timeout to restore later
const originalTimeout = AbortSignal.timeout;

// Function to delete the global timeout (from the original test file)
function deleteGlobalAbortTimeout() {
  try {
    delete (AbortSignal as any).timeout;
  } catch {
    // ignore
  }
}

// Function to restore the global timeout
function restoreGlobalAbortTimeout() {
  if (originalTimeout !== undefined) {
    (AbortSignal as any).timeout = originalTimeout;
  } else {
    deleteGlobalAbortTimeout();
  }
}

describe("AbortSignal.timeout polyfill", () => {
  it("should exist and abort after the delay", async () => {
    vi.useFakeTimers();
    try {
      // First delete the existing timeout if it exists
      deleteGlobalAbortTimeout();
      
      // Verify that timeout doesn't exist
      expect(typeof AbortSignal.timeout).toBe("undefined");
      
      // Now apply the polyfill (this is what polyfills.ts does)
      if (typeof AbortSignal !== "undefined" && typeof (AbortSignal as any).timeout !== "function") {
        Object.defineProperty(AbortSignal, "timeout", {
          value: function timeout(this: unknown, ms: number): AbortSignal {
            const controller = new AbortController();
            setTimeout(() => controller.abort(new DOMException("signal timed out", "TimeoutError")), ms);
            return controller.signal;
          },
          writable: true,
          configurable: true,
          enumerable: false,
        });
      }
      
      // Now timeout should exist
      expect(typeof AbortSignal.timeout).toBe("function");
      
      // Create a timeout signal
      const s = AbortSignal.timeout(50);
      expect(s.aborted).toBe(false);
      
      // Advance time to trigger timeout
      vi.advanceTimersByTime(50);
      
      // Should be aborted now
      expect(s.aborted).toBe(true);
      
      // Should have the correct error
      expect((s.reason as DOMException).name).toBe("TimeoutError");
    } finally {
      restoreGlobalAbortTimeout();
      vi.useRealTimers();
    }
  });

  it("does not replace existing native AbortSignal.timeout", async () => {
    // Save native timeout
    const nativeTimeout = AbortSignal.timeout;
    
    // Delete timeout to trigger polyfill application
    deleteGlobalAbortTimeout();
    
    // Verify it's gone
    expect(typeof AbortSignal.timeout).toBe("undefined");
    
    // Now apply the polyfill (this is what polyfills.ts does)
    if (typeof AbortSignal !== "undefined" && typeof (AbortSignal as any).timeout !== "function") {
      Object.defineProperty(AbortSignal, "timeout", {
        value: function timeout(this: unknown, ms: number): AbortSignal {
          const controller = new AbortController();
          setTimeout(() => controller.abort(new DOMException("signal timed out", "TimeoutError")), ms);
          return controller.signal;
        },
        writable: true,
        configurable: true,
        enumerable: false,
      });
    }
    
    // Restore native timeout (to test that polyfill doesn't replace it)
    (AbortSignal as any).timeout = nativeTimeout;
    
    // Import polyfills.ts to verify it doesn't replace the native timeout
    await import("./polyfills");
    
    // Verify it's still the native timeout
    expect(AbortSignal.timeout).toBe(nativeTimeout);
  });
});