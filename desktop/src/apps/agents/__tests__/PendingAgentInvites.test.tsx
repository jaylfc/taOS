import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, fireEvent, waitFor, within } from "@testing-library/react";
import { PendingAgentInvites } from "../PendingAgentInvites";
import { useProcessStore } from "@/stores/process-store";

// #2067: the Agents app registry area shows pending external-agent invites
// from the cross-project aggregate, with cancel + a link to the owning
// project. Non-admins (403 from the admin-gated aggregate) see nothing.

const nowSecs = () => Math.floor(Date.now() / 1000);

function aggregateRow(overrides: Record<string, unknown> = {}) {
  return {
    invite_id: "654321",
    scopes: ["a2a_send", "a2a_receive"],
    status: "pending",
    kind: "agent",
    created_ts: nowSecs() - 300,
    expires_ts: nowSecs() + 3300,
    pin_required: true,
    display_name: "Scout",
    project_id: "prj_a",
    project_name: "Alpha",
    ...overrides,
  };
}

describe("PendingAgentInvites (#2067)", () => {
  let fetchMock: ReturnType<typeof vi.fn>;
  let rows: unknown[];
  let mineRows: unknown[];
  let deleted: string[];
  let aggregateStatus: number;
  let mineStatus: number;
  const openWindow = vi.fn(() => "win-1");

  beforeEach(() => {
    rows = [];
    mineRows = [];
    deleted = [];
    aggregateStatus = 200;
    mineStatus = 200;
    openWindow.mockClear();
    useProcessStore.setState({ openWindow });
    fetchMock = vi.fn((url: string, init?: RequestInit) => {
      if (url === "/api/agents/invites/pending") {
        return Promise.resolve({
          ok: aggregateStatus === 200,
          status: aggregateStatus,
          json: async () => rows,
        });
      }
      if (url === "/api/agents/invites/pending/mine") {
        return Promise.resolve({
          ok: mineStatus === 200,
          status: mineStatus,
          json: async () => mineRows,
        });
      }
      if (init?.method === "DELETE") {
        deleted.push(url);
        const id = url.split("/").pop()!;
        rows = rows.filter((r) => (r as { invite_id: string }).invite_id !== id);
        return Promise.resolve({ ok: true, status: 204, json: async () => null });
      }
      return Promise.resolve({ ok: true, status: 200, json: async () => [] });
    });
    vi.stubGlobal("fetch", fetchMock);
  });

  it("renders project and OS-level invites from the aggregate", async () => {
    rows = [
      aggregateRow(),
      aggregateRow({ invite_id: "111111", display_name: null, project_id: null, project_name: null }),
    ];
    render(<PendingAgentInvites active refreshKey={0} isAdmin={true} />);

    // Query the section by its region role: the shared list inside it
    // carries the same "Pending invites" aria-label, so a label query
    // matches both elements and is ambiguous.
    const section = await screen.findByRole("region", { name: "Pending invites" });
    expect(within(section).getByText("654321")).toBeInTheDocument();
    expect(within(section).getByText("Scout")).toBeInTheDocument();
    expect(within(section).getByText("111111")).toBeInTheDocument();
    expect(within(section).getByText("OS-level")).toBeInTheDocument();
    expect(within(section).getByRole("button", { name: /open project Alpha/i })).toBeInTheDocument();
  });

  it("opens the owning project on its Members tab when the project link is clicked", async () => {
    rows = [aggregateRow()];
    render(<PendingAgentInvites active refreshKey={0} isAdmin={true} />);

    const section = await screen.findByRole("region", { name: "Pending invites" });
    fireEvent.click(within(section).getByRole("button", { name: /open project Alpha/i }));
    expect(openWindow).toHaveBeenCalledWith(
      "projects",
      expect.anything(),
      { projectId: "prj_a", tab: "members" },
    );
  });

  it("cancel routes project invites to the per-project DELETE and OS invites to the OS DELETE", async () => {
    rows = [
      aggregateRow(),
      aggregateRow({ invite_id: "111111", project_id: null, project_name: null }),
    ];
    render(<PendingAgentInvites active refreshKey={0} isAdmin={true} />);

    const section = await screen.findByRole("region", { name: "Pending invites" });
    fireEvent.click(within(section).getByRole("button", { name: /cancel invite 654321/i }));
    await waitFor(() => {
      expect(deleted).toContain("/api/projects/prj_a/invites/654321");
    });
    fireEvent.click(within(section).getByRole("button", { name: /cancel invite 111111/i }));
    await waitFor(() => {
      expect(deleted).toContain("/api/agents/invites/111111");
    });
  });

  it("calls the per-owner endpoint, not the aggregate, for a non-admin owner", async () => {
    mineRows = [aggregateRow()];
    const { container } = render(
      <PendingAgentInvites active refreshKey={0} isAdmin={false} />,
    );
    await waitFor(() => {
      expect(fetchMock).toHaveBeenCalledWith(
        "/api/agents/invites/pending/mine",
        expect.anything(),
      );
    });
    expect(fetchMock).not.toHaveBeenCalledWith(
      "/api/agents/invites/pending",
      expect.anything(),
    );
    expect(container.querySelector("section")).not.toBeNull();
  });

  it("renders the member's own pending invites from the per-owner endpoint (non-admin)", async () => {
    mineRows = [aggregateRow()];
    render(<PendingAgentInvites active refreshKey={0} isAdmin={false} />);

    const section = await screen.findByRole("region", { name: "Pending invites" });
    expect(within(section).getByText("654321")).toBeInTheDocument();
    expect(within(section).getByText("Scout")).toBeInTheDocument();
  });

  it("renders only the invites returned by the per-owner endpoint (non-admin)", async () => {
    // The endpoint returns only the caller's own invite; the component must
    // not render an invite the endpoint did not return.
    mineRows = [aggregateRow({ invite_id: "654321", display_name: "Scout" })];
    render(<PendingAgentInvites active refreshKey={0} isAdmin={false} />);

    const section = await screen.findByRole("region", { name: "Pending invites" });
    expect(within(section).getByText("654321")).toBeInTheDocument();
    expect(within(section).getByText("Scout")).toBeInTheDocument();
    expect(screen.queryByText("999999")).not.toBeInTheDocument();
  });

  it("shows the empty state for a non-admin owner with no pending invites", async () => {
    mineRows = [];
    render(<PendingAgentInvites active refreshKey={0} isAdmin={false} />);
    const section = await screen.findByRole("region", { name: "Pending invites" });
    expect(within(section).getByText("No pending invites")).toBeInTheDocument();
  });

  it("renders nothing when the per-owner endpoint errors (non-admin)", async () => {
    mineStatus = 403;
    const { container } = render(
      <PendingAgentInvites active refreshKey={0} isAdmin={false} />,
    );
    await waitFor(() => {
      expect(fetchMock).toHaveBeenCalledWith(
        "/api/agents/invites/pending/mine",
        expect.anything(),
      );
    });
    expect(container.querySelector("section")).toBeNull();
  });

  it("shows the empty state for an admin with no pending invites", async () => {
    rows = [];
    render(<PendingAgentInvites active refreshKey={0} isAdmin={true} />);
    const section = await screen.findByRole("region", { name: "Pending invites" });
    expect(within(section).getByText("No pending invites")).toBeInTheDocument();
  });
});
