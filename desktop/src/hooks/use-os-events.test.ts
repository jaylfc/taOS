import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { renderHook, act } from "@testing-library/react";
import { useOsEvents, OsEvent } from "./use-os-events";

// ---------------------------------------------------------------------------
// Mock EventSource -- must use a regular function (not arrow) so `new` works
// ---------------------------------------------------------------------------

type MessageListener = (e: MessageEvent) => void;

interface MockEventSource {
  url: string;
  onopen: (() => void) | null;
  onmessage: MessageListener | null;
  onerror: ((e: Event) => void) | null;
  close: ReturnType<typeof vi.fn>;
  readyState: number;
  _fire: (data: unknown) => void;
  _fireError: () => void;
}

let lastEs: MockEventSource | null = null;

// eslint-disable-next-line @typescript-eslint/no-explicit-any
const MockEventSourceCtor = vi.fn().mockImplementation(function (this: any, url: string) {
  this.url = url;
  this.onopen = null;
  this.onmessage = null;
  this.onerror = null;
  this.close = vi.fn();
  this.readyState = 0; // CONNECTING
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  this._fire = (data: unknown) => {
    (this as MockEventSource).onmessage?.({ data: JSON.stringify(data) } as MessageEvent);
  };
  this._fireError = () => {
    (this as MockEventSource).onerror?.(new Event("error"));
  };
  lastEs = this as MockEventSource;
});

// The real EventSource carries these as static members, and the hook compares
// readyState against them. Without them both sides of `es.readyState ===
// EventSource.CLOSED` are undefined, so every disconnect assertion below
// passes no matter what the hook does.
Object.assign(MockEventSourceCtor, { CONNECTING: 0, OPEN: 1, CLOSED: 2 });

