import { describe, it, expect, vi, afterEach } from "vitest";
import { render, screen, cleanup } from "@testing-library/react";
import { CommunityView } from "../CommunityView";

// Mock the API module so the component doesn't make real HTTP calls.
vi.mock("@/lib/projects", () => ({
  projectsApi: {
    community: {
      snapshot: vi.fn(),
      stats: vi.fn(),
    },
  },
}));

import { projectsApi } from "@/lib/projects";

describe("CommunityView", () => {
  afterEach(() => {
    cleanup();
    vi.restoreAllMocks();
  });

  it("shows loading state initially", () => {
    vi.mocked(projectsApi.community.snapshot).mockReturnValue(new Promise(() => {}));
    render(<CommunityView projectId="test-pid" />);
    expect(screen.getByText(/loading community view/i)).toBeInTheDocument();
  });

  it("shows error state when snapshot fails", async () => {
    vi.mocked(projectsApi.community.snapshot).mockRejectedValue(new Error("Test error"));
    render(<CommunityView projectId="test-pid" />);
    expect(await screen.findByText(/failed to load community view/i, {}, { timeout: 5000 })).toBeInTheDocument();
  }, 10000);

  it("shows empty state when snapshot returns null data", async () => {
    vi.mocked(projectsApi.community.snapshot).mockResolvedValue(null as never);
    render(<CommunityView projectId="test-pid" />);
    expect(await screen.findByText(/no community data available/i, {}, { timeout: 5000 })).toBeInTheDocument();
  }, 10000);

  it("renders overview stats, leaderboard, board, and activity feed for a populated project", async () => {
    vi.mocked(projectsApi.community.snapshot).mockResolvedValue({
      project: {
        id: "pid-1",
        name: "Test",
        slug: "test",
        description: "desc",
        status: "active",
      },
      tasks: [
        { id: "t1", title: "Task 1", status: "open", priority: 0, labels: [], claimed_by: null, claimed_at: null, closed_at: null, created_at: 1, updated_at: 1 },
        { id: "t2", title: "Task 2", status: "claimed", priority: 1, labels: ["bug"], claimed_by: "agent-1", claimed_at: 2, closed_at: null, created_at: 1, updated_at: 2 },
      ],
      status_counts: { open: 1, claimed: 1 },
      contributors: [{ actor: "agent-1", claims: 1, closes: 0, total: 1 }],
      recent_activity: [{ id: "e1", task_id: "t2", event: "task.claimed", actor: "agent-1", ts: "2026-01-01T00:00:00Z" }],
    });

    render(<CommunityView projectId="test-pid" />);

    // Wait for the snapshot to load.
    expect(await screen.findByText("Overview", {}, { timeout: 5000 })).toBeInTheDocument();
    expect(screen.getByText("Leaderboard")).toBeInTheDocument();
    expect(screen.getByText("Board")).toBeInTheDocument();
    expect(screen.getByText("Recent Activity")).toBeInTheDocument();

    // Task titles should appear in the kanban.
    expect(screen.getByText("Task 1")).toBeInTheDocument();
    expect(screen.getByText("Task 2")).toBeInTheDocument();

    // Status badges should be rendered (text appears in both badge and column title).
    expect(screen.getAllByText("open").length).toBeGreaterThanOrEqual(2);
    expect(screen.getAllByText("claimed").length).toBeGreaterThanOrEqual(2);

    // Leaderboard contributor (appears in multiple places: table, kanban, feed).
    expect(screen.getAllByText("agent-1").length).toBeGreaterThanOrEqual(3);
  }, 10000);
});
