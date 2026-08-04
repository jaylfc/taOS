import { render, screen, act, waitFor, fireEvent } from "@testing-library/react";
import { describe, it, expect, vi, beforeEach } from "vitest";
import { ActivityApp } from "./ActivityApp";

function mockFetch(
  responses: Record<string, { ok: boolean; status?: number; body: unknown }>,
) {
  return vi.fn().mockImplementation((input: string) => {
    const hit = responses[input];
    if (!hit) throw new Error(`Unmocked fetch: ${input}`);
    return Promise.resolve({
      ok: hit.ok,
      status: hit.status ?? (hit.ok ? 200 : 500),
      json: () => Promise.resolve(hit.body),
    });
  });
}

async function flush() {
  await act(async () => {
    await new Promise((r) => setTimeout(r, 0));
  });
}

function makeActivity(overrides: Record<string, unknown> = {}) {
  return {
    timestamp: Date.now(),
    hardware: {
      board: "Raspberry Pi 5",
      cpu: { model: "ARM GPIO rev1", arch: "aarch64", cores: 4, soc: "BCM2712" },
      ram_mb: 8192,
    },
    cpu: {
      cores: [
        { core: 0, load_percent: 12.5, freq_khz: 2400000, governor: "ondemand" },
        { core: 1, load_percent: 80, freq_khz: 2400000, governor: "ondemand" },
      ],
      load_avg: [0.42, 0.35, 0.3],
      overall_percent: 45.3,
    },
    memory: {
      total_mb: 8192,
      used_mb: 4096,
      available_mb: 4096,
      percent: 50,
      swap_total_mb: 0,
      swap_used_mb: 0,
      swap_percent: 0,
    },
    npu: {
      cores: [{ core: 0, load_percent: 0 }],
      freq_hz: 600000000,
      type: "rknpu",
      tops: 6,
    },
    gpu: { load: null, vram_percent: null, vram_used_mb: null, vram_total_mb: null, type: "none" },
    thermal: [{ name: "soc", temp_c: 48.6 }],
    zram: [],
    disk: {
      io_rate: { read_bps: 1048576, write_bps: 524288 },
      usage_percent: 55.5,
      total_gb: 32,
      used_gb: 18,
    },
    network: [{ name: "eth0", rx_bps: 1024, tx_bps: 2048, rx_total: 1000000, tx_total: 2000000 }],
    processes: [{ pid: 1, name: "taosd", user: "root", rss_mb: 128.5, cpu_percent: 1.2 }],
    ...overrides,
  };
}

function schedulerStatsBody() {
  return { submitted: 1, completed: 0, errors: 2, rejected: 1, active: 3, resources: [] };
}

function baseResponses(activity = makeActivity(), overrides: Record<string, { ok: boolean; status?: number; body: unknown }> = {}) {
  return {
    "/api/activity": { ok: true, body: activity },
    "/api/models/loaded": { ok: true, body: { loaded: [] } },
    "/api/scheduler/stats": { ok: true, body: schedulerStatsBody() },
    "/api/scheduler/tasks?limit=8": { ok: true, body: { tasks: [] } },
    "/api/cluster/workers": { ok: true, body: [] },
    ...overrides,
  };
}

