import { describe, it, expect, vi, afterEach } from "vitest";
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

describe("useFirstChat", () => {
  afterEach(() => { vi.useRealTimers(); });

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
    expect(result.current).toEqual({ waiting: false, channelId: "d" });
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
    expect(result.current).toEqual({ waiting: false, channelId: undefined });
  });
});
