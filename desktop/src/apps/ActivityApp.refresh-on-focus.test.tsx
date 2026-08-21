import { render, act } from "@testing-library/react";
import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { ActivityApp } from "./ActivityApp";

describe("ActivityApp refresh-on-focus", () => {
  beforeEach(() => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
  });
  afterEach(() => {
    vi.useRealTimers();
    vi.unstubAllGlobals();
  });

  it("refetches /api/activity on window focus with the same URL as mount", async () => {
    const urls: string[] = [];
    const fetchMock = vi.fn().mockImplementation((url: string) => {
      urls.push(String(url));
      if (url === "/api/activity") {
        return Promise.resolve({
          ok: true,
          status: 200,
          headers: { get: () => "application/json" },
          json: () => Promise.resolve({
            timestamp: Date.now(),
            hardware: { cpu: { cores: 4, arch: "aarch64" }, ram_mb: 8192 },
            cpu: { cores: [], overall_percent: 0 },
            memory: { total_mb: 8192, used_mb: 4096, available_mb: 4096, percent: 50, swap_total_mb: 0, swap_used_mb: 0, swap_percent: 0 },
            npu: { cores: [], freq_hz: 0, type: "none" },
            gpu: { load: null, vram_percent: null, vram_used_mb: null, vram_total_mb: null, type: "none" },
            thermal: [],
            zram: [],
            disk: { io_rate: { read_bps: 0, write_bps: 0 }, usage_percent: 0, total_gb: 32, used_gb: 18 },
            network: [],
            processes: [],
          }),
          text: () => Promise.resolve(""),
        });
      }
      if (url === "/api/models/loaded") {
        return Promise.resolve({
          ok: true,
          status: 200,
          headers: { get: () => "application/json" },
          json: () => Promise.resolve({ loaded: [] }),
          text: () => Promise.resolve(""),
        });
      }
      if (url === "/api/scheduler/stats") {
        return Promise.resolve({
          ok: true,
          status: 200,
          headers: { get: () => "application/json" },
          json: () => Promise.resolve({ submitted: 0, completed: 0, errors: 0, rejected: 0, active: 0, resources: [] }),
          text: () => Promise.resolve(""),
        });
      }
      if (url === "/api/scheduler/tasks?limit=8") {
        return Promise.resolve({
          ok: true,
          status: 200,
          headers: { get: () => "application/json" },
          json: () => Promise.resolve({ tasks: [] }),
          text: () => Promise.resolve(""),
        });
      }
      if (url === "/api/cluster/workers") {
        return Promise.resolve({
          ok: true,
          status: 200,
          headers: { get: () => "application/json" },
          json: () => Promise.resolve([]),
          text: () => Promise.resolve(""),
        });
      }
      return Promise.resolve({
        ok: true,
        status: 200,
        headers: { get: () => "application/json" },
        json: () => Promise.resolve({}),
        text: () => Promise.resolve(""),
      });
    });
    vi.stubGlobal("fetch", fetchMock);

    render(<ActivityApp windowId="w1" />);
    await act(async () => {
      await Promise.resolve();
    });

    const activityCalls = () => urls.filter((u) => u === "/api/activity");
    expect(activityCalls().length).toBeGreaterThan(0);
    const before = activityCalls().length;

    window.dispatchEvent(new Event("focus"));
    await act(async () => {
      vi.advanceTimersByTime(1100);
      await Promise.resolve();
    });

    const after = activityCalls();
    expect(after.length).toBe(before + 1);
  });
});
