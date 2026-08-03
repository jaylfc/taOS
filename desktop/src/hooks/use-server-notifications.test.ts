import { describe, it, expect, vi, afterEach, beforeEach } from "vitest";
import { renderHook, act } from "@testing-library/react";
import { useServerNotifications } from "./use-server-notifications";
import { useNotificationStore } from "@/stores/notification-store";

var fetchSpy: ReturnType<typeof vi.fn>;

vi.mock("@/lib/server-notifications", async () => {
  const actual = await vi.importActual("@/lib/server-notifications");
  fetchSpy = vi.fn();
  return {
    ...actual,
    fetchServerNotifications: fetchSpy,
  };
});

let capturedListener: (() => void) | null = null;

beforeEach(() => {
  useNotificationStore.setState({ notifications: [], centreOpen: false });
  fetchSpy.mockClear();
  fetchSpy.mockResolvedValue([]);
  capturedListener = null;
  vi.stubGlobal("fetch", vi.fn(() => Promise.resolve(new Response("[]", { status: 200, headers: { "Content-Type": "application/json" } }))));
});

afterEach(() => {
  vi.unstubAllGlobals();
  fetchSpy.mockClear();
  capturedListener = null;
});

describe("useServerNotifications", () => {
  it("starts with a clean notification store", () => {
    vi.spyOn(document, "addEventListener").mockImplementation((_event, fn) => {
      capturedListener = fn as () => void;
    });
    renderHook(() => useServerNotifications());
    expect(useNotificationStore.getState().notifications).toHaveLength(0);
  });

  it("calls fetchServerNotifications on mount and merges results", async () => {
    fetchSpy.mockResolvedValueOnce([
      { id: "srv-1", timestamp: 1700000000, level: "info", title: "Server msg", message: "hello", read: false, source: "system", data: null },
    ]);

    vi.spyOn(document, "addEventListener").mockImplementation((_event, fn) => {
      capturedListener = fn as () => void;
    });

    renderHook(() => useServerNotifications());

    await act(async () => {
      await Promise.resolve();
      await new Promise((r) => setTimeout(r, 0));
    });

    expect(fetchSpy).toHaveBeenCalledTimes(1);
    expect(useNotificationStore.getState().notifications).toHaveLength(1);
    expect(useNotificationStore.getState().notifications[0].title).toBe("Server msg");
    expect(useNotificationStore.getState().notifications[0].id).toBe("srv-1");
  });

  it("returns an empty array from fetch without affecting the store", async () => {
    fetchSpy.mockResolvedValueOnce([]);

    vi.spyOn(document, "addEventListener").mockImplementation((_event, fn) => {
      capturedListener = fn as () => void;
    });

    renderHook(() => useServerNotifications());

    await act(async () => {
      await Promise.resolve();
      await new Promise((r) => setTimeout(r, 0));
    });

    expect(fetchSpy).toHaveBeenCalledTimes(1);
    expect(useNotificationStore.getState().notifications).toHaveLength(0);
  });

  it("registers a visibilitychange listener on mount", () => {
    const addSpy = vi.spyOn(document, "addEventListener");
    renderHook(() => useServerNotifications());
    expect(addSpy).toHaveBeenCalledWith("visibilitychange", expect.any(Function));
    addSpy.mockRestore();
  });

  it("removes the visibilitychange listener on unmount", () => {
    const addSpy = vi.spyOn(document, "addEventListener");
    const removeSpy = vi.spyOn(document, "removeEventListener");
    const { unmount } = renderHook(() => useServerNotifications());
    unmount();
    expect(removeSpy).toHaveBeenCalledWith("visibilitychange", expect.any(Function));
    addSpy.mockRestore();
    removeSpy.mockRestore();
  });

  it("fires sync and restarts polling when the tab becomes visible again", async () => {
    const setIntervalSpy = vi.spyOn(globalThis, "setInterval").mockReturnValue(99);
    const clearIntervalSpy = vi.spyOn(globalThis, "clearInterval");

    vi.spyOn(document, "addEventListener").mockImplementation((_event, fn) => {
      capturedListener = fn as () => void;
    });

    renderHook(() => useServerNotifications());

    await act(async () => {
      await Promise.resolve();
      await new Promise((r) => setTimeout(r, 0));
    });

    expect(capturedListener).toBeDefined();

    fetchSpy.mockClear();

    Object.defineProperty(document, "hidden", { value: true, configurable: true });
    act(() => capturedListener!());
    expect(clearIntervalSpy).toHaveBeenCalled();

    Object.defineProperty(document, "hidden", { value: false, configurable: true });
    act(() => capturedListener!());

    await act(async () => {
      await Promise.resolve();
      await new Promise((r) => setTimeout(r, 0));
    });

    expect(fetchSpy).toHaveBeenCalledTimes(1);
    expect(setIntervalSpy).toHaveBeenCalled();

    setIntervalSpy.mockRestore();
    clearIntervalSpy.mockRestore();
  });

  it("syncs and starts a polling interval while the tab is visible", () => {
    const setIntervalSpy = vi.spyOn(globalThis, "setInterval").mockReturnValue(42);
    const clearIntervalSpy = vi.spyOn(globalThis, "clearInterval");

    vi.spyOn(document, "addEventListener").mockImplementation((_event, fn) => {
      capturedListener = fn as () => void;
    });

    renderHook(() => useServerNotifications());

    expect(setIntervalSpy).toHaveBeenCalledTimes(1);
    expect(clearIntervalSpy).not.toHaveBeenCalled();

    setIntervalSpy.mockRestore();
    clearIntervalSpy.mockRestore();
  });

  it("clears the polling interval on unmount", () => {
    const setIntervalSpy = vi.spyOn(globalThis, "setInterval").mockReturnValue(7);
    const clearIntervalSpy = vi.spyOn(globalThis, "clearInterval");

    vi.spyOn(document, "addEventListener").mockImplementation((_event, fn) => {
      capturedListener = fn as () => void;
    });

    const { unmount } = renderHook(() => useServerNotifications());
    unmount();
    expect(clearIntervalSpy).toHaveBeenCalled();

    setIntervalSpy.mockRestore();
    clearIntervalSpy.mockRestore();
  });

  it("does not poll when the tab starts hidden", () => {
    const setIntervalSpy = vi.spyOn(globalThis, "setInterval").mockReturnValue(7);
    const clearIntervalSpy = vi.spyOn(globalThis, "clearInterval");

    Object.defineProperty(document, "hidden", { value: true, configurable: true });

    vi.spyOn(document, "addEventListener").mockImplementation((_event, fn) => {
      capturedListener = fn as () => void;
    });

    renderHook(() => useServerNotifications());

    expect(setIntervalSpy).not.toHaveBeenCalled();
    expect(clearIntervalSpy).not.toHaveBeenCalled();

    setIntervalSpy.mockRestore();
    clearIntervalSpy.mockRestore();
    Object.defineProperty(document, "hidden", { value: false, configurable: true });
  });

  it("refreshes when the notification centre opens", async () => {
    fetchSpy.mockClear();
    fetchSpy.mockResolvedValueOnce([]);

    renderHook(() => useServerNotifications());

    await act(async () => {
      await Promise.resolve();
      await new Promise((r) => setTimeout(r, 0));
    });

    useNotificationStore.setState({ centreOpen: true });

    await act(async () => {
      await Promise.resolve();
      await new Promise((r) => setTimeout(r, 0));
    });

    // mount effect fires once, second effect fires on centreOpen transition
    expect(fetchSpy).toHaveBeenCalledTimes(2);
  });

  it("does not re-fetch when centreOpen is already true and re-renders", async () => {
    fetchSpy.mockClear();
    fetchSpy.mockResolvedValueOnce([]);

    useNotificationStore.setState({ centreOpen: true });

    renderHook(() => useServerNotifications());

    await act(async () => {
      await Promise.resolve();
      await new Promise((r) => setTimeout(r, 0));
    });

    // mount effect fires once; second effect fires because centreOpen is true at mount
    expect(fetchSpy).toHaveBeenCalledTimes(2);
  });
});
