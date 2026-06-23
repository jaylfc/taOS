import { render, screen, fireEvent, act, waitFor } from "@testing-library/react";
import { describe, it, expect, vi, beforeEach } from "vitest";
import { ObservatoryApp } from "./ObservatoryApp";

function mockFetch(
  responses: Record<string, { ok: boolean; status?: number; body: unknown }>,
) {
  return vi.fn().mockImplementation((input: string, init?: RequestInit) => {
    const method = (init?.method ?? "GET").toUpperCase();
    const hit = responses[`${method} ${input}`] ?? responses[input] ?? responses["*"];
    if (!hit) throw new Error(`Unmocked fetch: ${method} ${input}`);
    return Promise.resolve({
      ok: hit.ok,
      status: hit.status ?? (hit.ok ? 200 : 422),
      json: () => Promise.resolve(hit.body),
    });
  });
}

async function flush() {
  await act(async () => {
    await new Promise((r) => setTimeout(r, 0));
  });
}

const fleetBody = {
  agents: [
    {
      handle: "@taOS-dev-kilo-owl-alpha",
      state: "working",
      holds: { task_id: "tsk-1", project_id: "prj-x", title: "Add tests" },
    },
  ],
  paused: { global: false, lanes: {} },
};

describe("ObservatoryApp", () => {
  beforeEach(() => vi.restoreAllMocks());

  it("renders the fleet with the held card", async () => {
    vi.stubGlobal(
      "fetch",
      mockFetch({ "GET /api/observatory/fleet": { ok: true, body: fleetBody } }),
    );
    render(<ObservatoryApp windowId="w1" />);
    await flush();
    await waitFor(() =>
      expect(screen.getByText("@taOS-dev-kilo-owl-alpha")).toBeTruthy(),
    );
    expect(screen.getByText(/Add tests/)).toBeTruthy();
  });

  it("posts a global pause when the queue toggle is clicked", async () => {
    const fetchMock = mockFetch({
      "GET /api/observatory/fleet": { ok: true, body: fleetBody },
      "POST /api/observatory/pause": { ok: true, body: { global: true, lanes: {} } },
    });
    vi.stubGlobal("fetch", fetchMock);
    render(<ObservatoryApp windowId="w1" />);
    await flush();

    fireEvent.click(screen.getByRole("button", { name: /pause queue/i }));
    await flush();

    const post = fetchMock.mock.calls.find(
      (c) => (c[1] as RequestInit)?.method === "POST",
    );
    expect(post![0]).toBe("/api/observatory/pause");
    const sent = JSON.parse((post![1] as RequestInit).body as string);
    expect(sent).toEqual({ scope: "global", paused: true });
  });

  it("posts a per-lane pause from the lane switch", async () => {
    const fetchMock = mockFetch({
      "GET /api/observatory/fleet": { ok: true, body: fleetBody },
      "POST /api/observatory/pause": {
        ok: true,
        body: { global: false, lanes: { "@taOS-dev-kilo-owl-alpha": true } },
      },
    });
    vi.stubGlobal("fetch", fetchMock);
    render(<ObservatoryApp windowId="w1" />);
    await flush();

    fireEvent.click(screen.getByRole("switch", { name: /pause lane/i }));
    await flush();

    const post = fetchMock.mock.calls.find(
      (c) => (c[1] as RequestInit)?.method === "POST",
    );
    const sent = JSON.parse((post![1] as RequestInit).body as string);
    expect(sent).toEqual({ scope: "@taOS-dev-kilo-owl-alpha", paused: true });
  });

  it("shows the idle empty state when no agents are working", async () => {
    vi.stubGlobal(
      "fetch",
      mockFetch({
        "GET /api/observatory/fleet": {
          ok: true,
          body: { agents: [], paused: { global: false, lanes: {} } },
        },
      }),
    );
    render(<ObservatoryApp windowId="w1" />);
    await flush();
    await waitFor(() => expect(screen.getByText(/all lanes idle/i)).toBeTruthy());
  });
});

describe("ObservatoryApp polling", () => {
  it("re-fetches the fleet on an interval so status stays live", async () => {
    vi.useFakeTimers();
    const fetchMock = mockFetch({
      "GET /api/observatory/fleet": { ok: true, body: fleetBody },
    });
    vi.stubGlobal("fetch", fetchMock);
    render(<ObservatoryApp windowId="w1" />);
    await act(async () => { await Promise.resolve(); });
    const initial = fetchMock.mock.calls.length;
    expect(initial).toBeGreaterThanOrEqual(1);
    await act(async () => { vi.advanceTimersByTime(5000); await Promise.resolve(); });
    expect(fetchMock.mock.calls.length).toBeGreaterThan(initial);
    vi.useRealTimers();
  });
});
