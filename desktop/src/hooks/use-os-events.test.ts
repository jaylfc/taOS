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
const allEs: MockEventSource[] = [];

// Streams constructed and not yet closed. Widening the server-side filter
// overlaps two of them on purpose, so "one stream" is an invariant about how
// many are OPEN, not about how many were ever constructed.
function openStreams(): MockEventSource[] {
  return allEs.filter((es) => es.close.mock.calls.length === 0);
}

// Let a widened stream finish taking over from the narrow one.
function settleHandoff() {
  act(() => {
    lastEs?.onopen?.();
  });
}

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
  allEs.push(this as MockEventSource);
});

Object.assign(MockEventSourceCtor, { CONNECTING: 0, OPEN: 1, CLOSED: 2 });

beforeEach(() => {
  resetOsEventsState();
  vi.stubGlobal("EventSource", MockEventSourceCtor);
  MockEventSourceCtor.mockClear();
  lastEs = null;
  allEs.length = 0;
});

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("useOsEvents", () => {
  it("opens an EventSource filtered to the kinds someone asked for", () => {
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

  it("closes the EventSource on unmount", async () => {
    const { unmount } = renderHook(() =>
      useOsEvents(["projects.task.changed"], () => {}),
    );
    unmount();
    // The teardown decision is deferred by a microtask so it reads the
    // subscriber map once the commit has settled rather than mid-teardown.
    await act(async () => {});
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

  it("does not reopen when a subscriber widens into kinds already covered", () => {
    // Another subscriber already put `agents.status.changed` in the union, so
    // this widening costs nothing -- which is the whole point of one shared
    // stream carrying the union rather than one stream per caller.
    renderHook(() => useOsEvents(["agents.status.changed"], () => {}));
    const { rerender } = renderHook(
      ({ kinds }: { kinds: string[] }) => useOsEvents(kinds, () => {}),
      { initialProps: { kinds: ["projects.task.changed"] } },
    );
    settleHandoff();
    const opened = MockEventSourceCtor.mock.calls.length;

    rerender({ kinds: ["projects.task.changed", "agents.status.changed"] });

    expect(MockEventSourceCtor).toHaveBeenCalledTimes(opened);
    expect(openStreams()).toHaveLength(1);
  });

  it("never reopens when a subscriber narrows its kinds", () => {
    const { rerender } = renderHook(
      ({ kinds }: { kinds: string[] }) => useOsEvents(kinds, () => {}),
      {
        initialProps: {
          kinds: ["projects.task.changed", "notifications.new"],
        },
      },
    );
    settleHandoff();
    expect(MockEventSourceCtor).toHaveBeenCalledTimes(1);

    // Coverage is monotone. Narrowing it would only buy another reopen the
    // next time a subscriber asks for that kind again.
    rerender({ kinds: ["projects.task.changed"] });

    expect(MockEventSourceCtor).toHaveBeenCalledTimes(1);
    expect(openStreams()).toHaveLength(1);
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
    settleHandoff();

    expect(openStreams()).toHaveLength(1);
    expect(openStreams()[0].url).toBe(
      "/api/os/events?kinds=agents.status.changed,projects.task.changed",
    );
  });

  it("filters events client-side per subscriber", () => {
    const onEvent1 = vi.fn();
    const onEvent2 = vi.fn();

    renderHook(() => useOsEvents(["projects.task.changed"], onEvent1));
    renderHook(() => useOsEvents(["agents.status.changed"], onEvent2));
    settleHandoff();

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

  it("keeps the connection open while at least one instance remains", async () => {
    const { unmount: unmount1 } = renderHook(() =>
      useOsEvents(["projects.task.changed"], () => {}),
    );
    renderHook(() =>
      useOsEvents(["agents.status.changed"], () => {}),
    );
    settleHandoff();
    expect(openStreams()).toHaveLength(1);

    unmount1();
    await act(async () => {});
    expect(lastEs?.close).not.toHaveBeenCalled();
    expect(openStreams()).toHaveLength(1);
  });

  it("closes the EventSource when the last instance unmounts", async () => {
    const { unmount: unmount1 } = renderHook(() =>
      useOsEvents(["projects.task.changed"], () => {}),
    );
    const { unmount: unmount2 } = renderHook(() =>
      useOsEvents(["agents.status.changed"], () => {}),
    );
    settleHandoff();
    expect(openStreams()).toHaveLength(1);

    unmount2();
    await act(async () => {});
    expect(lastEs?.close).not.toHaveBeenCalled();

    unmount1();
    await act(async () => {});
    expect(lastEs?.close).toHaveBeenCalled();
    expect(openStreams()).toHaveLength(0);
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

    // Same kinds as `Widening` starts with, so nothing here widens the union:
    // this test is about the teardown race, not about coverage changes.
    function Leaving() {
      useOsEvents(["projects.task.changed"], () => {});
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
    settleHandoff();
    expect(MockEventSourceCtor).toHaveBeenCalledTimes(1);
    const opened = lastEs;

    // Widening to a kind already in the union costs no reopen, so anything
    // that closes here closed for the wrong reason.
    rerender(harness(["projects.task.changed"], false));

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

  it("keeps the shared stream when the last subscriber unmounts in the same commit as a new one mounts", async () => {
    function Leaving() {
      useOsEvents(["projects.task.changed"], () => {});
      return null;
    }

    // Same kinds as `Leaving`: the union does not move, so a reopen here can
    // only come from the teardown misreading the map.
    function Arriving() {
      useOsEvents(["projects.task.changed"], () => {});
      return null;
    }

    // Swapping the component at a position unmounts one and mounts the other
    // in a single commit. React runs every cleanup in that commit before any
    // setup, so the map is empty for a moment even though the window never
    // stops listening. Closing on that moment drops in-flight events and the
    // dedup window for nothing.
    const { rerender } = render(createElement(Leaving));
    settleHandoff();
    expect(MockEventSourceCtor).toHaveBeenCalledTimes(1);
    const opened = lastEs;

    rerender(createElement(Arriving));
    await act(async () => {});

    expect(opened?.close).not.toHaveBeenCalled();
    expect(MockEventSourceCtor).toHaveBeenCalledTimes(1);
  });

  it("does not cancel a scheduled reconnect when another subscriber mounts", async () => {
    vi.useFakeTimers();
    renderHook(() => useOsEvents(["projects.task.changed"], () => {}));

    act(() => {
      lastEs?.onopen?.();
    });
    act(() => {
      if (lastEs) {
        lastEs.readyState = EventSource.CLOSED;
        lastEs._fireError();
      }
    });
    expect(MockEventSourceCtor).toHaveBeenCalledTimes(1);

    // A subscriber mounting mid-backoff must not turn the scheduled retry into
    // an immediate reconnect. A view that mounts callers in a loop against a
    // down endpoint would otherwise retry at mount frequency, not 5s -> 30s.
    renderHook(() => useOsEvents(["agents.status.changed"], () => {}));
    expect(MockEventSourceCtor).toHaveBeenCalledTimes(1);

    // The retry it did NOT cancel still has to land.
    await act(async () => {
      await vi.advanceTimersByTimeAsync(5000);
    });
    expect(MockEventSourceCtor).toHaveBeenCalledTimes(2);

    vi.useRealTimers();
  });

  it("does not let a busy kind evict another subscriber's dedup window", () => {
    const quiet = vi.fn();
    const busy = vi.fn();

    renderHook(() => useOsEvents(["projects.task.changed"], quiet));
    renderHook(() => useOsEvents(["agents.status.changed"], busy));
    settleHandoff();

    act(() => {
      lastEs?._fire({ kind: "projects.task.changed", id: "quiet-1", ts: 1 });
    });
    expect(quiet).toHaveBeenCalledTimes(1);

    act(() => {
      for (let i = 0; i < 200; i += 1) {
        lastEs?._fire({ kind: "agents.status.changed", id: `busy-${i}`, ts: 2 });
      }
    });
    expect(busy).toHaveBeenCalledTimes(200);

    // The server replays the tail of EACH channel on reconnect, so `quiet-1`
    // can come back long after 200 events of somebody else's kind went past.
    // A dedup budget the quiet subscriber shares with the busy one has already
    // forgotten it, and the quiet handler refetches for nothing.
    act(() => {
      lastEs?._fire({ kind: "projects.task.changed", id: "quiet-1", ts: 1 });
    });
    expect(quiet).toHaveBeenCalledTimes(1);
  });

  it("asks the server for only the kinds someone subscribed to", () => {
    // The relay in tinyagentos/routes/os_events.py drops unrequested kinds
    // BEFORE its bounded 256-slot queue. If the desktop opened the stream
    // unfiltered, a busy kind nobody subscribed to would occupy those slots,
    // evict an event somebody did ask for, and raise an `events.lagged` that
    // from the subscriber's side never happened. The filter has to be upstream.
    renderHook(() => useOsEvents(["projects.task.changed"], () => {}));
    renderHook(() => useOsEvents(["notifications.new"], () => {}));
    settleHandoff();

    expect(openStreams()).toHaveLength(1);
    const url = openStreams()[0].url;
    expect(url).toBe(
      "/api/os/events?kinds=notifications.new,projects.task.changed",
    );
    expect(url).not.toContain("agents.status.changed");
  });

  it("keeps delivering on the old stream while a widened one takes over", () => {
    const onEvent = vi.fn();
    const { rerender } = renderHook(
      ({ kinds }: { kinds: string[] }) => useOsEvents(kinds, onEvent),
      { initialProps: { kinds: ["projects.task.changed"] } },
    );
    settleHandoff();
    const narrow = lastEs;

    rerender({ kinds: ["projects.task.changed", "notifications.new"] });
    const widened = lastEs;

    expect(widened).not.toBe(narrow);
    expect(widened?.url).toBe(
      "/api/os/events?kinds=notifications.new,projects.task.changed",
    );
    // The narrow stream is still live: closing it before its replacement is
    // open would drop every event in the gap, and this endpoint has no resume.
    expect(narrow?.close).not.toHaveBeenCalled();

    act(() => {
      narrow?._fire({
        kind: "projects.task.changed",
        id: "during-handoff",
        ts: 1,
      });
    });
    expect(onEvent).toHaveBeenCalledWith(
      expect.objectContaining({ id: "during-handoff" }),
    );

    act(() => {
      widened?.onopen?.();
    });
    expect(narrow?.close).toHaveBeenCalled();
    expect(openStreams()).toHaveLength(1);

    // The replay the widened stream opens with re-sends what the narrow one
    // already delivered; the per-subscriber dedup is what makes the overlap
    // safe rather than duplicated.
    act(() => {
      widened?._fire({
        kind: "projects.task.changed",
        id: "during-handoff",
        ts: 1,
      });
    });
    expect(onEvent).toHaveBeenCalledTimes(1);
  });

  it("keeps the narrow stream when the widened one fails to open", () => {
    const onEvent = vi.fn();
    const { rerender } = renderHook(
      ({ kinds }: { kinds: string[] }) => useOsEvents(kinds, onEvent),
      { initialProps: { kinds: ["projects.task.changed"] } },
    );
    settleHandoff();
    const narrow = lastEs;

    rerender({ kinds: ["projects.task.changed", "notifications.new"] });
    const widened = lastEs;

    act(() => {
      if (widened) {
        widened.readyState = EventSource.CLOSED;
        widened._fireError();
      }
    });

    // A failed widening must not take the working stream down with it.
    expect(narrow?.close).not.toHaveBeenCalled();
    act(() => {
      narrow?._fire({ kind: "projects.task.changed", id: "still-here", ts: 1 });
    });
    expect(onEvent).toHaveBeenCalledWith(
      expect.objectContaining({ id: "still-here" }),
    );
  });
});
