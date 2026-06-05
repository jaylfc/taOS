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

// Helper: build a fetch mock that returns a JSON response
function mockFetch(status: number, body: unknown): ReturnType<typeof vi.fn> {
  return vi.fn().mockResolvedValue({
    ok: status >= 200 && status < 300,
    status,
    json: () => Promise.resolve(body),
  });
}

describe("StreamedBrowserApp — running session", () => {
  it("renders LiveBrowserView with nekoUrl and streamToken when session is running", async () => {
    global.fetch = mockFetch(200, {
      id: "sess-1",
      status: "running",
      neko_url: "https://neko.local",
      stream_token: "tok-abc",
    });

    await act(async () => {
      render(<StreamedBrowserApp windowId={WINDOW_ID} />);
    });

    const view = screen.getByTestId("live-browser-view");
    expect(view).toBeTruthy();
    expect(view.getAttribute("data-neko-url")).toBe("https://neko.local");
    expect(view.getAttribute("data-stream-token")).toBe("tok-abc");
  });

  it("calls /api/browser/sessions/mine with credentials: include", async () => {
    const fetchMock = mockFetch(200, {
      id: "sess-1",
      status: "running",
      neko_url: "https://neko.local",
      stream_token: "tok-abc",
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

describe("StreamedBrowserApp — 409 no_capable_node", () => {
  it("shows gate-and-guide message, not a blank screen", async () => {
    global.fetch = mockFetch(409, { error: "no_capable_node" });

    await act(async () => {
      render(<StreamedBrowserApp windowId={WINDOW_ID} />);
    });

    const alert = screen.getByRole("alert");
    expect(alert).toBeTruthy();
    expect(alert.textContent).toMatch(/capable device/i);
    // No iframe rendered
    expect(screen.queryByTestId("live-browser-view")).toBeNull();
  });
});

describe("StreamedBrowserApp — error states", () => {
  it("shows error message and Retry button on server error", async () => {
    global.fetch = mockFetch(500, {});

    await act(async () => {
      render(<StreamedBrowserApp windowId={WINDOW_ID} />);
    });

    const alert = screen.getByRole("alert");
    expect(alert).toBeTruthy();
    const retryBtn = screen.getByRole("button", { name: /retry/i });
    expect(retryBtn).toBeTruthy();
  });

  it("shows error message and Retry button on network failure", async () => {
    global.fetch = vi.fn().mockRejectedValue(new TypeError("Network error"));

    await act(async () => {
      render(<StreamedBrowserApp windowId={WINDOW_ID} />);
    });

    expect(screen.getByRole("alert")).toBeTruthy();
    expect(screen.getByRole("button", { name: /retry/i })).toBeTruthy();
  });

  it("re-fetches when Retry is clicked", async () => {
    const fetchMock = vi.fn()
      .mockResolvedValueOnce({ ok: false, status: 500, json: () => Promise.resolve({}) })
      .mockResolvedValueOnce({
        ok: true,
        status: 200,
        json: () => Promise.resolve({ id: "s2", status: "running", neko_url: "https://neko.local", stream_token: "tok-xyz" }),
      });
    global.fetch = fetchMock;

    await act(async () => {
      render(<StreamedBrowserApp windowId={WINDOW_ID} />);
    });

    expect(screen.getByRole("alert")).toBeTruthy();

    await act(async () => {
      fireEvent.click(screen.getByRole("button", { name: /retry/i }));
    });

    expect(screen.getByTestId("live-browser-view")).toBeTruthy();
    expect(fetchMock).toHaveBeenCalledTimes(2);
  });
});

describe("StreamedBrowserApp — connecting/polling state", () => {
  it("shows connecting message when session is pending, then goes live after poll", async () => {
    // Use real timers for this test so waitFor works correctly alongside fake setTimeout
    vi.useRealTimers();

    const fetchMock = vi.fn()
      // First call: /mine returns pending
      .mockResolvedValueOnce({
        ok: true,
        status: 200,
        json: () => Promise.resolve({ id: "sess-pending", status: "pending", neko_url: null }),
      })
      // Poll call: session now running
      .mockResolvedValueOnce({
        ok: true,
        status: 200,
        json: () => Promise.resolve({ id: "sess-pending", status: "running", neko_url: "https://neko.local", stream_token: "tok-poll" }),
      });
    global.fetch = fetchMock;

    render(<StreamedBrowserApp windowId={WINDOW_ID} />);

    // After initial fetch resolves, should show connecting state
    await waitFor(() => {
      expect(screen.getByRole("status")).toBeTruthy();
    }, { timeout: 3000 });
    expect(screen.getByRole("status").textContent).toMatch(/waiting|starting/i);

    // The real poll fires after POLL_INTERVAL_MS (1500ms). Wait for it.
    await waitFor(() => {
      expect(screen.getByTestId("live-browser-view")).toBeTruthy();
    }, { timeout: 4000 });

    expect(screen.getByTestId("live-browser-view").getAttribute("data-stream-token")).toBe("tok-poll");
  }, 10000);
});
