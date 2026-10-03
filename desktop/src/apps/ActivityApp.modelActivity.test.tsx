import { render, screen, act, waitFor, fireEvent, cleanup, within } from "@testing-library/react";
import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { ModelActivityPanel } from "./ActivityApp.modelActivity";
import type { ModelActivityEvent } from "@/lib/model-activity-api";

// A controllable EventSource: the panel subscribes to
// /api/activity/models/stream and must fold live frames into the feed.
class FakeEventSource {
  static instances: FakeEventSource[] = [];
  static CONNECTING = 0;
  static OPEN = 1;
  static CLOSED = 2;
  readyState = 0;
  onopen: (() => void) | null = null;
  onmessage: ((msg: MessageEvent) => void) | null = null;
  onerror: (() => void) | null = null;
  constructor(public url: string) {
    FakeEventSource.instances.push(this);
  }
  close() {
    this.readyState = FakeEventSource.CLOSED;
  }
}

function ev(overrides: Partial<ModelActivityEvent> = {}): ModelActivityEvent {
  return {
    seq: 1,
    // The wire format is unix SECONDS (server: time.time()), like SystemEvent.ts.
    ts: Date.now() / 1000,
    event: "model.load",
    model: "qwen3-8b",
    worker: "controller",
    backend: "rkllama",
    duration_ms: null,
    tokens_in: null,
    tokens_out: null,
    token_rate: null,
    reason: null,
    detail: {},
    ...overrides,
  };
}

function mockFetch(events: ModelActivityEvent[]) {
  return vi.fn().mockImplementation((input: string) =>
    Promise.resolve({
      ok: true,
      status: 200,
      json: () => Promise.resolve({ events, count: events.length, event_types: ["model.load", "model.unload"] }),
    }),
  );
}

async function flush() {
  await act(async () => {
    await new Promise((r) => setTimeout(r, 0));
  });
}

