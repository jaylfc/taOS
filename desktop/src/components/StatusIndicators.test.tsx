import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { StatusIndicators } from "./StatusIndicators";

const mockOpenWindow = vi.fn();
vi.mock("@/stores/process-store", () => ({
  useProcessStore: (selector: (s: { openWindow: typeof mockOpenWindow }) => unknown) =>
    selector({ openWindow: mockOpenWindow }),
}));

vi.mock("@/registry/app-registry", () => ({
  getApp: (id: string) =>
    id === "dashboard" ? { id: "dashboard", defaultSize: { w: 1100, h: 720 } } : undefined,
}));

function mockSystemApi(json: Record<string, unknown>) {
  vi.spyOn(globalThis, "fetch").mockResolvedValue(
    new Response(JSON.stringify(json), {
      status: 200,
      headers: { "content-type": "application/json" },
    }),
  );
}

describe("StatusIndicators", () => {
  beforeEach(() => {
    vi.restoreAllMocks();
    mockOpenWindow.mockClear();
  });

  it("renders CPU and RAM indicators with the fetched data", async () => {
    mockSystemApi({
      resources: { cpu_pct: 42, ram_pct: 73, ram_used_mb: 8192 },
      hardware: { gpu: { type: "none" }, npu: { type: "none" } },
    });

    render(<StatusIndicators />);

    await waitFor(() => {
      expect(screen.getByLabelText(/cpu usage/i)).toBeInTheDocument();
    });

    expect(screen.getByLabelText(/ram usage/i)).toBeInTheDocument();
  });

  it("opens the dashboard when clicked", async () => {
    mockSystemApi({
      resources: { cpu_pct: 10, ram_pct: 20 },
      hardware: { gpu: { type: "none" }, npu: { type: "none" } },
    });

    render(<StatusIndicators />);

    const btn = await screen.findByRole("button", { name: /open dashboard/i });
    fireEvent.click(btn);

    expect(mockOpenWindow).toHaveBeenCalledWith("dashboard", { w: 1100, h: 720 });
  });

  it("renders a VRAM indicator when a GPU is present", async () => {
    mockSystemApi({
      resources: { cpu_pct: 15, ram_pct: 30, vram_pct: 55 },
      hardware: { gpu: { type: "nvidia", vram_mb: 8192 }, npu: { type: "none" } },
    });

    render(<StatusIndicators />);

    await waitFor(() => {
      expect(screen.getByLabelText(/vram usage/i)).toBeInTheDocument();
    });
  });
});