describe("ActivityApp", () => {
  beforeEach(() => vi.restoreAllMocks());

  it("shows a loading spinner until activity data arrives", async () => {
    vi.stubGlobal("fetch", mockFetch(baseResponses()));
    const { container } = render(<ActivityApp windowId="win-1" />);
    expect(container.querySelector(".animate-spin")).not.toBeNull();
    expect(screen.queryByRole("heading", { name: "Activity" })).toBeNull();

    await flush();

    expect(screen.getByRole("heading", { name: "Activity" })).toBeTruthy();
    expect(container.querySelector(".animate-spin")).toBeNull();
  });

  it("fetches all mount endpoints and renders the header with hardware info", async () => {
    const fetchMock = mockFetch(baseResponses());
    vi.stubGlobal("fetch", fetchMock);
    render(<ActivityApp windowId="win-1" />);
    await flush();

    await waitFor(() =>
      expect(screen.getByRole("heading", { name: "Activity" })).toBeTruthy(),
    );
    expect(fetchMock).toHaveBeenCalledWith("/api/activity", expect.anything());
    expect(fetchMock).toHaveBeenCalledWith("/api/models/loaded", expect.anything());
    expect(fetchMock).toHaveBeenCalledWith("/api/scheduler/stats", expect.anything());
    expect(fetchMock).toHaveBeenCalledWith("/api/scheduler/tasks?limit=8", expect.anything());
    expect(fetchMock).toHaveBeenCalledWith("/api/cluster/workers", expect.anything());
    expect(
      screen.getByText(/Raspberry Pi 5.*aarch64.*8 GB RAM/i),
    ).toBeTruthy();
    expect(screen.getByRole("heading", { name: "Scheduler" })).toBeTruthy();
    expect(screen.getByText(/active 3/i)).toBeTruthy();
    expect(screen.getByText(/err 2/i)).toBeTruthy();
  });

  it("renders CPU per-core load bars and the overall load", async () => {
    vi.stubGlobal("fetch", mockFetch(baseResponses()));
    render(<ActivityApp windowId="win-1" />);
    await flush();

    expect(screen.getByRole("heading", { name: "CPU" })).toBeTruthy();
    expect(screen.getByText(/45% overall/i)).toBeTruthy();
    expect(screen.getByText(/0\.42 0\.35 0\.30/i)).toBeTruthy();
    expect(screen.getByText("C0 2.4G")).toBeTruthy();
    expect(screen.getByText("C1 2.4G")).toBeTruthy();
  });

  it("renders the NPU card with TOPS and clock when a non-none NPU is reported", async () => {
    vi.stubGlobal("fetch", mockFetch(baseResponses()));
    render(<ActivityApp windowId="win-1" />);
    await flush();

    expect(screen.getByRole("heading", { name: "NPU" })).toBeTruthy();
    expect(screen.getByText(/rknpu.*6 TOPS.*0\.60 GHz/i)).toBeTruthy();
    expect(screen.getByText("Core 0")).toBeTruthy();
  });

  it("renders the GPU card with load and VRAM when an NVIDIA GPU is reported", async () => {
    const activity = makeActivity({
      gpu: {
        type: "nvidia",
        model: "NVIDIA GeForce RTX 3060",
        load: { load_percent: 65, freq_hz: 1800000000 },
        vram_percent: 70,
        vram_used_mb: 8000,
        vram_total_mb: 12000,
      },
    });
    vi.stubGlobal("fetch", mockFetch(baseResponses(activity)));
    render(<ActivityApp windowId="win-1" />);
    await flush();

    expect(screen.getByRole("heading", { name: "GPU" })).toBeTruthy();
    expect(screen.getByText("Load", { exact: true })).toBeTruthy();
    expect(screen.getByText("VRAM", { exact: true })).toBeTruthy();
    expect(screen.getByText(/7\.8 GB \/ 11\.7 GB/i)).toBeTruthy();
  });

  it("hides the GPU card when the GPU type is none", async () => {
    vi.stubGlobal("fetch", mockFetch(baseResponses()));
    render(<ActivityApp windowId="win-1" />);
    await flush();

    expect(screen.queryByRole("heading", { name: "GPU" })).toBeNull();
    expect(screen.queryByText("Stats unavailable")).toBeNull();
  });

  it("renders the memory bar with RAM usage and the disk transfer rates", async () => {
    vi.stubGlobal("fetch", mockFetch(baseResponses()));
    render(<ActivityApp windowId="win-1" />);
    await flush();

    expect(screen.getByRole("heading", { name: "Memory" })).toBeTruthy();
    expect(screen.getByText("RAM")).toBeTruthy();
    expect(screen.getByText(/4\.0 GB \/ 8\.0 GB/i)).toBeTruthy();
    expect(screen.getByRole("heading", { name: "Disk" })).toBeTruthy();
    expect(screen.getByText("Read")).toBeTruthy();
    expect(screen.getByText("Write")).toBeTruthy();
    expect(screen.getByText(/1\.0 MB\/s/i)).toBeTruthy();
    expect(screen.getByText(/512\.0 KB\/s/i)).toBeTruthy();
  });

  it("renders loaded models from the controller by name", async () => {
    const activity = makeActivity();
    vi.stubGlobal(
      "fetch",
      mockFetch(
        baseResponses(activity, {
          "/api/models/loaded": {
            ok: true,
            body: {
              loaded: [
                { name: "phi3", backend: "llama.cpp", purpose: "chat", size_mb: 2048, ram_mb: 1536, vram_mb: null },
              ],
            },
          },
        }),
      ),
    );
    render(<ActivityApp windowId="win-1" />);
    await flush();

    expect(screen.getByText("Loaded Models (1)")).toBeTruthy();
    expect(screen.getByText("phi3")).toBeTruthy();
    expect(screen.getByText("chat")).toBeTruthy();
    expect(screen.getByText("1.5 GB")).toBeTruthy();
  });

  it("shows the loaded-models empty state when nothing is loaded", async () => {
    vi.stubGlobal("fetch", mockFetch(baseResponses()));
    render(<ActivityApp windowId="win-1" />);
    await flush();

    expect(screen.getByText("Loaded Models (0)")).toBeTruthy();
    expect(screen.getByText(/no models currently loaded/i)).toBeTruthy();
  });

  it("renders a cluster worker with status, backends and capabilities", async () => {
    const worker = {
      name: "fedora-lxc-test",
      url: "http://10.228.114.35:8080",
      last_heartbeat: Date.now() / 1000,
      hardware: { cpu: { model: "ARM GPIO rev1", cores: 4, soc: "BCM2712" }, ram_mb: 8192, npu: { type: "rknpu", tops: 6 } },
      backends: [{ name: "llama.cpp", type: "llama.cpp" }],
      capabilities: ["llama.cpp"],
    };
    vi.stubGlobal(
      "fetch",
      mockFetch(baseResponses(makeActivity(), { "/api/cluster/workers": { ok: true, body: [worker] } })),
    );
    render(<ActivityApp windowId="win-1" />);
    await flush();

    expect(screen.getByText("Cluster (1 worker)")).toBeTruthy();
    expect(screen.getByText("fedora-lxc-test")).toBeTruthy();
    expect(screen.getByLabelText("Status: online")).toBeTruthy();
    // The worker's backends and capabilities are each rendered once in the
    // Cluster panel and once in the Scheduler's hardware view.
    expect(screen.getAllByText("llama.cpp").length).toBeGreaterThanOrEqual(2);
  });

  it("shows the cluster empty state when no workers are registered", async () => {
    vi.stubGlobal("fetch", mockFetch(baseResponses()));
    render(<ActivityApp windowId="win-1" />);
    await flush();

    expect(screen.getByText("Cluster (0 workers)")).toBeTruthy();
    expect(screen.getByText(/no workers registered yet/i)).toBeTruthy();
    expect(screen.getByText(/how to add a worker/i)).toBeTruthy();
  });

  it("shows an unavailable state with the error when the activity fetch rejects", async () => {
    const fetchMock = vi.fn().mockImplementation((input: string) => {
      if (input === "/api/activity") return Promise.reject(new Error("Network down"));
      if (input === "/api/models/loaded") return Promise.resolve({ ok: true, status: 200, json: () => Promise.resolve({ loaded: [] }) });
      if (input === "/api/scheduler/stats")
        return Promise.resolve({ ok: true, status: 200, json: () => Promise.resolve(schedulerStatsBody()) });
      if (input === "/api/scheduler/tasks?limit=8")
        return Promise.resolve({ ok: true, status: 200, json: () => Promise.resolve({ tasks: [] }) });
      if (input === "/api/cluster/workers") return Promise.resolve({ ok: true, status: 200, json: () => Promise.resolve([]) });
      return Promise.resolve({ ok: true, status: 200, json: () => Promise.resolve({}) });
    });
    vi.stubGlobal("fetch", fetchMock);
    render(<ActivityApp windowId="win-1" />);
    await flush();

    expect(screen.getByText(/activity data unavailable/i)).toBeTruthy();
    expect(screen.getByText("Network down")).toBeTruthy();
  });

  it("re-fetches the activity data when the Refresh button is clicked", async () => {
    const fetchMock = mockFetch(baseResponses());
    vi.stubGlobal("fetch", fetchMock);
    render(<ActivityApp windowId="win-1" />);
    await flush();

    const activityCallsBefore = fetchMock.mock.calls.filter((c) => c[0] === "/api/activity").length;

    fireEvent.click(screen.getByRole("button", { name: "Refresh" }));
    await flush();

    const activityCallsAfter = fetchMock.mock.calls.filter((c) => c[0] === "/api/activity").length;
    expect(activityCallsAfter).toBe(activityCallsBefore + 1);
  });
});
