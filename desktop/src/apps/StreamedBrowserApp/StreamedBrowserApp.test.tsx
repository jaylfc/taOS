import { describe, it, expect, beforeEach, afterEach, vi } from "vitest";
import { render, screen, waitFor, act, fireEvent } from "@testing-library/react";
import { StreamedBrowserApp } from "./StreamedBrowserApp";

// LiveBrowserView renders an iframe — stub it so we can assert on its props
// without needing a real DOM iframe environment.
vi.mock("@/apps/BrowserApp/LiveBrowserView", () => ({
  LiveBrowserView: ({ nekoUrl, streamToken }: { nekoUrl: string; streamToken: string }) => (
    <div data-testid="live-browser-view" data-neko-url={nekoUrl} data-stream-token={streamToken} />
  ),
}));

// Radix Tooltip uses pointerEvents; jsdom doesn't fire them by default.
// Mock Tooltip so it renders children + content inline.
vi.mock("@radix-ui/react-tooltip", () => ({
  Provider: ({ children }: { children: React.ReactNode }) => <>{children}</>,
  Root: ({ children }: { children: React.ReactNode }) => <>{children}</>,
  Trigger: ({ children }: { children: React.ReactNode; asChild?: boolean }) => <>{children}</>,
  Portal: ({ children }: { children: React.ReactNode }) => <>{children}</>,
  Content: ({ children }: { children: React.ReactNode }) => (
    <div data-testid="tooltip-content">{children}</div>
  ),
  Arrow: () => null,
}));

const WINDOW_ID = "win-sb-test";

const originalFetch = global.fetch;

beforeEach(() => {
  vi.useFakeTimers({ shouldAdvanceTime: false });
});

afterEach(() => {
  global.fetch = originalFetch;
  vi.restoreAllMocks();
  vi.useRealTimers();
});

// ── Helpers ──────────────────────────────────────────────────────────────────

function mockFetch(status: number, body: unknown): ReturnType<typeof vi.fn> {
  return vi.fn().mockResolvedValue({
    ok: status >= 200 && status < 300,
    status,
    json: () => Promise.resolve(body),
  });
}

/** Build a fetch mock that routes requests by URL fragment. */
function routedFetch(routes: Record<string, { status: number; body: unknown }>) {
  return vi.fn().mockImplementation((url: string) => {
    for (const [pattern, resp] of Object.entries(routes)) {
      if (url.includes(pattern)) {
        return Promise.resolve({
          ok: resp.status >= 200 && resp.status < 300,
          status: resp.status,
          json: () => Promise.resolve(resp.body),
        });
      }
    }
    return Promise.resolve({ ok: false, status: 404, json: () => Promise.resolve({}) });
  });
}

const runningMineSession = {
  id: "mine-1",
  owner_type: "user",
  owner_id: "user-1",
  status: "running",
  neko_url: "https://neko.local",
  stream_token: "tok-mine",
};

const agentSession = {
  id: "agent-sess-1",
  owner_type: "agent",
  owner_id: "researcher",
  status: "running",
  neko_url: "https://neko-agent.local",
  url: "https://example.com",
  stream_token: "tok-agent",
};

const sessionListWithAgent = {
  sessions: [
    {
      id: "mine-1",
      owner_type: "user",
      owner_id: "user-1",
      status: "running",
      neko_url: "https://neko.local",
      url: null,
    },
    {
      id: "agent-sess-1",
      owner_type: "agent",
      owner_id: "researcher",
      status: "running",
      neko_url: "https://neko-agent.local",
      url: "https://example.com",
    },
  ],
};

// ── C1 regression: My browser running session ─────────────────────────────────

