import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { StatusIndicators } from "./StatusIndicators";

const mockOpenWindow = vi.fn();
const mockGetApp = vi.fn().mockReturnValue({ defaultSize: { w: 1100, h: 720 } });

vi.mock("@/stores/process-store", () => ({
  useProcessStore: (sel: (s: { openWindow: typeof mockOpenWindow }) => unknown) =>
    sel({ openWindow: mockOpenWindow }),
}));

vi.mock("@/registry/app-registry", () => ({
  getApp: (id: string) => mockGetApp(id),
}));

const systemResponse = {
  resources: {
    cpu_percent: 42,
    ram_percent: 65,
    ram_used_mb: 8192,
  },
  hardware: {
    gpu: { type: "nvidia", vram_mb: 12288 },
    npu: { type: "none" },
  },
};

describe("StatusIndicators", () => {
  let origFetch: typeof globalThis.fetch;

  beforeEach(() => {
    vi.clearAllMocks();
    origFetch = globalThis.fetch;
    globalThis.fetch = vi.fn().mockResolvedValue({
      ok: true,
      headers: new Map([["content-type", "application/json"]]),
      json: async () => systemResponse,
    }) as unknown as typeof fetch;
  });

  afterEach(() => {
    globalThis.fetch = origFetch;
  });

  it("renders CPU and RAM indicators after fetching system data", async () => {
    render(<StatusIndicators />);
    await waitFor(() => {
      expect(screen.getByLabelText(/cpu usage/i)).toBeInTheDocument();
    });
    expect(screen.getByLabelText(/ram usage/i)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /open dashboard/i })).toBeInTheDocument();
  });

  it("does not render VRAM indicator when GPU is absent", async () => {
    globalThis.fetch = vi.fn().mockResolvedValue({
      ok: true,
      headers: new Map([["content-type", "application/json"]]),
      json: async () => ({
        resources: { cpu_percent: 10, ram_percent: 20 },
        hardware: { gpu: { type: "none" }, npu: { type: "none" } },
      }),
    }) as unknown as typeof fetch;

    render(<StatusIndicators />);
    await waitFor(() => {
      expect(screen.getByLabelText(/cpu usage/i)).toBeInTheDocument();
    });
    expect(screen.queryByLabelText(/vram usage/i)).not.toBeInTheDocument();
  });

  it("calls openWindow when the dashboard button is clicked", async () => {
    render(<StatusIndicators />);
    await waitFor(() => {
      expect(screen.getByRole("button", { name: /open dashboard/i })).toBeInTheDocument();
    });
    fireEvent.click(screen.getByRole("button", { name: /open dashboard/i }));
    expect(mockGetApp).toHaveBeenCalledWith("dashboard");
    expect(mockOpenWindow).toHaveBeenCalledWith("dashboard", { w: 1100, h: 720 });
  });
});
