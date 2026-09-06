import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { renderHook, act } from "@testing-library/react";
import { useEventStream } from "./use-event-stream";
import { useNotificationStore } from "@/stores/notification-store";
import { useDecisionEventsStore } from "@/stores/decision-events-store";

// ---------------------------------------------------------------------------
// Mock EventSource — must use a regular function (not arrow) so `new` works
// ---------------------------------------------------------------------------

type MessageListener = (e: MessageEvent) => void;

interface MockEventSource {
  url: string;
  onmessage: MessageListener | null;
  onerror: ((e: Event) => void) | null;
  close: ReturnType<typeof vi.fn>;
  _fire: (data: unknown) => void;
}

let lastEs: MockEventSource | null = null;

// eslint-disable-next-line @typescript-eslint/no-explicit-any
const MockEventSourceCtor = vi.fn().mockImplementation(function (this: any, url: string) {
  this.url = url;
  this.onmessage = null as MessageListener | null;
  this.onerror = null;
  this.close = vi.fn();
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  this._fire = (data: unknown) => {
    (this as MockEventSource).onmessage?.({ data: JSON.stringify(data) } as MessageEvent);
  };
  lastEs = this as MockEventSource;
});

// ---------------------------------------------------------------------------
// Setup / teardown
// ---------------------------------------------------------------------------

beforeEach(() => {
  vi.stubGlobal("EventSource", MockEventSourceCtor);
  MockEventSourceCtor.mockClear();
  lastEs = null;
  // Reset the notification store so tests start clean
  useNotificationStore.setState({ notifications: [] });
  // Reset the decision events store so tests start clean
  useDecisionEventsStore.setState({ answeredEpoch: 0, lastAnsweredId: null });
});

afterEach(() => {
  vi.unstubAllGlobals();
});

// ---------------------------------------------------------------------------
// Tests
// ---------------------------------------------------------------------------

describe("useEventStream", () => {
  it("opens an EventSource to /api/events/stream on mount", () => {
    renderHook(() => useEventStream());
    expect(MockEventSourceCtor).toHaveBeenCalledWith("/api/events/stream");
  });

  it("closes the EventSource on unmount", () => {
    const { unmount } = renderHook(() => useEventStream());
    unmount();
    expect(lastEs?.close).toHaveBeenCalled();
  });

  it("routes notification.added into the notification store via mapRow", () => {
    renderHook(() => useEventStream());

    const row = {
      id: 42,
      timestamp: 1700000000,
      level: "info",
      title: "Agent joined",
      message: "worker-1 is now online",
      read: false,
      source: "worker.join",
      data: null,
    };

    act(() => {
      lastEs?._fire({ type: "notification.added", payload: row, ts: 1700000000 });
    });

    const notifications = useNotificationStore.getState().notifications;
    expect(notifications).toHaveLength(1);
    const n = notifications[0];
    expect(n.id).toBe("srv-42");
    expect(n.title).toBe("Agent joined");
    expect(n.body).toBe("worker-1 is now online");
    // timestamp is converted from seconds to milliseconds by mapRow
    expect(n.timestamp).toBe(1700000000 * 1000);
  });

  it("ignores unknown event types without throwing", () => {
    renderHook(() => useEventStream());
    expect(() => {
      act(() => {
        lastEs?._fire({ type: "future.event.type", payload: {}, ts: 0 });
      });
    }).not.toThrow();
    expect(useNotificationStore.getState().notifications).toHaveLength(0);
  });

  it("ignores malformed JSON without throwing", () => {
    renderHook(() => useEventStream());
    expect(() => {
      act(() => {
        lastEs?.onmessage?.({ data: "not json {{" } as MessageEvent);
      });
    }).not.toThrow();
  });

  it("ignores messages with no payload without throwing", () => {
    renderHook(() => useEventStream());
    expect(() => {
      act(() => {
        lastEs?._fire({ type: "notification.added" }); // no payload
      });
    }).not.toThrow();
  });

  it("merges multiple notification.added events", () => {
    renderHook(() => useEventStream());

    const base = {
      timestamp: 1700000000,
      level: "info",
      message: "msg",
      read: false,
      source: "system",
      data: null,
    };

    act(() => {
      lastEs?._fire({ type: "notification.added", payload: { ...base, id: 1, title: "First" } });
      lastEs?._fire({ type: "notification.added", payload: { ...base, id: 2, title: "Second" } });
    });

    const notifications = useNotificationStore.getState().notifications;
    expect(notifications).toHaveLength(2);
    const ids = notifications.map((n) => n.id);
    expect(ids).toContain("srv-1");
    expect(ids).toContain("srv-2");
  });

  it("routes decision.answered into the decision events store", () => {
    renderHook(() => useEventStream());

    act(() => {
      lastEs?._fire({
        type: "decision.answered",
        payload: { decision_id: "dec-1", id: "dec-1", answer: { value: "approve" } },
        ts: 1700000000,
      });
    });

    const state = useDecisionEventsStore.getState();
    expect(state.lastAnsweredId).toBe("dec-1");
    expect(state.answeredEpoch).toBe(1);
  });

  it("does not update decision events store for unrelated events", () => {
    renderHook(() => useEventStream());

    act(() => {
      lastEs?._fire({ type: "future.event.type", payload: {}, ts: 0 });
    });

    const state = useDecisionEventsStore.getState();
    expect(state.lastAnsweredId).toBeNull();
    expect(state.answeredEpoch).toBe(0);
  });
});