describe("ModelActivityPanel", () => {
  beforeEach(() => {
    FakeEventSource.instances = [];
    vi.stubGlobal("EventSource", FakeEventSource);
  });

  afterEach(() => {
    cleanup();
    vi.unstubAllGlobals();
    vi.restoreAllMocks();
  });

  it("renders history from GET /api/activity/models", async () => {
    const fetchMock = mockFetch([
      ev({ seq: 2, event: "model.unload", model: "embed-x", worker: "pi-4" }),
      ev({ seq: 1, event: "model.load", model: "qwen3-8b" }),
    ]);
    vi.stubGlobal("fetch", fetchMock);

    render(<ModelActivityPanel />);
    await flush();

    expect(fetchMock).toHaveBeenCalledWith(
      "/api/activity/models?limit=200",
      expect.anything(),
    );
    const rows = await screen.findAllByTestId("model-activity-row");
    expect(rows).toHaveLength(2);
    // Scope to the rows: the filter <option>s carry the same model names.
    expect(within(rows[0]).getByText("embed-x")).toBeTruthy();
    expect(within(rows[0]).getByText("Unloaded")).toBeTruthy();
    expect(within(rows[1]).getByText("qwen3-8b")).toBeTruthy();
  });

  it("shows the empty state when nothing has happened", async () => {
    vi.stubGlobal("fetch", mockFetch([]));
    render(<ModelActivityPanel />);
    await flush();
    expect(screen.getByText(/no model activity yet/i)).toBeTruthy();
  });

  it("streams the SSE endpoint and prepends live events", async () => {
    vi.stubGlobal("fetch", mockFetch([ev({ seq: 1 })]));
    render(<ModelActivityPanel />);
    await flush();

    expect(FakeEventSource.instances).toHaveLength(1);
    expect(FakeEventSource.instances[0].url).toBe("/api/activity/models/stream?limit=0");

    await act(async () => {
      FakeEventSource.instances[0].onopen?.();
    });
    expect(screen.getByTestId("model-activity-status").textContent).toBe("live");

    await act(async () => {
      FakeEventSource.instances[0].onmessage?.({
        data: JSON.stringify(ev({ seq: 9, event: "model.evict", model: "old-embed" })),
      } as MessageEvent);
    });

    const rows = screen.getAllByTestId("model-activity-row");
    expect(rows).toHaveLength(2);
    expect(rows[0].textContent).toContain("old-embed");
    expect(rows[0].textContent).toContain("Evicted");
  });

  it("ignores a re-delivered frame instead of duplicating the row", async () => {
    vi.stubGlobal("fetch", mockFetch([ev({ seq: 1 })]));
    render(<ModelActivityPanel />);
    await flush();

    const frame = { data: JSON.stringify(ev({ seq: 1, model: "qwen3-8b" })) } as MessageEvent;
    await act(async () => {
      FakeEventSource.instances[0].onmessage?.(frame);
      FakeEventSource.instances[0].onmessage?.(frame);
    });

    expect(screen.getAllByTestId("model-activity-row")).toHaveLength(1);
  });

  it("refetches with the selected filters", async () => {
    const fetchMock = mockFetch([ev({ seq: 1 })]);
    vi.stubGlobal("fetch", fetchMock);
    render(<ModelActivityPanel />);
    await flush();

    await act(async () => {
      fireEvent.change(screen.getByLabelText("Filter by event type"), {
        target: { value: "model.load" },
      });
    });
    await flush();

    await waitFor(() =>
      expect(fetchMock).toHaveBeenCalledWith(
        "/api/activity/models?event=model.load&limit=200",
        expect.anything(),
      ),
    );
    expect(FakeEventSource.instances.at(-1)?.url).toBe(
      "/api/activity/models/stream?event=model.load&limit=0",
    );
  });

  it("renders the request finish duration and token rate", async () => {
    vi.stubGlobal(
      "fetch",
      mockFetch([
        ev({
          seq: 3,
          event: "request.finish",
          model: "qwen3-8b",
          duration_ms: 2500,
          tokens_out: 50,
          token_rate: 20,
        }),
      ]),
    );
    render(<ModelActivityPanel />);
    await flush();

    const row = (await screen.findAllByTestId("model-activity-row"))[0];
    expect(row.textContent).toContain("Completed");
    expect(row.textContent).toContain("2.5 s");
    expect(row.textContent).toContain("50 tok");
  });

  it("renders the timestamp as seconds-since, not a 1000x-misread date", async () => {
    // Regression guard: the server sends unix seconds; formatting it against
    // Date.now() milliseconds renders every row as ~20000d ago.
    vi.stubGlobal("fetch", mockFetch([ev({ seq: 1, ts: Date.now() / 1000 - 120 })]));
    render(<ModelActivityPanel />);
    await flush();

    const row = (await screen.findAllByTestId("model-activity-row"))[0];
    expect(row.textContent).toContain("2m ago");
    expect(row.textContent).not.toMatch(/\d+d ago/);
  });

  it("keeps live frames that arrive before the history response resolves", async () => {
    let resolveHistory: ((value: unknown) => void) | undefined;
    const fetchMock = vi.fn().mockImplementation(
      () => new Promise((resolve) => { resolveHistory = resolve; }),
    );
    vi.stubGlobal("fetch", fetchMock);
    render(<ModelActivityPanel />);
    await flush();

    // A live frame lands while GET /api/activity/models is still in flight.
    await act(async () => {
      FakeEventSource.instances[0].onmessage?.({
        data: JSON.stringify(ev({ seq: 9, model: "live-only" })),
      } as MessageEvent);
    });

    await act(async () => {
      resolveHistory?.({
        ok: true,
        status: 200,
        json: () =>
          Promise.resolve({ events: [ev({ seq: 5, model: "from-history" })], count: 1, event_types: [] }),
      });
      await Promise.resolve();
    });
    await flush();

    const rows = screen.getAllByTestId("model-activity-row");
    expect(rows).toHaveLength(2);
    expect(rows[0].textContent).toContain("live-only");
    expect(rows[1].textContent).toContain("from-history");
  });

  it("reports offline when the stream errors", async () => {
    vi.stubGlobal("fetch", mockFetch([]));
    render(<ModelActivityPanel />);
    await flush();

    await act(async () => {
      FakeEventSource.instances[0].onerror?.();
    });
    expect(screen.getByTestId("model-activity-status").textContent).toBe("offline");
  });
});