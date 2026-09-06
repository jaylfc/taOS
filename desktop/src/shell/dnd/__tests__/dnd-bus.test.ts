import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { startDrag, endDrag, getCurrent, subscribe } from "../dnd-bus";

const samplePayload = { kind: "file" as const, path: "/a/b.png", mime_type: "image/png", size: 10, name: "b.png" };

describe("dnd-bus", () => {
  beforeEach(() => {
    endDrag();
    vi.useFakeTimers();
  });
  afterEach(() => {
    vi.useRealTimers();
    endDrag();
  });

  it("startDrag sets current and notifies subscribers", () => {
    const fn = vi.fn();
    const unsub = subscribe(fn);
    startDrag(samplePayload);
    expect(getCurrent()).toEqual(samplePayload);
    expect(fn).toHaveBeenCalled();
    unsub();
  });

  it("endDrag clears current", () => {
    startDrag(samplePayload);
    endDrag();
    expect(getCurrent()).toBeNull();
  });

  it("30s stale timeout auto-clears", () => {
    startDrag(samplePayload);
    expect(getCurrent()).not.toBeNull();
    vi.advanceTimersByTime(30_000);
    expect(getCurrent()).toBeNull();
  });

  it("starting a new drag resets the stale timer", () => {
    startDrag(samplePayload);
    vi.advanceTimersByTime(25_000);
    startDrag({ ...samplePayload, name: "c.png" });
    vi.advanceTimersByTime(15_000);
    expect(getCurrent()).not.toBeNull();
    vi.advanceTimersByTime(20_000);
    expect(getCurrent()).toBeNull();
  });

  it("subscribers receive change events for both start and end", () => {
    const fn = vi.fn();
    subscribe(fn);
    startDrag(samplePayload);
    endDrag();
    expect(fn).toHaveBeenCalledTimes(2);
  });
});
