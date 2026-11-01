import { describe, it, expect, vi, afterEach, beforeEach } from "vitest";
import { renderHook, act } from "@testing-library/react";
import { findAgentDm, useFirstChat } from "./first-chat";

const dm = (id: string, members: string[], type = "dm") => ({ id, type, members });

describe("findAgentDm", () => {
  it("finds the user's two-person DM with that agent only", () => {
    const channels = [
      dm("g", ["user", "sage", "other"]),
      dm("t", ["user", "sage"], "topic"),
      dm("o", ["user", "other"]),
      dm("d", ["user", "sage"]),
    ];
    expect(findAgentDm(channels, "sage")?.id).toBe("d");
    expect(findAgentDm(channels.slice(0, 3), "sage")).toBeUndefined();
  });
});

function statusFetch(body: unknown, status = 200) {
  return vi.fn(async (url: string) => {
    if (!url.startsWith("/api/agents/sage/deploy-status")) throw new Error(`unexpected ${url}`);
    return new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } });
  });
}

describe("useFirstChat", () => {
  beforeEach(() => { vi.stubGlobal("fetch", statusFetch({ status: "deploying", name: "sage" })); });
  afterEach(() => { vi.useRealTimers(); vi.unstubAllGlobals(); });

  it("re-reads channels until the DM appears, then opens it once and stops", async () => {
    vi.useFakeTimers();
    const refresh = vi.fn(async () => {});
    const open = vi.fn();
    const { result, rerender } = renderHook(
      ({ channels }) => useFirstChat("sage", channels, refresh, open),
      { initialProps: { channels: [dm("o", ["user", "other"])] } },
    );
    expect(result.current.waiting).toBe(true);
    await act(async () => { vi.advanceTimersByTime(6000); });
    expect(refresh).toHaveBeenCalledTimes(2);

    rerender({ channels: [dm("o", ["user", "other"]), dm("d", ["user", "sage"])] });
    expect(open).toHaveBeenCalledExactlyOnceWith("d");
    expect(result.current).toEqual({ waiting: false, channelId: "d", failure: undefined });
    // A fresh channel list (every poll, every message) must not re-open it.
    rerender({ channels: [dm("d", ["user", "sage"]), dm("x", ["user", "x"])] });
    expect(open).toHaveBeenCalledOnce();
    await act(async () => { vi.advanceTimersByTime(9000); });
    expect(refresh).toHaveBeenCalledTimes(2);
  });

  it("does nothing without an agent", async () => {
    vi.useFakeTimers();
    const refresh = vi.fn(async () => {});
    const open = vi.fn();
    const { result } = renderHook(() => useFirstChat(undefined, [dm("d", ["user", "sage"])], refresh, open));
    await act(async () => { vi.advanceTimersByTime(9000); });
    expect(refresh).not.toHaveBeenCalled();
    expect(open).not.toHaveBeenCalled();
    expect(result.current).toEqual({ waiting: false, channelId: undefined, failure: undefined });
    expect(fetch).not.toHaveBeenCalled();
  });

  it("stops waiting and names the error when the deploy failed", async () => {
    vi.useFakeTimers();
    const f = statusFetch({ status: "failed", name: "sage", error: "No room for another agent" });
    vi.stubGlobal("fetch", f);
    const refresh = vi.fn(async () => {});
    const { result } = renderHook(() => useFirstChat("sage", [], refresh, vi.fn()));
    expect(result.current.waiting).toBe(true);
    await act(async () => { vi.advanceTimersByTime(3000); });
    expect(result.current.waiting).toBe(false);
    expect(result.current.failure).toBe("No room for another agent.");
    const calls = refresh.mock.calls.length;
    await act(async () => { vi.advanceTimersByTime(9000); });
    expect(refresh).toHaveBeenCalledTimes(calls);
    expect(f).toHaveBeenCalledOnce();
  });

  it("keeps waiting while the deploy is running or its record is gone", async () => {
    vi.useFakeTimers();
    for (const [body, status] of [[{ status: "deploying" }, 200], [{ error: "No deploy task" }, 404]] as const) {
      vi.stubGlobal("fetch", statusFetch(body, status));
      const refresh = vi.fn(async () => {});
      const { result, unmount } = renderHook(() => useFirstChat("sage", [], refresh, vi.fn()));
      await act(async () => { vi.advanceTimersByTime(9000); });
      expect(result.current).toEqual({ waiting: true, channelId: undefined, failure: undefined });
      expect(refresh).toHaveBeenCalledTimes(3);
      unmount();
    }
  });

  it("stops saying it is setting up once it gives up", async () => {
    vi.useFakeTimers();
    const refresh = vi.fn(async () => {});
    const { result } = renderHook(() => useFirstChat("sage", [], refresh, vi.fn()));
    await act(async () => { vi.advanceTimersByTime(10 * 60 * 1000 + 6000); });
    expect(result.current.waiting).toBe(false);
    expect(result.current.failure).toMatch(/taking longer/i);
  });
});
