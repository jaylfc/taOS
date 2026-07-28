import { renderHook } from "@testing-library/react";
import { describe, it, expect, vi, afterEach } from "vitest";
import { usePushClickHandler } from "./use-push-click";

afterEach(() => {
  vi.restoreAllMocks();
});

describe("usePushClickHandler", () => {
  it("opens the Decisions app for a decision push click", () => {
    const openWindow = vi.fn();
    const { unmount } = renderHook(() => usePushClickHandler(openWindow));
    window.dispatchEvent(
      new MessageEvent("message", {
        data: { type: "taos-push:click", data: { source: "decisions" } },
      }),
    );
    expect(openWindow).toHaveBeenCalledWith("decisions", { w: 640, h: 620 }, undefined);
    unmount();
  });

  it("opens Settings for a disk_quota push click", () => {
    const openWindow = vi.fn();
    const { unmount } = renderHook(() => usePushClickHandler(openWindow));
    window.dispatchEvent(
      new MessageEvent("message", {
        data: { type: "taos-push:click", data: { source: "disk_quota" } },
      }),
    );
    expect(openWindow).toHaveBeenCalledWith("settings", { w: 800, h: 550 }, { section: "storage" });
    unmount();
  });

  it("passes meta through as props", () => {
    const openWindow = vi.fn();
    const { unmount } = renderHook(() => usePushClickHandler(openWindow));
    window.dispatchEvent(
      new MessageEvent("message", {
        data: { type: "taos-push:click", data: { source: "system.update" } },
      }),
    );
    expect(openWindow).toHaveBeenCalledWith(
      "settings",
      { w: 800, h: 550 },
      { section: "updates" },
    );
    unmount();
  });

  it("ignores non-push messages", () => {
    const openWindow = vi.fn();
    const { unmount } = renderHook(() => usePushClickHandler(openWindow));
    window.dispatchEvent(new MessageEvent("message", { data: { type: "other" } }));
    expect(openWindow).not.toHaveBeenCalled();
    unmount();
  });

  it("removes the listener on unmount", () => {
    const openWindow = vi.fn();
    const { unmount } = renderHook(() => usePushClickHandler(openWindow));
    unmount();
    window.dispatchEvent(
      new MessageEvent("message", {
        data: { type: "taos-push:click", data: { source: "decisions" } },
      }),
    );
    expect(openWindow).not.toHaveBeenCalled();
  });
});
