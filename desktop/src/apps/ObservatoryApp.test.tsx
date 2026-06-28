import { render, screen, fireEvent, act, waitFor } from "@testing-library/react";
import { describe, it, expect, vi, beforeEach } from "vitest";
import { ObservatoryApp } from "./ObservatoryApp";

function mockFetch(
  responses: Record<string, { ok: boolean; status?: number; body: unknown }>,
) {
  return vi.fn().mockImplementation((input: string, init?: RequestInit) => {
    const method = (init?.method ?? "GET").toUpperCase();
    let hit = responses[`${method} ${input}`] ?? responses[input] ?? responses["*"];
    // The app loads the throttle state alongside the fleet; default it to
    // "no cap" so tests that only care about the fleet need not mock it.
    if (!hit && method === "GET" && input === "/api/observatory/throttle") {
      hit = { ok: true, body: { global: null, lanes: {} } };
    }
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

  it("renders the loaded global concurrency cap", async () => {
    vi.stubGlobal(
      "fetch",
      mockFetch({
        "GET /api/observatory/fleet": { ok: true, body: fleetBody },
        "GET /api/observatory/throttle": { ok: true, body: { global: 4, lanes: {} } },
      }),
    );
    render(<ObservatoryApp windowId="w1" />);
    await flush();
    await waitFor(() =>
      expect(screen.getByLabelText(/concurrency cap value/i).textContent).toBe("4"),
    );
  });

  it("raises the cap and posts the new value to the throttle endpoint", async () => {
    const fetchMock = mockFetch({
      "GET /api/observatory/fleet": { ok: true, body: fleetBody },
      "GET /api/observatory/throttle": { ok: true, body: { global: null, lanes: {} } },
      "POST /api/observatory/throttle": { ok: true, body: { global: 1, lanes: {} } },
    });
    vi.stubGlobal("fetch", fetchMock);
    render(<ObservatoryApp windowId="w1" />);
    await flush();

    fireEvent.click(screen.getByRole("button", { name: /raise concurrency cap/i }));
    await flush();

    const post = fetchMock.mock.calls.find(
      (c) => (c[1] as RequestInit)?.method === "POST",
    );
    expect(post![0]).toBe("/api/observatory/throttle");
    expect(JSON.parse((post![1] as RequestInit).body as string)).toEqual({
      scope: "global",
      max_concurrent: 1,
    });
  });

  it("clears the cap to null via the Clear button", async () => {
    const fetchMock = mockFetch({
      "GET /api/observatory/fleet": { ok: true, body: fleetBody },
      "GET /api/observatory/throttle": { ok: true, body: { global: 1, lanes: {} } },
      "POST /api/observatory/throttle": { ok: true, body: { global: null, lanes: {} } },
    });
    vi.stubGlobal("fetch", fetchMock);
    render(<ObservatoryApp windowId="w1" />);
    await flush();
    await waitFor(() =>
      expect(screen.getByLabelText(/concurrency cap value/i).textContent).toBe("1"),
    );

    // The lower button floors at 1 (disabled there); removal is the explicit Clear.
    expect(
      (screen.getByRole("button", { name: /lower concurrency cap/i }) as HTMLButtonElement)
        .disabled,
    ).toBe(true);
    fireEvent.click(screen.getByRole("button", { name: /^clear$/i }));
    await flush();

    const post = fetchMock.mock.calls.find(
      (c) => (c[1] as RequestInit)?.method === "POST",
    );
    expect(JSON.parse((post![1] as RequestInit).body as string)).toEqual({
      scope: "global",
      max_concurrent: null,
    });
  });

  it("disables raising the cap at the ceiling", async () => {
    vi.stubGlobal(
      "fetch",
      mockFetch({
        "GET /api/observatory/fleet": { ok: true, body: fleetBody },
        "GET /api/observatory/throttle": { ok: true, body: { global: 50, lanes: {} } },
      }),
    );
    render(<ObservatoryApp windowId="w1" />);
    await flush();
    await waitFor(() =>
      expect(screen.getByLabelText(/concurrency cap value/i).textContent).toBe("50"),
    );
    expect(
      (screen.getByRole("button", { name: /raise concurrency cap/i }) as HTMLButtonElement)
        .disabled,
    ).toBe(true);
  });

  it("renders a per-lane concurrency cap loaded from the throttle state", async () => {
    vi.stubGlobal(
      "fetch",
      mockFetch({
        "GET /api/observatory/fleet": { ok: true, body: fleetBody },
        "GET /api/observatory/throttle": {
          ok: true,
          body: { global: null, lanes: { "@taOS-dev-kilo-owl-alpha": 2 } },
        },
      }),
    );
    render(<ObservatoryApp windowId="w1" />);
    await flush();
    await waitFor(() =>
      expect(
        screen.getByLabelText(/@taOS-dev-kilo-owl-alpha limit value/i).textContent,
      ).toBe("2"),
    );
  });

  it("posts a per-lane cap scoped to the lane handle", async () => {
    const fetchMock = mockFetch({
      "GET /api/observatory/fleet": { ok: true, body: fleetBody },
      "GET /api/observatory/throttle": { ok: true, body: { global: null, lanes: {} } },
      "POST /api/observatory/throttle": {
        ok: true,
        body: { global: null, lanes: { "@taOS-dev-kilo-owl-alpha": 1 } },
      },
    });
    vi.stubGlobal("fetch", fetchMock);
    render(<ObservatoryApp windowId="w1" />);
    await flush();

    fireEvent.click(
      screen.getByRole("button", { name: /raise @taOS-dev-kilo-owl-alpha limit/i }),
    );
    await flush();

    const post = fetchMock.mock.calls.find(
      (c) => (c[1] as RequestInit)?.method === "POST",
    );
    expect(post![0]).toBe("/api/observatory/throttle");
    expect(JSON.parse((post![1] as RequestInit).body as string)).toEqual({
      scope: "@taOS-dev-kilo-owl-alpha",
      max_concurrent: 1,
    });
  });

  it("surfaces an error when a steer post is rejected by the server", async () => {
    const fetchMock = mockFetch({
      "GET /api/observatory/fleet": { ok: true, body: fleetBody },
      "GET /api/observatory/throttle": { ok: true, body: { global: null, lanes: {} } },
      "POST /api/observatory/pause": { ok: false, status: 403, body: { detail: "forbidden" } },
    });
    vi.stubGlobal("fetch", fetchMock);
    render(<ObservatoryApp windowId="w1" />);
    await flush();

    fireEvent.click(screen.getByRole("button", { name: /pause queue/i }));
    await flush();

    await waitFor(() =>
      expect(screen.getByText(/could not update the pause state/i)).toBeTruthy(),
    );
  });

  it("clears the steer error after a subsequent successful post", async () => {
    let pauseOk = false;
    const fetchMock = vi.fn().mockImplementation((input: string, init?: RequestInit) => {
      const method = (init?.method ?? "GET").toUpperCase();
      if (input === "/api/observatory/fleet") {
        return Promise.resolve({ ok: true, status: 200, json: () => Promise.resolve(fleetBody) });
      }
      if (input === "/api/observatory/throttle") {
        return Promise.resolve({ ok: true, status: 200, json: () => Promise.resolve({ global: null, lanes: {} }) });
      }
      if (method === "POST" && input === "/api/observatory/pause") {
        const ok = pauseOk;
        pauseOk = true; // first attempt fails, the next succeeds
        return Promise.resolve({ ok, status: ok ? 200 : 403, json: () => Promise.resolve({}) });
      }
      throw new Error(`Unmocked fetch: ${method} ${input}`);
    });
    vi.stubGlobal("fetch", fetchMock);
    render(<ObservatoryApp windowId="w1" />);
    await flush();

    fireEvent.click(screen.getByRole("button", { name: /pause queue/i }));
    await flush();
    await waitFor(() =>
      expect(screen.getByText(/could not update the pause state/i)).toBeTruthy(),
    );

    fireEvent.click(screen.getByRole("button", { name: /pause queue/i }));
    await flush();
    await waitFor(() =>
      expect(screen.queryByText(/could not update the pause state/i)).toBeNull(),
    );
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

  it("skips the background poll while a steer write is in flight", async () => {
    vi.useFakeTimers();
    let resolvePost: (() => void) | null = null;
    const fetchMock = vi.fn().mockImplementation((input: string, init?: RequestInit) => {
      const method = (init?.method ?? "GET").toUpperCase();
      if (method === "POST" && input === "/api/observatory/pause") {
        return new Promise((res) => {
          resolvePost = () =>
            res({ ok: true, status: 200, json: () => Promise.resolve({}) });
        });
      }
      if (input === "/api/observatory/throttle") {
        return Promise.resolve({
          ok: true,
          status: 200,
          json: () => Promise.resolve({ global: null, lanes: {} }),
        });
      }
      return Promise.resolve({
        ok: true,
        status: 200,
        json: () => Promise.resolve(fleetBody),
      });
    });
    vi.stubGlobal("fetch", fetchMock);
    render(<ObservatoryApp windowId="w1" />);
    await act(async () => { await Promise.resolve(); });

    // Start a pause write whose POST stays pending so inFlight stays non-zero.
    fireEvent.click(screen.getByRole("button", { name: /pause queue/i }));
    await act(async () => { await Promise.resolve(); });

    const fleetCalls = () =>
      fetchMock.mock.calls.filter((c) => c[0] === "/api/observatory/fleet").length;
    const before = fleetCalls();

    // The 5s tick must NOT background-fetch the fleet while the write is pending.
    await act(async () => { vi.advanceTimersByTime(5000); await Promise.resolve(); });
    expect(fleetCalls()).toBe(before);

    // Once the write resolves, postSteer's reconcile fetches the fleet.
    await act(async () => {
      resolvePost?.();
      await Promise.resolve();
      await Promise.resolve();
    });
    expect(fleetCalls()).toBeGreaterThan(before);
    vi.useRealTimers();
  });

  it("a finished write's reconcile does not revert another in-flight write", async () => {
    let resolvePause: (() => void) | null = null;
    const fetchMock = vi.fn().mockImplementation((input: string, init?: RequestInit) => {
      const method = (init?.method ?? "GET").toUpperCase();
      if (method === "POST" && input === "/api/observatory/pause") {
        // The pause POST stays pending until we release it.
        return new Promise((res) => {
          resolvePause = () =>
            res({ ok: true, status: 200, json: () => Promise.resolve({}) });
        });
      }
      if (method === "POST" && input === "/api/observatory/throttle") {
        // The cap POST resolves immediately, but the server read still reports
        // no cap (as if the change has not propagated yet).
        return Promise.resolve({ ok: true, status: 200, json: () => Promise.resolve({}) });
      }
      if (input === "/api/observatory/throttle") {
        return Promise.resolve({
          ok: true,
          status: 200,
          json: () => Promise.resolve({ global: null, lanes: {} }),
        });
      }
      return Promise.resolve({
        ok: true,
        status: 200,
        json: () => Promise.resolve(fleetBody),
      });
    });
    vi.stubGlobal("fetch", fetchMock);
    render(<ObservatoryApp windowId="w1" />);
    await flush();

    // Start a cap raise (optimistic cap -> 1) AND a pause whose POST stays
    // pending, so a write is still in flight when the cap write reconciles.
    fireEvent.click(screen.getByRole("button", { name: /pause queue/i }));
    fireEvent.click(screen.getByRole("button", { name: /raise concurrency cap/i }));
    await flush();

    // The cap write's reconcile read says "no cap", but the pause write is still
    // in flight, so the optimistic cap must be preserved (not reverted to 0).
    expect(screen.getByLabelText(/concurrency cap value/i).textContent).toBe("1");

    await act(async () => { resolvePause?.(); await Promise.resolve(); });
  });
});