describe("StreamedBrowserApp — My browser running session (C1 regression)", () => {
  it("renders LiveBrowserView with nekoUrl and streamToken when session is running", async () => {
    global.fetch = routedFetch({
      "/api/browser/sessions/mine": { status: 200, body: runningMineSession },
      "/api/browser/sessions": { status: 200, body: { sessions: [] } },
    });

    await act(async () => {
      render(<StreamedBrowserApp windowId={WINDOW_ID} />);
    });

    const view = screen.getByTestId("live-browser-view");
    expect(view).toBeTruthy();
    expect(view.getAttribute("data-neko-url")).toBe("https://neko.local");
    expect(view.getAttribute("data-stream-token")).toBe("tok-mine");
  });

  it("calls /api/browser/sessions/mine with credentials: include", async () => {
    const fetchMock = routedFetch({
      "/api/browser/sessions/mine": { status: 200, body: runningMineSession },
      "/api/browser/sessions": { status: 200, body: { sessions: [] } },
    });
    global.fetch = fetchMock;

    await act(async () => {
      render(<StreamedBrowserApp windowId={WINDOW_ID} />);
    });

    expect(fetchMock).toHaveBeenCalledWith(
      "/api/browser/sessions/mine",
      expect.objectContaining({ credentials: "include" }),
    );
  });
});

// ── Session switcher ──────────────────────────────────────────────────────────

describe("StreamedBrowserApp — session switcher", () => {
  it("renders 'My browser' entry", async () => {
    global.fetch = routedFetch({
      "/api/browser/sessions/mine": { status: 200, body: runningMineSession },
      "/api/browser/sessions": { status: 200, body: { sessions: [] } },
    });

    await act(async () => {
      render(<StreamedBrowserApp windowId={WINDOW_ID} />);
    });

    expect(screen.getByRole("button", { name: /my browser/i })).toBeTruthy();
  });

  it("renders agent sessions from GET /api/browser/sessions", async () => {
    global.fetch = routedFetch({
      "/api/browser/sessions/mine": { status: 200, body: runningMineSession },
      "/api/browser/sessions": { status: 200, body: sessionListWithAgent },
    });

    await act(async () => {
      render(<StreamedBrowserApp windowId={WINDOW_ID} />);
    });

    // The agent name "researcher" should appear as a button in the rail
    const btn = screen.getAllByRole("button").find((b) => b.textContent?.includes("researcher"));
    expect(btn).toBeTruthy();
  });

  it("selecting an agent session fetches /{id} and renders LiveBrowserView", async () => {
    global.fetch = routedFetch({
      "/api/browser/sessions/mine": { status: 200, body: runningMineSession },
      "/api/browser/sessions/agent-sess-1": { status: 200, body: agentSession },
      "/api/browser/sessions": { status: 200, body: sessionListWithAgent },
    });

    await act(async () => {
      render(<StreamedBrowserApp windowId={WINDOW_ID} />);
    });

    // Click the agent session button
    const agentBtn = screen.getAllByRole("button").find((b) =>
      b.textContent?.includes("researcher"),
    );
    expect(agentBtn).toBeTruthy();

    await act(async () => {
      fireEvent.click(agentBtn!);
    });

    // Should now show the agent's live stream
    const view = screen.getByTestId("live-browser-view");
    expect(view.getAttribute("data-neko-url")).toBe("https://neko-agent.local");
    expect(view.getAttribute("data-stream-token")).toBe("tok-agent");
  });

  it("shows 'Watching <agent>' label when viewing an agent session", async () => {
    global.fetch = routedFetch({
      "/api/browser/sessions/mine": { status: 200, body: runningMineSession },
      "/api/browser/sessions/agent-sess-1": { status: 200, body: agentSession },
      "/api/browser/sessions": { status: 200, body: sessionListWithAgent },
    });

    await act(async () => {
      render(<StreamedBrowserApp windowId={WINDOW_ID} />);
    });

    const agentBtn = screen.getAllByRole("button").find((b) =>
      b.textContent?.includes("researcher"),
    );
    await act(async () => {
      fireEvent.click(agentBtn!);
    });

    expect(screen.getByText(/watching researcher/i)).toBeTruthy();
  });

  it("request-control button is disabled on agent sessions", async () => {
    global.fetch = routedFetch({
      "/api/browser/sessions/mine": { status: 200, body: runningMineSession },
      "/api/browser/sessions/agent-sess-1": { status: 200, body: agentSession },
      "/api/browser/sessions": { status: 200, body: sessionListWithAgent },
    });

    await act(async () => {
      render(<StreamedBrowserApp windowId={WINDOW_ID} />);
    });

    const agentBtn = screen.getAllByRole("button").find((b) =>
      b.textContent?.includes("researcher"),
    );
    await act(async () => {
      fireEvent.click(agentBtn!);
    });

    const reqCtrl = screen.getByRole("button", { name: /request control/i });
    expect(reqCtrl).toBeTruthy();
    expect(reqCtrl).toBeDisabled();
  });

  it("no request-control button on My browser view", async () => {
    global.fetch = routedFetch({
      "/api/browser/sessions/mine": { status: 200, body: runningMineSession },
      "/api/browser/sessions": { status: 200, body: { sessions: [] } },
    });

    await act(async () => {
      render(<StreamedBrowserApp windowId={WINDOW_ID} />);
    });

    expect(screen.queryByRole("button", { name: /request control/i })).toBeNull();
  });
});

