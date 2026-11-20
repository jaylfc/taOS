import { render, screen, fireEvent, act } from "@testing-library/react";
import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { ClusterApp } from "../ClusterApp";

describe("ClusterApp clipboard in non-secure context", () => {
  beforeEach(() => {
    Object.defineProperty(window, "isSecureContext", {
      value: false,
      configurable: true,
    });
    Object.defineProperty(navigator, "clipboard", {
      value: undefined,
      configurable: true,
    });
    const execCommand = vi.fn().mockReturnValue(false);
    Object.defineProperty(document, "execCommand", {
      value: execCommand,
      configurable: true,
      writable: true,
    });
    globalThis.fetch = vi.fn().mockResolvedValue({
      ok: true,
      json: async () => [
        {
          name: "test-worker",
          status: "online",
          last_heartbeat: Date.now(),
          hardware: {},
          tier_id: null,
          potential_capabilities: [],
          backends: [],
          capabilities: [],
        },
      ],
    }) as typeof fetch;
  });

  afterEach(() => {
    vi.restoreAllMocks();
  });

  it("copy button does not report success when nothing was copied", async () => {
    render(<ClusterApp windowId="test" />);
    await act(async () => {
      await new Promise((resolve) => setTimeout(resolve, 100));
    });
    const copyButton = screen.getByText("Copy name");
    await act(async () => {
      fireEvent.click(copyButton);
    });
    await act(async () => {
      await new Promise((resolve) => setTimeout(resolve, 50));
    });
    expect(screen.queryByText("Copied")).not.toBeInTheDocument();
  });
});

describe("ClusterApp Resources tab", () => {
  beforeEach(() => {
    globalThis.fetch = vi.fn().mockResolvedValue({
      ok: true,
      json: async () => ({ resources: [], errors: [], generated_at: 0 }),
    }) as typeof fetch;
  });

  afterEach(() => {
    vi.restoreAllMocks();
  });

  it("has a Resources tab that requests /api/cluster/resources when selected", async () => {
    render(<ClusterApp windowId="test" />);
    const tab = screen.getByRole("tab", { name: "Show cluster resources" });
    expect(tab).toHaveAttribute("aria-selected", "false");
    expect(globalThis.fetch).not.toHaveBeenCalledWith("/api/cluster/resources", expect.anything());
    await act(async () => {
      fireEvent.click(tab);
    });
    await act(async () => {
      await new Promise((resolve) => setTimeout(resolve, 50));
    });
    expect(tab).toHaveAttribute("aria-selected", "true");
    expect(globalThis.fetch).toHaveBeenCalledWith("/api/cluster/resources", expect.anything());
    expect(screen.getByRole("tabpanel", { name: "Cluster resources" })).toBeInTheDocument();
  });
});

describe("ClusterApp drain", () => {
  const onlineWorker = () => ({
    name: "online-worker",
    status: "online",
    last_heartbeat: Date.now(),
    url: "http://localhost:8000",
    hardware: {},
    tier_id: null,
    potential_capabilities: [],
    backends: [],
    capabilities: [],
    models: [],
  });

  const drainingWorker = () => ({
    name: "draining-worker",
    status: "draining",
    last_heartbeat: Date.now(),
    url: "http://localhost:8001",
    hardware: {},
    tier_id: null,
    potential_capabilities: [],
    backends: [],
    capabilities: [],
    models: [],
  });

  beforeEach(() => {
    globalThis.fetch = vi.fn().mockResolvedValue({
      ok: true,
      json: async () => [onlineWorker()],
    }) as typeof fetch;
  });

  afterEach(() => {
    vi.restoreAllMocks();
  });

  it("enables Drain for an online worker and posts to /drain on confirm", async () => {
    vi.spyOn(window, "confirm").mockReturnValue(true);
    render(<ClusterApp windowId="test" />);
    await act(async () => {
      await new Promise((resolve) => setTimeout(resolve, 100));
    });
    const drain = screen.getByRole("button", { name: /Drain/ });
    expect(drain).not.toBeDisabled();
    fireEvent.click(drain);
    expect(window.confirm).toHaveBeenCalledWith(
      "Drain online-worker? No new tasks will be routed; running tasks finish.",
    );
    await act(async () => {
      await new Promise((resolve) => setTimeout(resolve, 50));
    });
    expect(globalThis.fetch).toHaveBeenCalledWith(
      "/api/cluster/workers/online-worker/drain",
      expect.objectContaining({ method: "POST", body: JSON.stringify({ graceful: true }) }),
    );
  });

  it("shows Cancel drain for a draining worker and posts to /cancel-drain", async () => {
    vi.spyOn(window, "confirm").mockReturnValue(true);
    globalThis.fetch = vi.fn().mockResolvedValue({
      ok: true,
      json: async () => [drainingWorker()],
    }) as typeof fetch;
    render(<ClusterApp windowId="test" />);
    await act(async () => {
      await new Promise((resolve) => setTimeout(resolve, 100));
    });
    const cancel = screen.getByRole("button", { name: /Cancel drain/ });
    expect(cancel).not.toBeDisabled();
    fireEvent.click(cancel);
    await act(async () => {
      await new Promise((resolve) => setTimeout(resolve, 50));
    });
    expect(globalThis.fetch).toHaveBeenCalledWith(
      "/api/cluster/workers/draining-worker/cancel-drain",
      expect.objectContaining({ method: "POST" }),
    );
  });
});
