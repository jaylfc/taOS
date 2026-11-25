import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, fireEvent, waitFor, within } from "@testing-library/react";
import { ProjectMembers } from "../ProjectMembers";
import type { Project } from "@/lib/projects";

// #2067: pending invites surface on the Members screen — before this they
// were only visible inside the mint dialog, so a minted invite could not
// be found again without reopening it.

const project = { id: "prj-test", name: "taOS", slug: "taos" } as unknown as Project;

const nowSecs = () => Math.floor(Date.now() / 1000);

function inviteRow(overrides: Record<string, unknown> = {}) {
  return {
    invite_id: "123456",
    scopes: ["project_tasks", "canvas_read"],
    status: "pending",
    kind: "agent",
    created_ts: nowSecs() - 600,
    expires_ts: nowSecs() + 3000,
    redeemed_by: null,
    pin_required: true,
    display_name: null,
    contact_id: null,
    ...overrides,
  };
}

describe("ProjectMembers pending invites (#2067)", () => {
  let fetchMock: ReturnType<typeof vi.fn>;
  let invites: unknown[];
  let deleted: string[];

  beforeEach(() => {
    invites = [];
    deleted = [];
    fetchMock = vi.fn((url: string, init?: RequestInit) => {
      if (url === "/api/projects/prj-test/members") {
        return Promise.resolve({ ok: true, status: 200, json: async () => ({ items: [] }) });
      }
      if (url === "/api/projects/prj-test/invites" && init?.method === "DELETE") {
        return Promise.resolve({ ok: true, status: 204, json: async () => null });
      }
      if (url.startsWith("/api/projects/prj-test/invites/") && init?.method === "DELETE") {
        deleted.push(url);
        const id = url.split("/").pop()!;
        invites = invites.filter((i) => (i as { invite_id: string }).invite_id !== id);
        return Promise.resolve({ ok: true, status: 204, json: async () => null });
      }
      if (url === "/api/projects/prj-test/invites") {
        return Promise.resolve({ ok: true, status: 200, json: async () => invites });
      }
      return Promise.resolve({ ok: true, status: 200, json: async () => [] });
    });
    vi.stubGlobal("fetch", fetchMock);
  });

  it("renders a pending invite with kind chip, scope chips and PIN badge", async () => {
    invites = [inviteRow()];
    render(<ProjectMembers project={project} onChanged={vi.fn()} />);

    // Query the section by its region role: the shared list inside it
    // carries the same "Pending invites" aria-label, so a label query
    // matches both elements and is ambiguous.
    const section = await screen.findByRole("region", { name: "Pending invites" });
    expect(within(section).getByText("123456")).toBeInTheDocument();
    expect(within(section).getByText("agent")).toBeInTheDocument();
    expect(within(section).getByText("project_tasks")).toBeInTheDocument();
    expect(within(section).getByText("canvas_read")).toBeInTheDocument();
    expect(within(section).getByText(/PIN required/i)).toBeInTheDocument();
  });

  it("shows collab kind and hides non-pending invites", async () => {
    invites = [
      inviteRow({ invite_id: "222222", kind: "collab", scopes: [], contact_id: "hub:abcdef", pin_required: false }),
      inviteRow({ invite_id: "333333", status: "redeemed", redeemed_by: "agent-x" }),
    ];
    render(<ProjectMembers project={project} onChanged={vi.fn()} />);

    const section = await screen.findByRole("region", { name: "Pending invites" });
    expect(within(section).getByText("222222")).toBeInTheDocument();
    expect(within(section).getByText("collab")).toBeInTheDocument();
    expect(within(section).getByText("hub:abcdef")).toBeInTheDocument();
    expect(within(section).queryByText(/PIN required/i)).not.toBeInTheDocument();
    expect(within(section).queryByText("333333")).not.toBeInTheDocument();
  });

  it("shows the empty state when there are no pending invites", async () => {
    invites = [];
    render(<ProjectMembers project={project} onChanged={vi.fn()} />);

    const section = await screen.findByRole("region", { name: "Pending invites" });
    expect(within(section).getByText("No pending invites")).toBeInTheDocument();
  });

  it("cancel calls the per-project DELETE and the row disappears", async () => {
    invites = [inviteRow()];
    render(<ProjectMembers project={project} onChanged={vi.fn()} />);

    const section = await screen.findByRole("region", { name: "Pending invites" });
    fireEvent.click(within(section).getByRole("button", { name: /cancel invite 123456/i }));

    await waitFor(() => {
      expect(deleted).toContain("/api/projects/prj-test/invites/123456");
    });
    await waitFor(() => {
      expect(within(section).queryByText("123456")).not.toBeInTheDocument();
    });
    expect(within(section).getByText("No pending invites")).toBeInTheDocument();
  });
});