// ── Migrating state ───────────────────────────────────────────────────────────

describe("StreamedBrowserApp — migrating state", () => {
  it("renders migrating message when mine session status is migrating", async () => {
    global.fetch = routedFetch({
      "/api/browser/sessions/mine": {
        status: 200,
        body: { id: "mine-1", status: "migrating", neko_url: null, stream_token: null },
      },
      "/api/browser/sessions": { status: 200, body: { sessions: [] } },
      // Poll call returns the same migrating state (no progression needed for this test)
      "/api/browser/sessions/mine-1": {
        status: 200,
        body: { id: "mine-1", status: "migrating", neko_url: null, stream_token: null },
      },
    });

    await act(async () => {
      render(<StreamedBrowserApp windowId={WINDOW_ID} />);
    });

    const status = screen.getByRole("status");
    expect(status.textContent).toMatch(/moving.*another device/i);
    expect(screen.queryByTestId("live-browser-view")).toBeNull();
  });

  it("renders migrating message when agent session status is migrating", async () => {
    global.fetch = routedFetch({
      "/api/browser/sessions/mine": { status: 200, body: runningMineSession },
      "/api/browser/sessions/agent-sess-1": {
        status: 200,
        body: { id: "agent-sess-1", owner_type: "agent", owner_id: "researcher", status: "migrating", neko_url: null },
      },
      "/api/browser/sessions": { status: 200, body: sessionListWithAgent },
    });

    await act(async () => {
      render(<StreamedBrowserApp windowId={WINDOW_ID} />);
    });

    const agentBtn = screen.getAllByRole("button").find((b) =>
      b.textContent?.includes("researcher"),
    );
    await act(async () => {
      fireEvent.click(agentBtn!);
    });

    const status = screen.getByRole("status");
    expect(status.textContent).toMatch(/moving.*another device/i);
  });
});

// ── 409 no_capable_node ───────────────────────────────────────────────────────

describe("StreamedBrowserApp — 409 no_capable_node", () => {
  it("shows gate-and-guide message, not a blank screen", async () => {
    global.fetch = routedFetch({
      "/api/browser/sessions/mine": { status: 409, body: { error: "no_capable_node" } },
      "/api/browser/sessions": { status: 200, body: { sessions: [] } },
    });

    await act(async () => {
      render(<StreamedBrowserApp windowId={WINDOW_ID} />);
    });

    const alert = screen.getByRole("alert");
    expect(alert).toBeTruthy();
    expect(alert.textContent).toMatch(/capable device/i);
    expect(screen.queryByTestId("live-browser-view")).toBeNull();
  });
});

