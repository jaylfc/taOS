import { renderHook, waitFor } from "@testing-library/react";
import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { useAgentShortcuts } from "./use-agent-shortcuts";

describe("useAgentShortcuts", () => {
  beforeEach(() => {
    vi.stubGlobal("fetch", vi.fn());
  });
  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("calls the correct endpoint and returns parsed shortcuts", async () => {
    const mockShortcuts = [
      { idx: 0, label: "Container shell", icon: "terminal", kind: "container-terminal", requires_capability: "agent.shell" },
      { idx: 1, label: "OpenClaw agent", icon: "tui", kind: "tui", requires_capability: "agent.terminal" },
    ];
    (fetch as ReturnType<typeof vi.fn>).mockResolvedValueOnce({
      ok: true,
      json: async () => mockShortcuts,
    });

    const { result } = renderHook(() => useAgentShortcuts("abc123"));
    await waitFor(() => expect(result.current.loading).toBe(false));

    expect(fetch).toHaveBeenCalledWith("/api/agents/abc123/shortcuts", expect.objectContaining({ signal: expect.any(AbortSignal) }));
    expect(result.current.shortcuts).toEqual(mockShortcuts);
    expect(result.current.error).toBeNull();
  });

  it("surfaces errors when fetch fails", async () => {
    (fetch as ReturnType<typeof vi.fn>).mockResolvedValueOnce({
      ok: false,
      status: 403,
      statusText: "Forbidden",
    });

    const { result } = renderHook(() => useAgentShortcuts("agent-x"));
    await waitFor(() => expect(result.current.loading).toBe(false));

    expect(result.current.shortcuts).toEqual([]);
    expect(result.current.error).toMatch(/403/);
  });

  it("surfaces network errors", async () => {
    (fetch as ReturnType<typeof vi.fn>).mockRejectedValueOnce(new Error("Network error"));

    const { result } = renderHook(() => useAgentShortcuts("agent-y"));
    await waitFor(() => expect(result.current.loading).toBe(false));

    expect(result.current.shortcuts).toEqual([]);
    expect(result.current.error).toBe("Network error");
  });
});
