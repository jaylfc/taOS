import { describe, it, expect, vi, beforeEach } from "vitest";
import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { AssignAgentToProjectDialog } from "../AssignAgentToProjectDialog";
import { projectsApi } from "@/lib/projects";

const ENTRY = {
  canonical_id: "agent:free-builder@taos",
  handle: "@free-builder",
  origin: "taos",
  status: "active" as const,
};

function ok(data: unknown, status = 200) {
  return { ok: true, status, json: async () => data };
}

describe("AssignAgentToProjectDialog", () => {
  let fetchMock: ReturnType<typeof vi.fn>;
  let assignBody: Record<string, unknown> | null = null;
  let assignUrl: string | null = null;

  beforeEach(() => {
    assignBody = null;
    assignUrl = null;
    fetchMock = vi.fn((url: string, init?: RequestInit) => {
      if (/\/api\/projects\/[^/]+\/members\/assign-agent$/.test(String(url)) && init?.method === "POST") {
        assignUrl = String(url);
        const parsed = JSON.parse(String(init.body));
        assignBody = parsed;
        return Promise.resolve(
          ok({
            member_id: "mem_1",
            project_id: parsed.project_id,
            canonical_id: parsed.canonical_id,
            scopes: parsed.scopes,
            is_lead: parsed.is_lead ?? 0,
          }),
        );
      }
      return Promise.resolve(ok({}));
    });
    vi.stubGlobal("fetch", fetchMock);
    vi.spyOn(projectsApi, "list").mockResolvedValue([
      { id: "prj_a", name: "Alpha", slug: "alpha", description: "", status: "active", created_by: "u", created_at: 0, updated_at: 0 },
      { id: "prj_b", name: "Beta", slug: "beta", description: "", status: "active", created_by: "u", created_at: 0, updated_at: 0 },
    ]);
  });

  it("loads and renders the project options", async () => {
    await act(async () => {
      render(
        <AssignAgentToProjectDialog
          entry={ENTRY}
          onClose={() => {}}
          onAssigned={() => {}}
        />,
      );
    });
    await waitFor(() => expect(screen.getByRole("option", { name: /Alpha/ })).toBeInTheDocument());
    expect(screen.getByRole("option", { name: /Beta/ })).toBeInTheDocument();
  });

  it("posts to /api/projects/{pid}/members/assign-agent with chosen scopes; project_tasks always present", async () => {
    await act(async () => {
      render(
        <AssignAgentToProjectDialog
          entry={ENTRY}
          onClose={() => {}}
          onAssigned={() => {}}
        />,
      );
    });
    fireEvent.change(screen.getByLabelText(/target project/i), { target: { value: "prj_a" } });
    fireEvent.click(screen.getByRole("button", { name: /assign to project/i }));

    await waitFor(() => expect(assignBody).not.toBeNull());
    expect(assignUrl).toBe("/api/projects/prj_a/members/assign-agent");
    expect(assignBody!.canonical_id).toBe(ENTRY.canonical_id);
    const scopes = assignBody!.scopes as string[];
    expect(scopes).toContain("project_tasks");
    expect(scopes).toContain("canvas_read");
    expect(scopes).toContain("canvas_write");
    // is_lead absent or false when toggle is off.
    expect(assignBody!.is_lead ?? false).toBeFalsy();
  });

  it("sends is_lead: true when the Lead toggle is on", async () => {
    await act(async () => {
      render(
        <AssignAgentToProjectDialog
          entry={ENTRY}
          onClose={() => {}}
          onAssigned={() => {}}
        />,
      );
    });
    fireEvent.change(screen.getByLabelText(/target project/i), { target: { value: "prj_b" } });
    fireEvent.click(screen.getByLabelText(/make this agent the project lead/i));
    fireEvent.click(screen.getByRole("button", { name: /assign to project/i }));

    await waitFor(() => expect(assignBody).not.toBeNull());
    expect(assignUrl).toBe("/api/projects/prj_b/members/assign-agent");
    expect(assignBody!.is_lead).toBe(true);
  });

  it("surfaces the error message on a failed POST", async () => {
    fetchMock.mockImplementation((url: string, init?: RequestInit) => {
      if (/\/api\/projects\/[^/]+\/members\/assign-agent$/.test(String(url)) && init?.method === "POST") {
        return Promise.resolve({ ok: false, status: 403, json: async () => ({ error: "not authorized" }) });
      }
      return Promise.resolve(ok({}));
    });
    vi.spyOn(projectsApi, "list").mockResolvedValue([
      { id: "prj_a", name: "Alpha", slug: "alpha", description: "", status: "active", created_by: "u", created_at: 0, updated_at: 0 },
    ]);

    await act(async () => {
      render(
        <AssignAgentToProjectDialog
          entry={ENTRY}
          onClose={() => {}}
          onAssigned={() => {}}
        />,
      );
    });
    fireEvent.change(screen.getByLabelText(/target project/i), { target: { value: "prj_a" } });
    fireEvent.click(screen.getByRole("button", { name: /assign to project/i }));

    await waitFor(() => expect(screen.getByRole("alert")).toHaveTextContent(/not authorized/));
  });
});
