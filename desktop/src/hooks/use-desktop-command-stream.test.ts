import { describe, it, expect, beforeEach, afterEach, vi } from "vitest";
import { renderHook } from "@testing-library/react";
import { useDesktopCommandStream } from "./use-desktop-command-stream";

// Minimal EventSource stub: capture the instance so a test can push messages.
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

describe("useDesktopCommandStream", () => {
  beforeEach(() => {
    FakeEventSource.last = null;
    vi.stubGlobal("EventSource", FakeEventSource as unknown as typeof EventSource);
  });
  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("subscribes to the desktop command stream", () => {
    renderHook(() => useDesktopCommandStream());
    expect(FakeEventSource.last?.url).toBe("/api/desktop/stream");
  });

  it("re-dispatches open-app commands as a taos:open-app event", () => {
    renderHook(() => useDesktopCommandStream());
    const seen: unknown[] = [];
    const handler = (e: Event) => seen.push((e as CustomEvent).detail);
    window.addEventListener("taos:open-app", handler);
    FakeEventSource.last!.push(JSON.stringify({ kind: "open-app", payload: { app: "projects" } }));
    window.removeEventListener("taos:open-app", handler);
    expect(seen).toEqual([{ app: "projects" }]);
  });

  it("re-dispatches window commands as a taos:window event", () => {
    renderHook(() => useDesktopCommandStream());
    const seen: unknown[] = [];
    const handler = (e: Event) => seen.push((e as CustomEvent).detail);
    window.addEventListener("taos:window", handler);
    FakeEventSource.last!.push(JSON.stringify({ kind: "window", payload: { action: "arrange", preset: "tile-2" } }));
    window.removeEventListener("taos:window", handler);
    expect(seen).toEqual([{ action: "arrange", preset: "tile-2" }]);
  });

  it("reports the desktop layout for a layout command", async () => {
    const layout = { screen: { width: 1920, height: 1080, ratio: 1.778 }, windows: [] };
    const getLayout = vi.fn(() => layout);
    (window as unknown as { taosDesktop?: unknown }).taosDesktop = { getLayout, run: vi.fn() };
    const fetchMock = vi.fn(() => Promise.resolve({ ok: true } as Response));
    vi.stubGlobal("fetch", fetchMock);
    renderHook(() => useDesktopCommandStream());
    FakeEventSource.last!.push(JSON.stringify({ kind: "layout", payload: { request_id: "req-1" } }));
    await vi.waitFor(() => expect(fetchMock).toHaveBeenCalled());
    expect(getLayout).toHaveBeenCalled();
    const [url, init] = fetchMock.mock.calls[0]!;
    expect(url).toBe("/api/desktop/layout-result");
    expect(JSON.parse((init as RequestInit).body as string)).toEqual({
      request_id: "req-1",
      layout,
    });
    delete (window as unknown as { taosDesktop?: unknown }).taosDesktop;
  });

  it("ignores malformed payloads without throwing", () => {
    renderHook(() => useDesktopCommandStream());
    expect(() => FakeEventSource.last!.push("not json")).not.toThrow();
  });

  it("closes the stream on unmount", () => {
    const { unmount } = renderHook(() => useDesktopCommandStream());
    const es = FakeEventSource.last!;
    unmount();
    expect(es.closed).toBe(true);
  });
});
