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
