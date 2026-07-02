import { renderHook } from "@testing-library/react";
import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { useDesktopControl } from "./use-desktop-control";
import { useProcessStore } from "@/stores/process-store";

const reset = () => useProcessStore.setState({ windows: [], nextZIndex: 1 });

describe("useDesktopControl taos:window receiver", () => {
  beforeEach(reset);
  afterEach(() => {
    vi.restoreAllMocks();
  });

  it("closes every window for an appId, not just the first", () => {
    renderHook(() => useDesktopControl());
    const first = useProcessStore.getState().openWindow("coding-studio", { w: 800, h: 600 });
    const second = useProcessStore
      .getState()
      .openWindow("coding-studio", { w: 800, h: 600 }, undefined, { forceNew: true });
    const other = useProcessStore.getState().openWindow("settings", { w: 400, h: 300 });

    window.dispatchEvent(
      new CustomEvent("taos:window", { detail: { action: "close", appId: "coding-studio" } }),
    );

    const windows = useProcessStore.getState().windows;
    expect(windows.find((w) => w.id === first)!.closing).toBe(true);
    expect(windows.find((w) => w.id === second)!.closing).toBe(true);
    expect(windows.find((w) => w.id === other)!.closing).toBeFalsy();
  });

  it("closes by app id using the op/app alias payload (the reported bug shape)", () => {
    renderHook(() => useDesktopControl());
    const id = useProcessStore.getState().openWindow("coding-studio", { w: 800, h: 600 });

    window.dispatchEvent(
      new CustomEvent("taos:window", { detail: { op: "close", app: "coding-studio" } }),
    );

    expect(useProcessStore.getState().windows.find((w) => w.id === id)!.closing).toBe(true);
  });

  it("warns and ignores a payload with neither action nor op", () => {
    renderHook(() => useDesktopControl());
    const id = useProcessStore.getState().openWindow("coding-studio", { w: 800, h: 600 });
    const warn = vi.spyOn(console, "warn").mockImplementation(() => {});

    window.dispatchEvent(
      new CustomEvent("taos:window", { detail: { appId: "coding-studio" } }),
    );

    expect(warn).toHaveBeenCalledTimes(1);
    expect(warn.mock.calls[0][1]).toEqual({ appId: "coding-studio" });
    expect(useProcessStore.getState().windows.find((w) => w.id === id)!.closing).toBeFalsy();
  });

  it("warns and ignores an unknown action/op (not in the allowlist)", () => {
    renderHook(() => useDesktopControl());
    const id = useProcessStore.getState().openWindow("coding-studio", { w: 800, h: 600 });
    const warn = vi.spyOn(console, "warn").mockImplementation(() => {});

    window.dispatchEvent(
      new CustomEvent("taos:window", { detail: { op: "shutdown", app: "coding-studio" } }),
    );

    expect(warn).toHaveBeenCalledTimes(1);
    expect(useProcessStore.getState().windows.find((w) => w.id === id)!.closing).toBeFalsy();
  });

  it("removes the event listener on unmount", () => {
    const { unmount } = renderHook(() => useDesktopControl());
    const id = useProcessStore.getState().openWindow("coding-studio", { w: 800, h: 600 });
    unmount();

    window.dispatchEvent(
      new CustomEvent("taos:window", { detail: { action: "close", appId: "coding-studio" } }),
    );

    expect(useProcessStore.getState().windows.find((w) => w.id === id)!.closing).toBeFalsy();
  });
});
