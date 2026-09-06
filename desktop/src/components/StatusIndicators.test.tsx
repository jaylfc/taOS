import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";

const openWindowMock = vi.fn();

vi.mock("@/stores/process-store", () => ({
  useProcessStore: (sel: (s: { openWindow: () => void }) => unknown) =>
    sel({ openWindow: openWindowMock }),
}));

vi.mock("@/registry/app-registry", () => ({
  getApp: (id: string) =>
    id === "dashboard"
      ? { id: "dashboard", name: "Activity", defaultSize: { w: 1100, h: 720 } }
      : undefined,
}));

vi.mock("lucide-react", () => {
  const mk = (name: string) =>
    function MockIcon({ size }: { size?: number }) {
      return <span data-testid={`icon-${name}`} data-size={size} />;
    };
  return {
    Cpu: mk("cpu"),
    MemoryStick: mk("memory-stick"),
    Zap: mk("zap"),
    CircuitBoard: mk("circuit-board"),
  };
});

import { StatusIndicators } from "./StatusIndicators";

let originalFetch: typeof globalThis.fetch;

function mockFetch(json: Record<string, unknown>) {
  globalThis.fetch = vi.fn().mockResolvedValue({
    ok: true,
    headers: new Map([["content-type", "application/json"]]) as unknown as Headers,
    json: () => Promise.resolve(json),
  }) as unknown as typeof fetch;
}

describe("StatusIndicators", () => {
  beforeEach(() => {
    originalFetch = globalThis.fetch;
    openWindowMock.mockClear();
  });

  afterEach(() => {
    globalThis.fetch = originalFetch;
    vi.restoreAllMocks();
  });

  it("renders nothing while data is loading", () => {
    globalThis.fetch = vi.fn().mockImplementation(() => new Promise(() => {})) as unknown as typeof fetch;
    const { container } = render(<StatusIndicators />);
    expect(container.innerHTML).toBe("");
  });

  it("renders CPU and RAM indicators after fetch resolves", async () => {
    mockFetch({
      resources: { cpu_percent: 42, ram_percent: 65 },
      hardware: { gpu: { type: "none" }, npu: { type: "none" } },
    });

    render(<StatusIndicators />);
    await waitFor(() => {
      expect(screen.getByRole("button", { name: /open dashboard/i })).toBeInTheDocument();
    });

    expect(screen.getByTitle("CPU: 42%")).toBeInTheDocument();
    expect(screen.getByTitle("RAM: 65%")).toBeInTheDocument();
  });

  it("shows the correct accessible labels for CPU and RAM with values", async () => {
    mockFetch({
      resources: { cpu_percent: 42, ram_percent: 65 },
      hardware: { gpu: { type: "none" }, npu: { type: "none" } },
    });

    render(<StatusIndicators />);
    await waitFor(() => screen.getByRole("button", { name: /open dashboard/i }));

    const cpuEl = screen.getByTitle("CPU: 42%");
    const ramEl = screen.getByTitle("RAM: 65%");

    expect(cpuEl).toHaveAttribute("aria-label", "CPU usage 42 percent");
    expect(ramEl).toHaveAttribute("aria-label", "RAM usage 65 percent");
  });

  it("shows VRAM indicator when GPU is present", async () => {
    mockFetch({
      resources: { cpu_percent: 30, ram_percent: 50, vram_percent: 75 },
      hardware: { gpu: { type: "nvidia", vram_mb: 8192 }, npu: { type: "none" } },
    });

    render(<StatusIndicators />);
    await waitFor(() => screen.getByRole("button", { name: /open dashboard/i }));

    expect(screen.getByTitle("CPU: 30%")).toBeInTheDocument();
    expect(screen.getByTitle("RAM: 50%")).toBeInTheDocument();
    expect(screen.getByTitle("VRAM: 75%")).toBeInTheDocument();
  });

  it("shows NPU indicator when NPU is present", async () => {
    mockFetch({
      resources: { cpu_percent: 20, ram_percent: 40, npu_pct: 15 },
      hardware: { gpu: { type: "none" }, npu: { type: "apple" } },
    });

    render(<StatusIndicators />);
    await waitFor(() => screen.getByRole("button", { name: /open dashboard/i }));

    expect(screen.getByTitle("CPU: 20%")).toBeInTheDocument();
    expect(screen.getByTitle("RAM: 40%")).toBeInTheDocument();
    expect(screen.getByTitle("NPU: 15%")).toBeInTheDocument();
    expect(screen.queryByTitle(/VRAM/)).toBeNull();
  });

  it("shows both VRAM and NPU when GPU and NPU are present", async () => {
    mockFetch({
      resources: { cpu_percent: 55, ram_pct: 70, vram_pct: 80, npu_pct: 25 },
      hardware: { gpu: { type: "nvidia", vram_mb: 16384 }, npu: { type: "qualcomm" } },
    });

    render(<StatusIndicators />);
    await waitFor(() => screen.getByRole("button", { name: /open dashboard/i }));

    expect(screen.getByTitle("CPU: 55%")).toBeInTheDocument();
    expect(screen.getByTitle("RAM: 70%")).toBeInTheDocument();
    expect(screen.getByTitle("VRAM: 80%")).toBeInTheDocument();
    expect(screen.getByTitle("NPU: 25%")).toBeInTheDocument();
  });

  it("shows unknown usage when values are null", async () => {
    mockFetch({
      resources: {},
      hardware: { gpu: { type: "none" }, npu: { type: "none" } },
    });

    render(<StatusIndicators />);
    await waitFor(() => screen.getByRole("button", { name: /open dashboard/i }));

    const cpuEl = screen.getByTitle("CPU: \u2014");
    const ramEl = screen.getByTitle("RAM: \u2014");

    expect(cpuEl).toHaveAttribute("aria-label", "CPU usage unknown");
    expect(ramEl).toHaveAttribute("aria-label", "RAM usage unknown");
  });

  it("calls openWindow when the dashboard button is clicked", async () => {
    mockFetch({
      resources: { cpu_percent: 10, ram_percent: 20 },
      hardware: { gpu: { type: "none" }, npu: { type: "none" } },
    });

    render(<StatusIndicators />);
    await waitFor(() => screen.getByRole("button", { name: /open dashboard/i }));

    fireEvent.click(screen.getByRole("button", { name: /open dashboard/i }));
    expect(openWindowMock).toHaveBeenCalledWith("dashboard", { w: 1100, h: 720 });
  });

  it("applies compact class when compact prop is true", async () => {
    mockFetch({
      resources: { cpu_percent: 10, ram_percent: 20 },
      hardware: { gpu: { type: "none" }, npu: { type: "none" } },
    });

    render(<StatusIndicators compact />);
    await waitFor(() => screen.getByRole("button", { name: /open dashboard/i }));

    const btn = screen.getByRole("button", { name: /open dashboard/i });
    expect(btn.className).not.toContain("px-1");
  });

  it("applies non-compact padding class when compact is false", async () => {
    mockFetch({
      resources: { cpu_percent: 10, ram_percent: 20 },
      hardware: { gpu: { type: "none" }, npu: { type: "none" } },
    });

    render(<StatusIndicators compact={false} />);
    await waitFor(() => screen.getByRole("button", { name: /open dashboard/i }));

    const btn = screen.getByRole("button", { name: /open dashboard/i });
    expect(btn.className).toContain("px-1");
  });
});
