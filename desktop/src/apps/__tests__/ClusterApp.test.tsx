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