beforeEach(() => {
  vi.stubGlobal("EventSource", MockEventSourceCtor);
  MockEventSourceCtor.mockClear();
  lastEs = null;
});

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("useOsEvents", () => {
  it("opens an EventSource to /api/os/events with kinds on mount", () => {
    renderHook(() => useOsEvents(["projects.task.changed"], () => {}));
    expect(MockEventSourceCtor).toHaveBeenCalledWith(
      "/api/os/events?kinds=projects.task.changed",
    );
  });

  it("opens an EventSource without kinds when kinds is empty", () => {
    renderHook(() => useOsEvents([], () => {}));
    expect(MockEventSourceCtor).toHaveBeenCalledWith("/api/os/events");
  });

  it("calls onEvent for matching kind", () => {
    const onEvent = vi.fn();
    renderHook(() => useOsEvents(["projects.task.changed"], onEvent));

    act(() => {
      lastEs?._fire({
        kind: "projects.task.changed",
        id: "evt-1",
        ts: 1234567890.0,
      });
    });

    expect(onEvent).toHaveBeenCalledWith({
      kind: "projects.task.changed",
      id: "evt-1",
      ts: 1234567890.0,
    });
  });

  it("ignores non-matching kind", () => {
    const onEvent = vi.fn();
    renderHook(() => useOsEvents(["projects.task.changed"], onEvent));

    act(() => {
      lastEs?._fire({
        kind: "agents.status.changed",
        id: "evt-2",
        ts: 1234567890.0,
      });
    });

    expect(onEvent).not.toHaveBeenCalled();
  });

  it("sets connected=true and stale=false on open", async () => {
    const { result } = renderHook(() =>
      useOsEvents(["projects.task.changed"], () => {}),
    );

    act(() => {
      lastEs?.onopen?.();
    });

    expect(result.current.connected).toBe(true);
    expect(result.current.stale).toBe(false);
  });

  it("sets connected=false and stale=true on hard disconnect", async () => {
    vi.useFakeTimers();
    const { result } = renderHook(() =>
      useOsEvents(["projects.task.changed"], () => {}),
    );

    act(() => {
      lastEs?.onopen?.();
    });
    expect(result.current.connected).toBe(true);
    expect(result.current.stale).toBe(false);

    act(() => {
      if (lastEs) {
        lastEs.readyState = EventSource.CLOSED;
        lastEs._fireError();
      }
    });

    expect(result.current.connected).toBe(false);
    expect(result.current.stale).toBe(true);

    vi.useRealTimers();
  });

  it("closes the EventSource on unmount", () => {
    const { unmount } = renderHook(() =>
      useOsEvents(["projects.task.changed"], () => {}),
    );
    unmount();
    expect(lastEs?.close).toHaveBeenCalled();
  });

  it("deduplicates events by id", () => {
    const onEvent = vi.fn();
    renderHook(() => useOsEvents(["projects.task.changed"], onEvent));

    act(() => {
      lastEs?._fire({
        kind: "projects.task.changed",
        id: "evt-dup",
        ts: 1234567890.0,
      });
      lastEs?._fire({
        kind: "projects.task.changed",
        id: "evt-dup",
        ts: 1234567890.0,
      });
    });

    expect(onEvent).toHaveBeenCalledTimes(1);
  });

  it("reconnects after drop and receives new events", async () => {
    vi.useFakeTimers();
    const onEvent = vi.fn();
    const { result } = renderHook(() =>
      useOsEvents(["projects.task.changed"], onEvent),
    );

    act(() => {
      lastEs?.onopen?.();
    });
    expect(result.current.connected).toBe(true);
    expect(result.current.stale).toBe(false);

    act(() => {
      if (lastEs) {
        lastEs.readyState = EventSource.CLOSED;
        lastEs._fireError();
      }
    });
    expect(result.current.connected).toBe(false);
    expect(result.current.stale).toBe(true);

    await act(async () => {
      await vi.advanceTimersByTimeAsync(5000);
    });

    expect(MockEventSourceCtor).toHaveBeenCalledTimes(2);
    expect(lastEs).not.toBeNull();
    act(() => {
      lastEs?.onopen?.();
    });
    expect(result.current.connected).toBe(true);
    expect(result.current.stale).toBe(false);

    act(() => {
      lastEs?._fire({
        kind: "projects.task.changed",
        id: "evt-reconnect",
        ts: 1234567890.0,
      });
    });
    expect(onEvent).toHaveBeenCalledWith({
      kind: "projects.task.changed",
      id: "evt-reconnect",
      ts: 1234567890.0,
    });

    vi.useRealTimers();
  });

  it("ignores events with no kind", () => {
    const onEvent = vi.fn();
    renderHook(() => useOsEvents(["projects.task.changed"], onEvent));

    act(() => {
      lastEs?._fire({
        id: "evt-nokind",
        ts: 1234567890.0,
      });
    });

    expect(onEvent).not.toHaveBeenCalled();
  });

  it("reopens the stream with the new kinds when kinds changes", () => {
    const { rerender } = renderHook(
      ({ kinds }: { kinds: string[] }) => useOsEvents(kinds, () => {}),
      { initialProps: { kinds: ["projects.task.changed"] } },
    );
    expect(MockEventSourceCtor).toHaveBeenLastCalledWith(
      "/api/os/events?kinds=projects.task.changed",
    );
    const first = lastEs;

    rerender({ kinds: ["projects.task.changed", "notifications.new"] });

    expect(first?.close).toHaveBeenCalled();
    expect(MockEventSourceCtor).toHaveBeenLastCalledWith(
      "/api/os/events?kinds=projects.task.changed%2Cnotifications.new",
    );
  });

  it("does not reopen the stream when kinds is a new array with the same contents", () => {
    const { rerender } = renderHook(
      ({ kinds }: { kinds: string[] }) => useOsEvents(kinds, () => {}),
      { initialProps: { kinds: ["projects.task.changed"] } },
    );
    expect(MockEventSourceCtor).toHaveBeenCalledTimes(1);

    rerender({ kinds: ["projects.task.changed"] });

    expect(MockEventSourceCtor).toHaveBeenCalledTimes(1);
  });

  it("reports disconnected while the stream is only CONNECTING", () => {
    const { result } = renderHook(() =>
      useOsEvents(["projects.task.changed"], () => {}),
    );

    act(() => {
      lastEs?.onopen?.();
    });
    expect(result.current.connected).toBe(true);

    act(() => {
      if (lastEs) {
        lastEs.readyState = EventSource.CONNECTING;
        lastEs._fireError();
      }
    });

    expect(result.current.connected).toBe(false);
    expect(result.current.stale).toBe(true);
  });
  it("delivers events.lagged even when it is not in the caller's kinds", () => {
    const onEvent = vi.fn();
    renderHook(() => useOsEvents(["projects.task.changed"], onEvent));

    act(() => {
      lastEs?._fire({ kind: "events.lagged", id: null, ts: 1, dropped: 7 });
    });

    expect(onEvent).toHaveBeenCalledWith(
      expect.objectContaining({ kind: "events.lagged", dropped: 7 }),
    );
  });

  it("does not dedupe successive events.lagged frames (their id is null)", () => {
    const onEvent = vi.fn();
    renderHook(() => useOsEvents([], onEvent));

    act(() => {
      lastEs?._fire({ kind: "events.lagged", id: null, ts: 1, dropped: 2 });
      lastEs?._fire({ kind: "events.lagged", id: null, ts: 2, dropped: 5 });
    });

    expect(onEvent).toHaveBeenCalledTimes(2);
  });
});
