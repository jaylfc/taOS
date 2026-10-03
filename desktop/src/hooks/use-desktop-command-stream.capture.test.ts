import { describe, it, expect, vi } from "vitest";
import { renderHook } from "@testing-library/react";
import { useDesktopCommandStream } from "./use-desktop-command-stream";

class FakeEventSource {
  static last: FakeEventSource | null = null;
  url: string;
  onmessage: ((e: { data: string }) => void) | null = null;
  onerror: (() => void) | null = null;
  closed = false;
  constructor(url: string) {
    this.url = url;
    FakeEventSource.last = this;
  }
  push(data: string) {
    this.onmessage?.({ data });
  }
  close() {
    this.closed = true;
  }
}

const domToPngMock = vi.fn();

vi.mock("modern-screenshot", () => ({
  domToPng: (...args: unknown[]) => domToPngMock(...args),
}));

const hasScreenCaptureMock = vi.fn();
const grabScreenFrameMock = vi.fn();

vi.mock("@/lib/screen-capture", () => ({
  hasScreenCapture: () => hasScreenCaptureMock(),
  grabScreenFrame: (...args: unknown[]) => grabScreenFrameMock(...args),
}));

describe("captureAndReport", () => {
  it("POSTs a timeout error when domToPng hangs", async () => {
    vi.useFakeTimers();

    const fetchMock = vi.fn(() => Promise.resolve(new Response()));
    vi.stubGlobal("fetch", fetchMock);
    vi.stubGlobal("EventSource", FakeEventSource as unknown as typeof EventSource);

    domToPngMock.mockImplementation(() => new Promise(() => {}));
    hasScreenCaptureMock.mockReturnValue(false);
    grabScreenFrameMock.mockReturnValue(null);

    renderHook(() => useDesktopCommandStream());

    FakeEventSource.last!.push(
      JSON.stringify({ kind: "screenshot", payload: { request_id: "req-1" } }),
    );

    await vi.advanceTimersByTimeAsync(16000);

    expect(fetchMock).toHaveBeenCalledWith(
      "/api/desktop/screenshot-result",
      expect.objectContaining({
        method: "POST",
        body: JSON.stringify({ request_id: "req-1", error: "capture timed out" }),
      }),
    );

    vi.useRealTimers();
    vi.unstubAllGlobals();
  });

  it("answers a second screenshot with in-progress error during a running capture", async () => {
    vi.useFakeTimers();

    const fetchMock = vi.fn(() => Promise.resolve(new Response()));
    vi.stubGlobal("fetch", fetchMock);
    vi.stubGlobal("EventSource", FakeEventSource as unknown as typeof EventSource);

    domToPngMock.mockImplementation(() => new Promise(() => {}));
    hasScreenCaptureMock.mockReturnValue(false);
    grabScreenFrameMock.mockReturnValue(null);

    renderHook(() => useDesktopCommandStream());

    FakeEventSource.last!.push(
      JSON.stringify({ kind: "screenshot", payload: { request_id: "req-1" } }),
    );
    FakeEventSource.last!.push(
      JSON.stringify({ kind: "screenshot", payload: { request_id: "req-2" } }),
    );

    await vi.advanceTimersByTimeAsync(16000);

    expect(fetchMock).toHaveBeenCalledWith(
      "/api/desktop/screenshot-result",
      expect.objectContaining({
        method: "POST",
        body: JSON.stringify({ request_id: "req-2", error: "capture already in progress" }),
      }),
    );

    vi.useRealTimers();
    vi.unstubAllGlobals();
  });

  it("answers a layout request even while a capture is stuck", async () => {
    vi.useFakeTimers();

    const fetchMock = vi.fn(() => Promise.resolve(new Response()));
    vi.stubGlobal("fetch", fetchMock);
    vi.stubGlobal("EventSource", FakeEventSource as unknown as typeof EventSource);

    domToPngMock.mockImplementation(() => new Promise(() => {}));
    hasScreenCaptureMock.mockReturnValue(false);
    grabScreenFrameMock.mockReturnValue(null);
    (window as unknown as { taosDesktop?: unknown }).taosDesktop = {
      getLayout: () => ({ screen: { width: 1920, height: 1080 }, windows: [] }),
    };

    renderHook(() => useDesktopCommandStream());

    FakeEventSource.last!.push(
      JSON.stringify({ kind: "screenshot", payload: { request_id: "req-1" } }),
    );
    FakeEventSource.last!.push(
      JSON.stringify({ kind: "layout", payload: { request_id: "req-layout" } }),
    );

    await vi.advanceTimersByTimeAsync(16000);

    expect(fetchMock).toHaveBeenCalledTimes(2);
    expect(fetchMock).toHaveBeenCalledWith(
      "/api/desktop/screenshot-result",
      expect.objectContaining({
        method: "POST",
        body: JSON.stringify({ request_id: "req-1", error: "capture timed out" }),
      }),
    );
    expect(fetchMock).toHaveBeenCalledWith(
      "/api/desktop/layout-result",
      expect.objectContaining({
        method: "POST",
        body: JSON.stringify({
          request_id: "req-layout",
          layout: { screen: { width: 1920, height: 1080 }, windows: [] },
        }),
      }),
    );

    delete (window as unknown as { taosDesktop?: unknown }).taosDesktop;
    vi.useRealTimers();
    vi.unstubAllGlobals();
  });
});
