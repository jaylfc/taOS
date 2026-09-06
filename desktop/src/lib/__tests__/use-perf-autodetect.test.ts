import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { renderHook } from "@testing-library/react";
import { useThemeStore } from "../../stores/theme-store";
import { usePerfAutoDetect } from "../use-perf-autodetect";

const KEY = "taos-reduce-effects";

let rafQueue: FrameRequestCallback[] = [];
let nowMs = 0;
let origSetter: (on: boolean) => void;

// Drain the queued rAF callbacks, advancing the clock one frame at a time at the
// given frame rate, so the probe sees a deterministic FPS.
function driveFrames(fps: number) {
  const dt = 1000 / fps;
  let guard = 0;
  while (rafQueue.length && guard++ < fps + 10) {
    const cb = rafQueue.shift()!;
    nowMs += dt;
    cb(nowMs);
  }
}

beforeEach(() => {
  rafQueue = [];
  nowMs = 0;
  localStorage.removeItem(KEY);
  origSetter = useThemeStore.getState().setReduceEffects;
  vi.spyOn(performance, "now").mockImplementation(() => nowMs);
  vi.stubGlobal("requestAnimationFrame", (cb: FrameRequestCallback) => {
    rafQueue.push(cb);
    return rafQueue.length;
  });
  vi.stubGlobal("cancelAnimationFrame", () => {});
});

afterEach(() => {
  useThemeStore.setState({ setReduceEffects: origSetter } as never);
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
});

describe("usePerfAutoDetect (#58 first-run GPU probe)", () => {
  it("enables Reduce effects when the measured frame rate is low", () => {
    const spy = vi.fn();
    useThemeStore.setState({ setReduceEffects: spy } as never);
    renderHook(() => usePerfAutoDetect());
    driveFrames(20); // 20 fps < 40 threshold
    expect(spy).toHaveBeenCalledWith(true);
  });

  it("leaves effects on for a capable machine", () => {
    const spy = vi.fn();
    useThemeStore.setState({ setReduceEffects: spy } as never);
    renderHook(() => usePerfAutoDetect());
    driveFrames(60);
    expect(spy).not.toHaveBeenCalled();
  });

  it("never overrides an explicit user choice", () => {
    localStorage.setItem(KEY, "off");
    const spy = vi.fn();
    useThemeStore.setState({ setReduceEffects: spy } as never);
    renderHook(() => usePerfAutoDetect());
    driveFrames(5);
    expect(spy).not.toHaveBeenCalled();
  });

  it("does not trip when the tab is backgrounded (throttled rAF is not a GPU signal)", () => {
    const spy = vi.fn();
    useThemeStore.setState({ setReduceEffects: spy } as never);
    // Tab hidden for the whole probe: rAF throttling would read as low FPS.
    const hiddenSpy = vi.spyOn(document, "hidden", "get").mockReturnValue(true);
    renderHook(() => usePerfAutoDetect());
    driveFrames(5); // looks slow, but it is a throttling artifact
    expect(spy).not.toHaveBeenCalled();
    hiddenSpy.mockRestore();
  });
});
