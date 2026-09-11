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

/**
 * The dialog reads the agent's current grants before it can be submitted (the
 * server treats the posted scope list as the complete set for the project),
 * so a test must wait for the submit button to come back before clicking it.
 */
async function waitForAssignEnabled() {
  const btn = screen.getByRole("button", { name: /assign to project/i });
  await waitFor(() => expect(btn).not.toBeDisabled());
  return btn;
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
    fireEvent.click(await waitForAssignEnabled());

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
    fireEvent.click(await waitForAssignEnabled());

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
    fireEvent.click(await waitForAssignEnabled());

    await waitFor(() => expect(screen.getByRole("alert")).toHaveTextContent(/not authorized/));
  });

  it("keeps scopes the agent already holds outside the preset list", async () => {
    // The server reads the posted `scopes` list as the agent's COMPLETE set for
    // the project, so an unlisted scope is REVOKED. files_read is not a preset;
    // it must be shown and re-sent, not dropped (taOS #2148).
    fetchMock.mockImplementation((url: string, init?: RequestInit) => {
      if (String(url).includes("/api/agents/registry/grants")) {
        return Promise.resolve(
          ok({
            grants: [
              { scope: "files_read", project_id: "prj_a", expires_at: null },
              { scope: "project_tasks", project_id: "prj_a", expires_at: null },
              { scope: "files_write", project_id: "prj_b", expires_at: null },
            ],
          }),
        );
      }
      if (/\/api\/projects\/[^/]+\/members\/assign-agent$/.test(String(url)) && init?.method === "POST") {
        assignUrl = String(url);
        assignBody = JSON.parse(String(init.body));
        return Promise.resolve(ok({ canonical_id: ENTRY.canonical_id }));
      }
      return Promise.resolve(ok({}));
    });

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

    await waitFor(() => expect(screen.getByLabelText("Scope files_read")).toBeInTheDocument());
    // A grant on ANOTHER project is not shown (nor sent).
    expect(screen.queryByLabelText("Scope files_write")).toBeNull();

    fireEvent.click(await waitForAssignEnabled());
    await waitFor(() => expect(assignBody).not.toBeNull());
    const posted = assignBody!.scopes as string[];
    expect(posted).toContain("files_read");
    expect(posted).toContain("project_tasks");
    expect(posted).not.toContain("files_write");
  });

  it("blocks assigning when the agent's current scopes cannot be read", async () => {
    // Unknown is not the same as "none": submitting on top of a failed read
    // would revoke scopes the operator never saw.
    fetchMock.mockImplementation((url: string) => {
      if (String(url).includes("/api/agents/registry/grants")) {
        return Promise.resolve({ ok: false, status: 403, json: async () => ({}) });
      }
      return Promise.resolve(ok({}));
    });

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

    await waitFor(() =>
      expect(screen.getByText(/Could not load this agent's current scopes/)).toBeInTheDocument(),
    );
    const btn = screen.getByRole("button", { name: /assign to project/i });
    expect(btn).toBeDisabled();
    fireEvent.click(btn);
    expect(assignBody).toBeNull();
  });
});
