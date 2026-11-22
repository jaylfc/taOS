import { render, act } from "@testing-library/react";
import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { NotesApp } from "./NotesApp";
import { BackendStatusProvider } from "@/contexts/BackendStatusContext";

function baseResponses() {
  return {
    "/api/notes": { ok: true, body: [] },
  };
}

function createFetchMock(healthFailThenSucceed: boolean, dataResponses: Record<string, { ok: boolean; status?: number; body: unknown }>) {
  let healthCallCount = 0;
  const calls: string[] = [];
  return {
    mock: vi.fn().mockImplementation((input: string) => {
      calls.push(input);
      if (input === "/api/health") {
        healthCallCount++;
        if (healthFailThenSucceed && healthCallCount === 1) {
          return Promise.reject(new TypeError("network"));
        }
        return Promise.resolve({
          ok: true,
          status: 200,
          headers: { get: (name: string) => name === "X-Taos-Version" ? "1.0.0" : null },
          json: () => Promise.resolve({}),
          text: () => Promise.resolve(""),
        });
      }
      const hit = dataResponses[input];
      if (!hit) throw new Error(`Unmocked fetch: ${input}`);
      return Promise.resolve({
        ok: hit.ok,
        status: hit.status ?? (hit.ok ? 200 : 500),
        headers: { get: () => "application/json" },
        json: () => Promise.resolve(hit.body),
        text: () => Promise.resolve(""),
      });
    }),
    getCalls: () => calls,
  };
}

let originalSetInterval: typeof setInterval;
let originalClearInterval: typeof clearInterval;
let intervalCallbacks: Map<number, () => void>;
let nextIntervalId: number;

function renderWithProvider(ui: React.ReactElement) {
  return render(<BackendStatusProvider>{ui}</BackendStatusProvider>);
}

describe("NotesApp backend recovery", () => {
  beforeEach(() => {
    vi.useFakeTimers();
    vi.resetModules();
    originalSetInterval = global.setInterval;
    originalClearInterval = global.clearInterval;
    intervalCallbacks = new Map();
    nextIntervalId = 1;

    vi.stubGlobal("setInterval", vi.fn((cb: TimerHandler, delay: number) => {
      if (typeof cb === "function") {
        const id = nextIntervalId++;
        intervalCallbacks.set(id, cb);
        return id;
      }
      return originalSetInterval(cb, delay);
    }));

    vi.stubGlobal("clearInterval", vi.fn((id: number) => {
      intervalCallbacks.delete(id);
      originalClearInterval(id);
    }));
  });
  afterEach(() => {
    vi.useRealTimers();
    vi.unstubAllGlobals();
    vi.restoreAllMocks();
    global.setInterval = originalSetInterval;
    global.clearInterval = originalClearInterval;
  });

  it("refetches notes when the backend recovers after a failed first load", async () => {
    const dataResponses = baseResponses();
    const fetchTracker = createFetchMock(true, dataResponses);
    vi.stubGlobal("fetch", fetchTracker.mock);

    renderWithProvider(<NotesApp windowId="w1" />);

    // Initial loadNotes runs immediately on mount
    await act(async () => { await Promise.resolve(); });
    
    const callsAfterMount = fetchTracker.getCalls();
    // Initial fetch: notes
    expect(callsAfterMount.filter(c => c === "/api/notes")).toHaveLength(1);
    expect(callsAfterMount.filter(c => c === "/api/health")).toHaveLength(0);

    // First health check runs after 2000ms and fails
    await act(async () => { await vi.advanceTimersByTimeAsync(2000); });

    const callsAfterFirstHealth = fetchTracker.getCalls();
    expect(callsAfterFirstHealth.filter(c => c === "/api/health")).toHaveLength(1);
    // loadNotes should not have been called again (intervals mocked)
    expect(callsAfterFirstHealth.filter(c => c === "/api/notes")).toHaveLength(1);

    // Second health check runs after 4000ms more (total 6000ms) and succeeds -> recovery
    await act(async () => { await vi.advanceTimersByTimeAsync(4000); });

    const callsAfterRecovery = fetchTracker.getCalls();
    // Should have: initial notes + 1 health (failed) + 1 health (success) + 1 notes (recovery) = relevant calls
    expect(callsAfterRecovery.filter(c => c === "/api/notes")).toHaveLength(2); // Initial + recovery
    expect(callsAfterRecovery.filter(c => c === "/api/health")).toHaveLength(2);
  });
});