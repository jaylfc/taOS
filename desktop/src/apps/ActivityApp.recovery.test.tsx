import { render, act } from "@testing-library/react";
import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { ActivityApp } from "./ActivityApp";
import { BackendStatusProvider } from "@/contexts/BackendStatusContext";

function makeActivity(overrides: Record<string, unknown> = {}) {
  return {
    timestamp: Date.now(),
    hardware: {
      board: "Test Board",
      cpu: { model: "Test CPU", arch: "x86_64", cores: 4 },
      ram_mb: 8192,
    },
    cpu: { cores: [], overall_percent: 0 },
    memory: {
      total_mb: 8192,
      used_mb: 4096,
      available_mb: 4096,
      percent: 50,
      swap_total_mb: 0,
      swap_used_mb: 0,
      swap_percent: 0,
    },
    npu: { cores: [], freq_hz: 0, type: "none" },
    gpu: {
      load: null,
      vram_percent: null,
      vram_used_mb: null,
      vram_total_mb: null,
      type: "none",
    },
    thermal: [],
    zram: [],
    disk: {
      io_rate: { read_bps: 0, write_bps: 0 },
      usage_percent: 0,
      total_gb: 32,
      used_gb: 18,
    },
    network: [],
    processes: [],
    ...overrides,
  };
}

function schedulerStatsBody() {
  return { submitted: 0, completed: 0, errors: 0, rejected: 0, active: 0, resources: [] };
}

function baseResponses(activity = makeActivity()) {
  return {
    "/api/activity": { ok: true, body: activity },
    "/api/models/loaded": { ok: true, body: { loaded: [] } },
    "/api/scheduler/stats": { ok: true, body: schedulerStatsBody() },
    "/api/scheduler/tasks?limit=8": { ok: true, body: { tasks: [] } },
    "/api/cluster/workers": { ok: true, body: [] },
    "/api/activity/models?limit=200": { ok: true, body: { events: [], count: 0, event_types: [] } },
    // System health glance card (#621) mount fetches
    "/api/health?detailed=true": { ok: true, body: { status: "ok", subsystems: {} } },
    "/api/health-check": { ok: true, body: { checks: [] } },
    "/api/agents": { ok: true, body: [] },
    "/api/memory/stats": { ok: true, body: {} },
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

function renderWithProvider(ui: React.ReactElement) {
  return render(<BackendStatusProvider>{ui}</BackendStatusProvider>);
}

describe("ActivityApp backend recovery", () => {
  let originalSetInterval: typeof setInterval;
  let originalClearInterval: typeof clearInterval;
  let intervalCallbacks: Map<number, () => void>;
  let nextIntervalId: number;

  beforeEach(() => {
    vi.useFakeTimers();
    vi.resetModules();
    originalSetInterval = global.setInterval;
    originalClearInterval = global.clearInterval;
    intervalCallbacks = new Map();
    nextIntervalId = 1;

    // Mock setInterval to capture callbacks but not fire them automatically
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

  it("refetches activity data when the backend recovers after a failed first load", async () => {
    const dataResponses = baseResponses();
    const fetchTracker = createFetchMock(true, dataResponses);
    vi.stubGlobal("fetch", fetchTracker.mock);

    renderWithProvider(<ActivityApp windowId="w1" />);

    // Initial fetchData runs immediately on mount
    await act(async () => { await Promise.resolve(); });
    
    const callsAfterMount = fetchTracker.getCalls();
    expect(callsAfterMount.length).toBe(14); // 4 data + 1 cluster + 1 model activity + 4 system health (#621, loading branch) + 4 again when the loaded grid mounts the card
    expect(callsAfterMount.filter(c => c === "/api/activity")).toHaveLength(1);
    expect(callsAfterMount.filter(c => c === "/api/health")).toHaveLength(0);

    // First health check runs after 2000ms and fails
    await act(async () => { await vi.advanceTimersByTimeAsync(2000); });

    const callsAfterFirstHealth = fetchTracker.getCalls();
    // Should have initial 6 + 1 health = 7 (intervals are mocked and don't fire)
    expect(callsAfterFirstHealth.filter(c => c === "/api/health")).toHaveLength(1);
    expect(callsAfterFirstHealth.filter(c => c === "/api/activity")).toHaveLength(1);

    // Second health check runs after 4000ms more (total 6000ms) and succeeds -> recovery
    await act(async () => { await vi.advanceTimersByTimeAsync(4000); });

    const callsAfterRecovery = fetchTracker.getCalls();
    // Should have: initial 14 + 1 health (failed) + 1 health (success) + recovery refetches (per-URL counts asserted below)
    expect(callsAfterRecovery.filter(c => c === "/api/activity")).toHaveLength(2); // Initial + recovery
    expect(callsAfterRecovery.filter(c => c === "/api/models/loaded")).toHaveLength(2);
    expect(callsAfterRecovery.filter(c => c === "/api/scheduler/stats")).toHaveLength(2);
    expect(callsAfterRecovery.filter(c => c === "/api/scheduler/tasks?limit=8")).toHaveLength(2);
    expect(callsAfterRecovery.filter(c => c === "/api/health")).toHaveLength(2);
  });
});