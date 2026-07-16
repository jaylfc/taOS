import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { act, renderHook } from "@testing-library/react";
import { useHtmlImage } from "./useHtmlImage";

/** A controllable stand-in for the browser's HTMLImageElement so tests can
 * drive the load/error lifecycle deterministically. */
class FakeImage {
  static last: FakeImage | undefined;
  crossOrigin = "";
  onload: (() => void) | null = null;
  onerror: (() => void) | null = null;
  private _src = "";
  constructor() {
    FakeImage.last = this;
  }
  set src(v: string) {
    this._src = v;
  }
  get src() {
    return this._src;
  }
}

describe("useHtmlImage", () => {
  beforeEach(() => {
    FakeImage.last = undefined;
    vi.stubGlobal("Image", FakeImage as unknown as typeof Image);
  });
  afterEach(() => {
    vi.unstubAllGlobals();
    vi.restoreAllMocks();
  });

  it("returns no image and no failure for an undefined src", () => {
    const { result } = renderHook(() => useHtmlImage(undefined));
    expect(result.current.image).toBeUndefined();
    expect(result.current.failed).toBe(false);
    expect(FakeImage.last).toBeUndefined();
  });

  it("exposes the loaded image once onload fires", () => {
    const { result } = renderHook(() => useHtmlImage("data:image/png;base64,AAAA"));
    expect(result.current.image).toBeUndefined();
    act(() => FakeImage.last!.onload!());
    expect(result.current.image).toBe(FakeImage.last);
    expect(result.current.failed).toBe(false);
  });

  it("sets failed and keeps no image when onerror fires", () => {
    const warn = vi.spyOn(console, "warn").mockImplementation(() => {});
    const { result } = renderHook(() => useHtmlImage("data:broken"));
    act(() => FakeImage.last!.onerror!());
    expect(result.current.failed).toBe(true);
    expect(result.current.image).toBeUndefined();
    expect(warn).toHaveBeenCalled();
  });

  it("requests images anonymously (crossOrigin) so exported canvases stay untainted", () => {
    renderHook(() => useHtmlImage("data:x"));
    expect(FakeImage.last!.crossOrigin).toBe("anonymous");
    expect(FakeImage.last!.src).toBe("data:x");
  });

  it("ignores a late onload after the src was cleared (unmount cancellation)", () => {
    const { result, rerender } = renderHook(({ src }) => useHtmlImage(src), {
      initialProps: { src: "data:first" as string | undefined },
    });
    const first = FakeImage.last!;
    // Switch to no src: the effect cleanup marks the first load cancelled.
    rerender({ src: undefined });
    act(() => first.onload!());
    expect(result.current.image).toBeUndefined();
  });
});
