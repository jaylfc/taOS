import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { render, screen, fireEvent, act, within } from "@testing-library/react";
import { NotificationHistoryApp } from "./NotificationHistoryApp";
import { rangeBounds } from "./NotificationHistoryApp";
import { useNotificationStore } from "@/stores/notification-store";
import { SEARCH_DEBOUNCE_MS } from "./NotificationHistoryApp";

type Row = {
  id: number;
  timestamp: number;
  level: string;
  title: string;
  message: string;
  read: boolean;
  source: string;
  data: null;
  archived: boolean;
};

function row(id: number, over: Partial<Row> = {}): Row {
  return {
    id,
    timestamp: 1_700_000_000 - id,
    level: "info",
    title: `Note ${id}`,
    message: `Message ${id}`,
    read: true,
    source: "system",
    data: null,
    archived: false,
    ...over,
  };
}

let historyPages: { items: Row[]; next_before: string | null }[] = [];
let fetchMock: ReturnType<typeof vi.fn>;

function jsonResponse(body: unknown) {
  return Promise.resolve({
    ok: true,
    status: 200,
    headers: new Headers({ "content-type": "application/json" }),
    json: () => Promise.resolve(body),
  });
}

/** Query params of every /api/notifications/history call, in order. */
function historyCalls(): URLSearchParams[] {
  return fetchMock.mock.calls
    .map(([input]) => String(input))
    .filter((u) => u.startsWith("/api/notifications/history") && !u.includes("/sources"))
    .map((u) => new URL(u, "http://x").searchParams);
}

async function settle() {
  await act(async () => {
    await vi.advanceTimersByTimeAsync(0);
  });
}

beforeEach(() => {
  vi.useFakeTimers();
  historyPages = [];
  fetchMock = vi.fn().mockImplementation((input: string) => {
    const url = String(input);
    if (url.startsWith("/api/notifications/history/sources")) {
      return jsonResponse({ sources: ["agents", "system", "updates"] });
    }
    if (url.startsWith("/api/notifications/history")) {
      return jsonResponse(historyPages.shift() ?? { items: [], next_before: null });
    }
    return jsonResponse([]);
  });
  vi.stubGlobal("fetch", fetchMock);
});

afterEach(() => {
  vi.useRealTimers();
  vi.unstubAllGlobals();
  // Reset the notification store
  useNotificationStore.setState({
    notifications: [],
    centreOpen: false,
  });
});

