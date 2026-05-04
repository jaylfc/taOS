import { describe, expect, it, beforeEach, vi } from "vitest";
import { renderHook, act } from "@testing-library/react";
import { useBrowserKeyboardShortcuts } from "./keyboard";
import { useBrowserStore } from "@/stores/browser-store";

const TEST_WINDOW_ID = "win-test";

beforeEach(() => {
  useBrowserStore.setState({ windows: {} });
  useBrowserStore.getState().createWindow(TEST_WINDOW_ID, "personal");
});

function fireKey(key: string, modifierKey: "metaKey" | "ctrlKey" = "ctrlKey") {
  const event = new KeyboardEvent("keydown", { key, [modifierKey]: true } as any);
  window.dispatchEvent(event);
}

describe("useBrowserKeyboardShortcuts", () => {
  it("Cmd/Ctrl+T calls addTab", () => {
    const addSpy = vi.spyOn(useBrowserStore.getState(), "addTab");
    renderHook(() =>
      useBrowserKeyboardShortcuts({
        windowId: TEST_WINDOW_ID,
        hasFocus: true,
        onOpenFind: () => {},
      }),
    );
    act(() => fireKey("t"));
    expect(addSpy).toHaveBeenCalledWith(TEST_WINDOW_ID);
  });

  it("Cmd/Ctrl+W calls closeTab on the active tab", () => {
    const closeSpy = vi.spyOn(useBrowserStore.getState(), "closeTab");
    renderHook(() =>
      useBrowserKeyboardShortcuts({
        windowId: TEST_WINDOW_ID,
        hasFocus: true,
        onOpenFind: () => {},
      }),
    );
    act(() => fireKey("w"));
    expect(closeSpy).toHaveBeenCalled();
  });

  it("Cmd/Ctrl+F calls onOpenFind", () => {
    const onOpenFind = vi.fn();
    renderHook(() =>
      useBrowserKeyboardShortcuts({
        windowId: TEST_WINDOW_ID,
        hasFocus: true,
        onOpenFind,
      }),
    );
    act(() => fireKey("f"));
    expect(onOpenFind).toHaveBeenCalled();
  });

  it("Cmd/Ctrl++ increases zoom", () => {
    const setZoomSpy = vi.spyOn(useBrowserStore.getState(), "setTabZoom");
    renderHook(() =>
      useBrowserKeyboardShortcuts({
        windowId: TEST_WINDOW_ID,
        hasFocus: true,
        onOpenFind: () => {},
      }),
    );
    act(() => fireKey("="));
    expect(setZoomSpy).toHaveBeenCalled();
    const callArgs = setZoomSpy.mock.calls[0];
    expect(callArgs[2]).toBeGreaterThan(1.0);
  });

  it("Cmd/Ctrl+- decreases zoom", () => {
    const setZoomSpy = vi.spyOn(useBrowserStore.getState(), "setTabZoom");
    renderHook(() =>
      useBrowserKeyboardShortcuts({
        windowId: TEST_WINDOW_ID,
        hasFocus: true,
        onOpenFind: () => {},
      }),
    );
    act(() => fireKey("-"));
    const callArgs = setZoomSpy.mock.calls[0];
    expect(callArgs[2]).toBeLessThan(1.0);
  });

  it("Cmd/Ctrl+0 resets zoom to 1.0", () => {
    const setZoomSpy = vi.spyOn(useBrowserStore.getState(), "setTabZoom");
    renderHook(() =>
      useBrowserKeyboardShortcuts({
        windowId: TEST_WINDOW_ID,
        hasFocus: true,
        onOpenFind: () => {},
      }),
    );
    act(() => fireKey("0"));
    expect(setZoomSpy).toHaveBeenCalledWith(
      TEST_WINDOW_ID,
      expect.any(String),
      1.0,
    );
  });

  it("does NOT fire shortcuts when hasFocus is false", () => {
    const addSpy = vi.spyOn(useBrowserStore.getState(), "addTab");
    renderHook(() =>
      useBrowserKeyboardShortcuts({
        windowId: TEST_WINDOW_ID,
        hasFocus: false,
        onOpenFind: () => {},
      }),
    );
    act(() => fireKey("t"));
    expect(addSpy).not.toHaveBeenCalled();
  });

  it("does NOT fire shortcuts without modifier key", () => {
    const addSpy = vi.spyOn(useBrowserStore.getState(), "addTab");
    renderHook(() =>
      useBrowserKeyboardShortcuts({
        windowId: TEST_WINDOW_ID,
        hasFocus: true,
        onOpenFind: () => {},
      }),
    );
    const event = new KeyboardEvent("keydown", { key: "t" });
    act(() => window.dispatchEvent(event));
    expect(addSpy).not.toHaveBeenCalled();
  });
});
