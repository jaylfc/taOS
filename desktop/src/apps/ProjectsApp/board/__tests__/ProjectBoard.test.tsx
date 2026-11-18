import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, fireEvent, waitFor, act } from "@testing-library/react";
import { ProjectBoard } from "../ProjectBoard";
import { projectsApi } from "../../../../lib/projects";

const openedUrls: string[] = [];
const closedUrls: string[] = [];
let latestSource: { url: string; close: () => void; onmessage: ((e: MessageEvent) => void) | null } | null = null;
const MockEventSourceCtor = vi.fn();
Object.assign(MockEventSourceCtor, { CONNECTING: 0, OPEN: 1, CLOSED: 2 });

beforeEach(() => {
  vi.restoreAllMocks();
  vi.spyOn(projectsApi.tasks, "list").mockResolvedValue([]);
  vi.spyOn(projectsApi.elements, "list").mockResolvedValue([]);
  const realSubscribe = projectsApi.subscribeEvents;
  vi.spyOn(projectsApi, "subscribeEvents").mockImplementation(
    (pid: string, cb: (e: unknown) => void, opts?: { onOpen?: () => void; onError?: () => void }) =>
      realSubscribe(pid, cb, opts),
  );
  MockEventSourceCtor.mockImplementation(function (this: any, url: string) {
    this.url = url;
    this.onopen = null;
    this.onmessage = null;
    this.onerror = null;
    const close = vi.fn(() => {
      closedUrls.push(url);
      this.readyState = 2;
    });
    this.close = close;
    this.readyState = 0;
    openedUrls.push(url);
    latestSource = this;
  });
  vi.stubGlobal("EventSource", MockEventSourceCtor);
  openedUrls.length = 0;
  closedUrls.length = 0;
  latestSource = null;
});

describe("ProjectBoard", () => {
  it("renders three columns (Ready / Claimed / Closed) in Kanban mode", async () => {
    render(<ProjectBoard projectId="p1" currentUserId="u1" />);
    fireEvent.click(await screen.findByRole("tab", { name: /Kanban/ }));
    await waitFor(() => {
      expect(screen.getByRole("region", { name: /Ready/ })).toBeInTheDocument();
      expect(screen.getByRole("region", { name: /Claimed/ })).toBeInTheDocument();
      expect(screen.getByRole("region", { name: /Closed/ })).toBeInTheDocument();
    });
  });

  it("toggles between Lanes and Kanban modes", async () => {
    render(<ProjectBoard projectId="p1" currentUserId="u1" />);
    fireEvent.click(await screen.findByRole("tab", { name: /Kanban/ }));
    expect(screen.getByRole("tab", { name: /Kanban/ })).toHaveAttribute("aria-selected", "true");
  });

  it("renders a Quarantined column with quarantined tasks in Kanban mode", async () => {
    vi.spyOn(projectsApi.tasks, "list").mockImplementation((_pid: string, status?: string) => {
      if (status === "quarantined") return Promise.resolve([{ id: "tq1", title: "Q task", status: "quarantined", labels: [], priority: 1, strike_count: 2, latest_strike: { step: "bad" } } as any]);
      return Promise.resolve([]);
    });
    render(<ProjectBoard projectId="p1" currentUserId="u1" isLead />);
    fireEvent.click(await screen.findByRole("tab", { name: /Kanban/ }));
    await waitFor(() => {
      expect(screen.getByRole("region", { name: /Quarantined/ })).toBeInTheDocument();
      expect(screen.getByText("Q task")).toBeInTheDocument();
    });
  });

  it("does not open the move dialog for a parked card", async () => {
    vi.spyOn(projectsApi.tasks, "list").mockImplementation((_pid: string, status?: string) => {
      if (status === "parked") return Promise.resolve([{ id: "tp1", title: "Parked task", status: "parked", labels: [], priority: 1 } as any]);
      return Promise.resolve([]);
    });
    render(<ProjectBoard projectId="p1" currentUserId="u1" isLead />);
    fireEvent.click(await screen.findByRole("tab", { name: /Kanban/ }));
    const card = await screen.findByLabelText("Parked task");
    fireEvent.keyDown(card, { key: "m" });
    // Parked is terminal: the card must offer no move affordance at all, so
    // the dialog that would dispatch claim/release/close never opens.
    expect(screen.queryByRole("dialog", { name: /Move task/ })).toBeNull();
  });

  it("shows unquarantine button on quarantined cards for leads", async () => {
    vi.spyOn(projectsApi.tasks, "list").mockImplementation((_pid: string, status?: string) => {
      if (status === "quarantined") return Promise.resolve([{ id: "tq1", title: "Q task", status: "quarantined", labels: [], priority: 1, strike_count: 1, latest_strike: { step: "boom" } } as any]);
      return Promise.resolve([]);
    });
    render(<ProjectBoard projectId="p1" currentUserId="u1" isLead />);
    fireEvent.click(await screen.findByRole("tab", { name: /Kanban/ }));
    await waitFor(() => {
      expect(screen.getByRole("button", { name: /Unquarantine task tq1/i })).toBeInTheDocument();
    });
  });

  describe("live updates", () => {
    it("subscribes to the per-project stream instead of the OS stream", () => {
      render(<ProjectBoard projectId="p1" currentUserId="u1" />);
      expect(openedUrls).toContain("/api/projects/p1/events");
      expect(openedUrls.some((url) => url.includes("/api/os/events"))).toBe(false);
    });

    it("applies a task.updated frame to the card", async () => {
      vi.spyOn(projectsApi.tasks, "list").mockResolvedValueOnce([
        { id: "t1", title: "Original", status: "open", labels: [], priority: 0 } as any,
      ]);
      render(<ProjectBoard projectId="p1" currentUserId="u1" />);
      fireEvent.click(await screen.findByRole("tab", { name: /Kanban/ }));
      await waitFor(() => expect(screen.getByText("Original")).toBeInTheDocument());
      expect(latestSource).not.toBeNull();
      act(() => {
        latestSource!.onmessage!({
          data: JSON.stringify({
            kind: "task.updated",
            payload: { id: "t1", patch: { title: "renamed" } },
            ts: 1,
          }),
        } as MessageEvent);
      });
      expect(screen.getByText("renamed")).toBeInTheDocument();
      expect(screen.queryByText("Original")).not.toBeInTheDocument();
    });

    it("closes the old project's stream and opens the new project's stream when the project changes", () => {
      const { rerender } = render(<ProjectBoard projectId="p1" currentUserId="u1" />);
      const firstUrl = openedUrls.find((url) => url.startsWith("/api/projects/p1/events"))!;
      rerender(<ProjectBoard projectId="q1" currentUserId="u1" />);
      expect(closedUrls).toContain(firstUrl);
      expect(openedUrls).toContain("/api/projects/q1/events");
      expect(openedUrls.some((url) => url.includes("/api/os/events"))).toBe(false);
    });
  });
});