describe("NotificationHistoryApp", () => {
  it("sends q only after the 250ms search debounce", async () => {
    render(<NotificationHistoryApp windowId="w1" />);
    await settle();
    expect(historyCalls()).toHaveLength(1);
    expect(historyCalls()[0].get("q")).toBeNull();

    fireEvent.change(screen.getByLabelText("Search notification history"), {
      target: { value: "disk" },
    });
    await act(async () => {
      await vi.advanceTimersByTimeAsync(249);
    });
    expect(historyCalls().some((p) => p.get("q") === "disk")).toBe(false);

    await act(async () => {
      await vi.advanceTimersByTimeAsync(1);
    });
    const last = historyCalls().at(-1)!;
    expect(last.get("q")).toBe("disk");
  });

  it("sends source once per chosen source", async () => {
    render(<NotificationHistoryApp windowId="w1" />);
    await settle();

    fireEvent.click(screen.getByLabelText("Source agents"));
    await settle();
    fireEvent.click(screen.getByLabelText("Source updates"));
    await settle();

    const last = historyCalls().at(-1)!;
    expect(last.getAll("source")).toEqual(["agents", "updates"]);
  });

  it("Load more sends before=<next_before> and appends the next page", async () => {
    historyPages = [
      { items: [row(1), row(2)], next_before: "1699999998:2" },
      { items: [row(3)], next_before: null },
    ];
    render(<NotificationHistoryApp windowId="w1" />);
    await settle();
    expect(screen.getByText("Note 1")).toBeInTheDocument();
    expect(screen.getByText("Note 2")).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "Load more" }));
    await settle();

    const last = historyCalls().at(-1)!;
    expect(last.get("before")).toBe("1699999998:2");
    expect(screen.getByText("Note 1")).toBeInTheDocument();
    expect(screen.getByText("Note 2")).toBeInTheDocument();
    expect(screen.getByText("Note 3")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Load more" })).not.toBeInTheDocument();
  });

  it("State Archived sends state=archived", async () => {
    render(<NotificationHistoryApp windowId="w1" />);
    await settle();

    fireEvent.click(screen.getByRole("radio", { name: "Archived" }));
    await settle();

    expect(historyCalls().at(-1)!.get("state")).toBe("archived");
  });

  it("sends level once per chosen level", async () => {
    render(<NotificationHistoryApp windowId="w1" />);
    await settle();

    fireEvent.click(screen.getByLabelText("Level warning"));
    await settle();
    fireEvent.click(screen.getByLabelText("Level error"));
    await settle();

    expect(historyCalls().at(-1)!.getAll("level")).toEqual(["warning", "error"]);
  });

  it("shows an error state with Retry that refetches", async () => {
    fetchMock.mockImplementation((input: string) => {
      const url = String(input);
      if (url.startsWith("/api/notifications/history/sources")) return jsonResponse({ sources: [] });
      return Promise.resolve({ ok: false, status: 500, headers: new Headers(), json: () => Promise.resolve({}) });
    });
    render(<NotificationHistoryApp windowId="w1" />);
    await settle();
    expect(screen.getByRole("alert")).toHaveTextContent("HTTP 500");

    const before = historyCalls().length;
    fireEvent.click(screen.getByRole("button", { name: "Retry" }));
    await settle();
    expect(historyCalls().length).toBe(before + 1);
   });

   it("expands a row to its full message on click", async () => {
     historyPages = [{ items: [row(1, { message: "A long message body" })], next_before: null }];
     render(<NotificationHistoryApp windowId="w1" />);
     await settle();

     const btn = screen.getByRole("button", { name: /Note 1/ });
     expect(btn).toHaveAttribute("aria-expanded", "false");
     fireEvent.click(btn);
     expect(btn).toHaveAttribute("aria-expanded", "true");
   });

    it("shows locally dismissed notifications under On this device", async () => {
      // Seed the store with a client notification
      useNotificationStore.getState().addNotification({
        source: "system",
        title: "Local thing",
        body: "b",
        level: "info",
      });
      const state = useNotificationStore.getState();
      const id = state.notifications[0].id;
      // Dismiss it
      state.dismiss(id);

      // Mock server history to be empty
      historyPages = [{ items: [], next_before: null }];

      render(<NotificationHistoryApp windowId="w1" />);
      await settle();

      // Expect to see the local archived section and the notification
      const deviceList = screen.getByRole("list", { name: /notifications on this device/i });
      expect(deviceList).toBeInTheDocument();
      expect(deviceList).toHaveTextContent("Local thing");
    });

    it("searches local archived notifications ignoring trailing spaces", async () => {
      // Seed the store with a client notification
      useNotificationStore.getState().addNotification({
        source: "system",
        title: "Local thing",
        body: "b",
        level: "info",
      });
      const state = useNotificationStore.getState();
      const id = state.notifications[0].id;
      // Dismiss it
      state.dismiss(id);

      // Mock server history to be empty
      historyPages = [{ items: [], next_before: null }];

      render(<NotificationHistoryApp windowId="w1" />);
      await settle();

      // Set search input with trailing space
      fireEvent.change(screen.getByLabelText("Search notification history"), {
        target: { value: "local thing " },
      });
      // Wait for debounce
      await act(async () => {
        await vi.advanceTimersByTimeAsync(SEARCH_DEBOUNCE_MS + 100);
      });

      // Expect to see the local archived section and the notification
      const deviceList = screen.getByRole("list", { name: /notifications on this device/i });
      expect(deviceList).toBeInTheDocument();
      expect(deviceList).toHaveTextContent("Local thing");
    });

    it("does not show empty state when local archived notifications exist", async () => {
      // Seed the store with a client notification
      useNotificationStore.getState().addNotification({
        source: "system",
        title: "Local thing",
        body: "b",
        level: "info",
      });
      const state = useNotificationStore.getState();
      const id = state.notifications[0].id;
      // Dismiss it
      state.dismiss(id);

      // Mock server history to be empty
      historyPages = [{ items: [], next_before: null }];

      render(<NotificationHistoryApp windowId="w1" />);
      await settle();

      // Expect to see the local archived section and the notification
      const deviceList = screen.getByRole("list", { name: /notifications on this device/i });
      expect(deviceList).toBeInTheDocument();
      expect(deviceList).toHaveTextContent("Local thing");

      // Expect that the empty state text is NOT in the document
      expect(screen.queryByText(/No notifications yet/i)).not.toBeInTheDocument();
      // Instead, we should see the On this device heading
      expect(screen.getByText(/On this device/i)).toBeInTheDocument();
    });

    it("custom range ends at the next local midnight minus one second", async () => {
      const originalTZ = process.env.TZ;
      try {
        process.env.TZ = "America/New_York";
        const result = rangeBounds("custom", "2025-03-09", "2025-03-09");
        expect(result.since).toBe(1741496400);
        expect(result.until).toBe(1741579199);
        expect(result.until - result.since + 1).toBe(23 * 3600);
      } finally {
        process.env.TZ = originalTZ;
      }
    });
 });
