import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { renderHook, act } from "@testing-library/react";
import { useDesktopCommandStream } from "../use-desktop-command-stream";

function makeEventSourceMock() {
  const instances: EventSourceLike[] = [];
  let capturedOnError: ((ev: Event) => void) | undefined;
  let capturedOnMessage: ((ev: MessageEvent) => void) | undefined;

  const mock = vi.fn(function (this: unknown, _url: string) {
    const es: EventSourceLike = {
      readyState: EventSource.OPEN,
      CONNECTING: EventSource.CONNECTING,
      OPEN: EventSource.OPEN,
      CLOSED: EventSource.CLOSED,
      close: vi.fn(),
      set onerror(handler: (this: EventSource, ev: Event) => void) {
        capturedOnError = handler as unknown as (ev: Event) => void;
      },
      get onerror() {
        return undefined;
      },
      set onmessage(handler: (this: EventSource, ev: MessageEvent) => void) {
        capturedOnMessage = handler as unknown as (ev: MessageEvent) => void;
      },
      get onmessage() {
        return undefined;
      },
      set onopen(_handler: (this: EventSource, ev: Event) => void) {},
      get onopen() {
        return undefined;
      },
    };
    instances.push(es);
    return es;
  });

  return { mock, instances, get capturedOnError() { return capturedOnError; }, get capturedOnMessage() { return capturedOnMessage; } };
}

type EventSourceLike = {
  readyState: number;
  CONNECTING: number;
  OPEN: number;
  CLOSED: number;
  close: ReturnType<typeof vi.fn>;
};

describe("useDesktopCommandStream (RED-first)", () => {
  let eventSourceMock: ReturnType<typeof vi.fn>;
  let instances: EventSourceLike[];
  let setup: ReturnType<typeof makeEventSourceMock>;

  beforeEach(() => {
    vi.useFakeTimers();
    setup = makeEventSourceMock();
    eventSourceMock = setup.mock;
    instances = setup.instances;
    vi.stubGlobal("EventSource", eventSourceMock);
  });

  afterEach(() => {
    vi.useRealTimers();
    vi.unstubAllGlobals();
  });

  it("reconnects after the server closes with a 401", () => {
    renderHook(() => useDesktopCommandStream());

    expect(eventSourceMock).toHaveBeenCalledTimes(1);

    act(() => {
      if (setup.capturedOnError) {
        instances[0].readyState = EventSource.CLOSED;
        setup.capturedOnError(new Event("error"));
      }
    });

    act(() => {
      vi.advanceTimersByTime(5000);
    });

    expect(eventSourceMock).toHaveBeenCalledTimes(2);
  });

  it("reconnects after a controller restart", () => {
    const commands: string[] = [];
    const dispatchSpy = vi.fn((event: Event) => {
      if (event instanceof CustomEvent && event.type === "taos:open-app") {
        commands.push((event as CustomEvent).detail?.app as string ?? "unknown");
      }
    });
    Object.defineProperty(window, "dispatchEvent", {
      configurable: true,
      writable: true,
      value: dispatchSpy,
    });

    renderHook(() => useDesktopCommandStream());

    act(() => {
      if (setup.capturedOnMessage) setup.capturedOnMessage(new MessageEvent("message", {
        data: JSON.stringify({ kind: "open-app", payload: { app: "first" } }),
      }));
    });
    expect(commands).toContain("first");
    expect(eventSourceMock).toHaveBeenCalledTimes(1);

    act(() => {
      if (setup.capturedOnError) {
        instances[0].readyState = EventSource.CLOSED;
        setup.capturedOnError(new Event("error"));
      }
    });

    act(() => {
      vi.advanceTimersByTime(5000);
    });

    expect(eventSourceMock).toHaveBeenCalledTimes(2);

    act(() => {
      if (setup.capturedOnMessage) setup.capturedOnMessage(new MessageEvent("message", {
        data: JSON.stringify({ kind: "open-app", payload: { app: "second" } }),
      }));
    });
    expect(commands).toContain("second");
  });

  it("backoff is shared, not duplicated per hook", async () => {
    const fs = await import("fs");
    const path = await import("path");

    const baseDir = path.resolve(import.meta.dirname, "..");

    const desktopStreamSrc = fs.readFileSync(
      path.join(baseDir, "use-desktop-command-stream.ts"),
      "utf-8",
    );
    const eventStreamSrc = fs.readFileSync(
      path.join(baseDir, "use-event-stream.ts"),
      "utf-8",
    );
    const osEventsSrc = fs.readFileSync(
      path.join(baseDir, "use-os-events.ts"),
      "utf-8",
    );

    const localDefs = [
      ...desktopStreamSrc.match(/const\s+RECONNECT_DELAY_MS\s*=/g) || [],
      ...eventStreamSrc.match(/const\s+RECONNECT_DELAY_MS\s*=/g) || [],
      ...osEventsSrc.match(/const\s+RECONNECT_DELAY_MS\s*=/g) || [],
    ];

    expect(localDefs.length).toBe(0);
  });
});
