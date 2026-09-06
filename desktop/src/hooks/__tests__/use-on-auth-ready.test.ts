import { describe, it, expect, vi, beforeEach } from "vitest";
import { renderHook, act } from "@testing-library/react";
import { useOnAuthReady } from "../use-on-auth-ready";
import { useAuthReadyStore } from "@/stores/auth-ready-store";

beforeEach(() => {
  useAuthReadyStore.setState({ ready: false });
});

describe("useOnAuthReady (#1601, #1603)", () => {
  it("does not run the callback while logged out", () => {
    const callback = vi.fn();
    renderHook(() => useOnAuthReady(callback));
    expect(callback).not.toHaveBeenCalled();
  });

  it("runs the callback once auth becomes ready", () => {
    const callback = vi.fn();
    renderHook(() => useOnAuthReady(callback));

    act(() => {
      useAuthReadyStore.setState({ ready: true });
    });

    expect(callback).toHaveBeenCalledTimes(1);
  });

  it("does not re-run while still authenticated (no duplicate restores per session)", () => {
    const callback = vi.fn();
    const { rerender } = renderHook(() => useOnAuthReady(callback));

    act(() => {
      useAuthReadyStore.setState({ ready: true });
    });
    rerender();
    rerender();

    expect(callback).toHaveBeenCalledTimes(1);
  });

  it("runs the callback again after a logout/login cycle", () => {
    // This is the exact bug: a theme/settings restore that only ever fires
    // once on mount is never retried after the user logs back in following
    // a logout. useOnAuthReady must re-arm on the falling edge so the next
    // rising edge (the next login) restores the new session's data.
    const callback = vi.fn();
    renderHook(() => useOnAuthReady(callback));

    act(() => {
      useAuthReadyStore.setState({ ready: true });
    });
    expect(callback).toHaveBeenCalledTimes(1);

    act(() => {
      useAuthReadyStore.setState({ ready: false }); // logout
    });
    act(() => {
      useAuthReadyStore.setState({ ready: true }); // login again
    });

    expect(callback).toHaveBeenCalledTimes(2);
  });
});
