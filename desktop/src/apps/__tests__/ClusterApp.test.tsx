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

describe("ClusterApp fetch failures", () => {
  const worker = () => ({
    name: "node-1",
    status: "online",
    last_heartbeat: Date.now(),
    hardware: {},
    tier_id: null,
    potential_capabilities: [],
    backends: [],
    capabilities: [],
  });

  const okFetch = () =>
    vi.fn().mockResolvedValue({ ok: true, json: async () => [worker()] }) as typeof fetch;

  beforeEach(() => {
    vi.useFakeTimers();
    globalThis.fetch = okFetch();
  });

  afterEach(() => {
    vi.useRealTimers();
    vi.restoreAllMocks();
  });

  it("shows an alert on a 500 poll, keeps the stale node, and clears on ok", async () => {
    render(<ClusterApp windowId="test" />);
    await act(async () => {
      await Promise.resolve();
    });
    expect(screen.getAllByText("node-1")).not.toHaveLength(0);
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();

    globalThis.fetch = vi.fn().mockResolvedValue({ ok: false, status: 500 }) as typeof fetch;
    await act(async () => {
      vi.advanceTimersByTime(10_000);
      await Promise.resolve();
    });

    const alert = screen.getByRole("alert");
    expect(alert).toHaveTextContent(/Could not refresh nodes \(500\)/);
    expect(screen.getAllByText("node-1")).not.toHaveLength(0);

    globalThis.fetch = okFetch();
    await act(async () => {
      vi.advanceTimersByTime(10_000);
      await Promise.resolve();
    });
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
  });

  it("shows an alert when the poll is rejected", async () => {
    render(<ClusterApp windowId="test" />);
    await act(async () => {
      await Promise.resolve();
    });
    expect(screen.getAllByText("node-1")).not.toHaveLength(0);

    globalThis.fetch = vi
      .fn()
      .mockRejectedValue(new TypeError("failed to fetch")) as typeof fetch;
    await act(async () => {
      vi.advanceTimersByTime(10_000);
      await Promise.resolve();
    });

    const alert = screen.getByRole("alert");
    expect(alert).toHaveTextContent("Controller unreachable. Showing the last known list.");
    expect(screen.getAllByText("node-1")).not.toHaveLength(0);

    globalThis.fetch = okFetch();
    await act(async () => {
      vi.advanceTimersByTime(10_000);
      await Promise.resolve();
    });
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
  });
});