// ── Error states ──────────────────────────────────────────────────────────────

describe("StreamedBrowserApp — error states", () => {
  it("shows error message and Retry button on server error", async () => {
    global.fetch = routedFetch({
      "/api/browser/sessions/mine": { status: 500, body: {} },
      "/api/browser/sessions": { status: 200, body: { sessions: [] } },
    });

    await act(async () => {
      render(<StreamedBrowserApp windowId={WINDOW_ID} />);
    });

    const alert = screen.getByRole("alert");
    expect(alert).toBeTruthy();
    const retryBtn = screen.getByRole("button", { name: /retry/i });
    expect(retryBtn).toBeTruthy();
  });

  it("shows error message and Retry button on network failure", async () => {
    global.fetch = vi.fn().mockImplementation((url: string) => {
      if ((url as string).includes("/mine")) return Promise.reject(new TypeError("Network error"));
      return Promise.resolve({ ok: true, status: 200, json: () => Promise.resolve({ sessions: [] }) });
    });

    await act(async () => {
      render(<StreamedBrowserApp windowId={WINDOW_ID} />);
    });

    expect(screen.getByRole("alert")).toBeTruthy();
    expect(screen.getByRole("button", { name: /retry/i })).toBeTruthy();
  });

  it("re-fetches when Retry is clicked", async () => {
    let mineCallCount = 0;
    global.fetch = vi.fn().mockImplementation((url: string) => {
      if ((url as string).includes("/mine")) {
        mineCallCount += 1;
        if (mineCallCount === 1) {
          // First /mine call — fail
          return Promise.resolve({ ok: false, status: 500, json: () => Promise.resolve({}) });
        }
        // Retry /mine call — succeed
        return Promise.resolve({
          ok: true, status: 200,
          json: () => Promise.resolve({ id: "s2", status: "running", neko_url: "https://neko.local", stream_token: "tok-xyz" }),
        });
      }
      // /sessions list — always OK
      return Promise.resolve({ ok: true, status: 200, json: () => Promise.resolve({ sessions: [] }) });
    });

    await act(async () => {
      render(<StreamedBrowserApp windowId={WINDOW_ID} />);
    });

    expect(screen.getByRole("alert")).toBeTruthy();

    await act(async () => {
      fireEvent.click(screen.getByRole("button", { name: /retry/i }));
    });

    expect(screen.getByTestId("live-browser-view")).toBeTruthy();
  });
});

// ── Connecting/polling state ──────────────────────────────────────────────────

describe("StreamedBrowserApp — connecting/polling state", () => {
  it("shows connecting message when session is pending, then goes live after poll", async () => {
    vi.useRealTimers();

    const fetchMock = vi.fn()
      // /sessions list
      .mockResolvedValueOnce({
        ok: true, status: 200,
        json: () => Promise.resolve({ sessions: [] }),
      })
      // /mine returns pending
      .mockResolvedValueOnce({
        ok: true, status: 200,
        json: () => Promise.resolve({ id: "sess-pending", status: "pending", neko_url: null }),
      })
      // Poll call: session now running
      .mockResolvedValueOnce({
        ok: true, status: 200,
        json: () => Promise.resolve({ id: "sess-pending", status: "running", neko_url: "https://neko.local", stream_token: "tok-poll" }),
      });
    global.fetch = fetchMock;

    render(<StreamedBrowserApp windowId={WINDOW_ID} />);

    await waitFor(() => {
      expect(screen.getByRole("status")).toBeTruthy();
    }, { timeout: 3000 });
    expect(screen.getByRole("status").textContent).toMatch(/waiting|starting/i);

    await waitFor(() => {
      expect(screen.getByTestId("live-browser-view")).toBeTruthy();
    }, { timeout: 4000 });

    expect(screen.getByTestId("live-browser-view").getAttribute("data-stream-token")).toBe("tok-poll");
  }, 10000);
});
