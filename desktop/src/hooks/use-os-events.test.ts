import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { renderHook, act, render } from "@testing-library/react";
import { createElement, Fragment, type ReactNode } from "react";
import {
  useOsEvents,
  OsEvent,
  resetOsEventsState,
} from "./use-os-events";

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

const MockEventSourceCtor = vi.fn().mockImplementation(function (this: any, url: string) {
  this.url = url;
  this.onopen = null;
  this.onmessage = null;
  this.onerror = null;
  this.close = vi.fn();
  this.readyState = 0;
  this._fire = (data: unknown) => {
    (this as MockEventSource).onmessage?.({ data: JSON.stringify(data) } as MessageEvent);
  };
  this._fireError = () => {
    (this as MockEventSource).onerror?.(new Event("error"));
  };
  lastEs = this as MockEventSource;
});

Object.assign(MockEventSourceCtor, { CONNECTING: 0, OPEN: 1, CLOSED: 2 });

beforeEach(() => {
  resetOsEventsState();
  vi.stubGlobal("EventSource", MockEventSourceCtor);
  MockEventSourceCtor.mockClear();
  lastEs = null;
});

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("useOsEvents", () => {
  it("opens an EventSource to /api/os/events on mount", () => {
    renderHook(() => useOsEvents(["projects.task.changed"], () => {}));
    expect(MockEventSourceCtor).toHaveBeenCalledWith("/api/os/events");
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

  it("does not reopen the stream when kinds changes", () => {
    const { rerender } = renderHook(
      ({ kinds }: { kinds: string[] }) => useOsEvents(kinds, () => {}),
      { initialProps: { kinds: ["projects.task.changed"] } },
    );
    expect(MockEventSourceCtor).toHaveBeenCalledTimes(1);
    expect(MockEventSourceCtor).toHaveBeenCalledWith("/api/os/events");

    rerender({ kinds: ["projects.task.changed", "notifications.new"] });

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

  it("two hook instances share one EventSource", () => {
    const onEvent1 = vi.fn();
    const onEvent2 = vi.fn();

    renderHook(() => useOsEvents(["projects.task.changed"], onEvent1));
    renderHook(() => useOsEvents(["agents.status.changed"], onEvent2));

    expect(MockEventSourceCtor).toHaveBeenCalledTimes(1);
  });

  it("filters events client-side per subscriber", () => {
    const onEvent1 = vi.fn();
    const onEvent2 = vi.fn();

    renderHook(() => useOsEvents(["projects.task.changed"], onEvent1));
    renderHook(() => useOsEvents(["agents.status.changed"], onEvent2));

    act(() => {
      lastEs?._fire({
        kind: "projects.task.changed",
        id: "evt-1",
        ts: 1234567890.0,
      });
    });

    expect(onEvent1).toHaveBeenCalledTimes(1);
    expect(onEvent2).not.toHaveBeenCalled();
  });

  it("keeps the connection open while at least one instance remains", () => {
    const { unmount: unmount1 } = renderHook(() =>
      useOsEvents(["projects.task.changed"], () => {}),
    );
    renderHook(() =>
      useOsEvents(["agents.status.changed"], () => {}),
    );

    expect(MockEventSourceCtor).toHaveBeenCalledTimes(1);

    unmount1();
    expect(lastEs?.close).not.toHaveBeenCalled();
  });

  it("closes the EventSource when the last instance unmounts", () => {
    const { unmount: unmount1 } = renderHook(() =>
      useOsEvents(["projects.task.changed"], () => {}),
    );
    const { unmount: unmount2 } = renderHook(() =>
      useOsEvents(["agents.status.changed"], () => {}),
    );

    expect(MockEventSourceCtor).toHaveBeenCalledTimes(1);

    unmount2();
    expect(lastEs?.close).not.toHaveBeenCalled();

    unmount1();
    expect(lastEs?.close).toHaveBeenCalled();
  });

  it("deduplicates events across subscribers", () => {
    const onEvent1 = vi.fn();
    const onEvent2 = vi.fn();

    renderHook(() => useOsEvents(["projects.task.changed"], onEvent1));
    renderHook(() => useOsEvents(["projects.task.changed"], onEvent2));

    act(() => {
      lastEs?._fire({
        kind: "projects.task.changed",
        id: "evt-shared",
        ts: 1234567890.0,
      });
    });

    expect(onEvent1).toHaveBeenCalledTimes(1);
    expect(onEvent2).toHaveBeenCalledTimes(1);

    act(() => {
      lastEs?._fire({
        kind: "projects.task.changed",
        id: "evt-shared",
        ts: 1234567890.0,
      });
    });

    expect(onEvent1).toHaveBeenCalledTimes(1);
    expect(onEvent2).toHaveBeenCalledTimes(1);
  });

  it("keeps the shared stream when one subscriber unmounts in the same commit as another widens its kinds", () => {
    function Widening({ kinds }: { kinds: string[] }) {
      useOsEvents(kinds, () => {});
      return null;
    }

    function Leaving() {
      useOsEvents(["agents.status.changed"], () => {});
      return null;
    }

    function Slot({ children }: { children: ReactNode }) {
      return createElement(Fragment, null, children);
    }

    // React runs EVERY effect cleanup in a commit before any setup, and the
    // order it walks them in is its own business. Nesting `Leaving` one level
    // down puts its teardown after `Widening`'s, so for a moment the map holds
    // neither subscriber even though a subscriber is very much still mounted.
    // Deciding "close the stream" from the unmounting component's own mounted
    // flag mistakes that moment for "nobody is listening any more".
    const harness = (kinds: string[], showLeaving: boolean) =>
      createElement(
        Fragment,
        null,
        createElement(Widening, { kinds, key: "widening" }),
        createElement(Slot, {
          key: "slot",
          children: showLeaving ? createElement(Leaving) : null,
        }),
      );

    const { rerender } = render(harness(["projects.task.changed"], true));
    expect(MockEventSourceCtor).toHaveBeenCalledTimes(1);
    const opened = lastEs;

    rerender(harness(["projects.task.changed", "notifications.new"], false));

    expect(opened?.close).not.toHaveBeenCalled();
    expect(MockEventSourceCtor).toHaveBeenCalledTimes(1);
  });

  it("does not re-render subscribers for an error that does not change the status", () => {
    let renders = 0;
    renderHook(() => {
      renders += 1;
      return useOsEvents([], () => {});
    });

    act(() => {
      lastEs?.onopen?.();
    });
    const afterOpen = renders;

    act(() => {
      if (lastEs) {
        lastEs.readyState = EventSource.CONNECTING;
        lastEs._fireError();
      }
    });
    // The first error is a real transition (connected -> stale): one re-render.
    expect(renders).toBe(afterOpen + 1);
    const afterFirstError = renders;

    // The browser fires `error` again on every failed retry. Republishing the
    // status nobody's UI can tell apart must not re-render every subscriber.
    for (let i = 0; i < 5; i += 1) {
      act(() => {
        if (lastEs) {
          lastEs.readyState = EventSource.CONNECTING;
          lastEs._fireError();
        }
      });
    }

    expect(renders).toBe(afterFirstError);
  });
});
