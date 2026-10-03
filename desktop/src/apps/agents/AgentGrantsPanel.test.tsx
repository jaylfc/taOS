import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { render, screen, fireEvent, waitFor, act } from "@testing-library/react";
import { AgentGrantsPanel, type AgentGrantsTarget } from "./AgentGrantsPanel";
import {
  listAgentGrants,
  revokeAgentProjectGrants,
  type AgentGrant,
} from "@/lib/agent-grants";
import { projectsApi, type Project } from "@/lib/projects";

vi.mock("@/lib/agent-grants", () => ({
  listAgentGrants: vi.fn(),
  revokeAgentProjectGrants: vi.fn(),
}));

vi.mock("@/lib/projects", () => ({
  projectsApi: { list: vi.fn() },
}));

const TARGET: AgentGrantsTarget = {
  canonical_id: "agent-canon-1",
  handle: "builder",
  display_name: "Builder Agent",
};

function makeGrant(overrides: Partial<AgentGrant> = {}): AgentGrant {
  return {
    canonical_id: TARGET.canonical_id,
    scope: "project_tasks",
    tier: "once",
    project_id: "proj-1",
    granted_at: new Date(Date.now() - 60_000).toISOString(),
    expires_at: null,
    ...overrides,
  };
}

function okRevoke(projectId: string, revoked: string[]) {
  return Promise.resolve({
    canonical_id: TARGET.canonical_id,
    project_id: projectId,
    revoked_scopes: revoked,
    active_scopes: [],
  });
}

beforeEach(() => {
  vi.mocked(listAgentGrants).mockResolvedValue([]);
  vi.mocked(revokeAgentProjectGrants).mockResolvedValue({
    canonical_id: TARGET.canonical_id,
    project_id: "proj-1",
    revoked_scopes: [],
    active_scopes: [],
  });
  vi.mocked(projectsApi.list).mockResolvedValue([]);
});

afterEach(() => {
  vi.clearAllMocks();
});

describe("AgentGrantsPanel", () => {
  it("loads and groups an agent's active grants by project", async () => {
    vi.mocked(listAgentGrants).mockResolvedValue([
      makeGrant({ scope: "project_tasks", project_id: "proj-1" }),
      makeGrant({ scope: "canvas_read", project_id: "proj-1" }),
      makeGrant({ scope: "memory_read", project_id: "proj-2" }),
    ]);

    render(<AgentGrantsPanel target={TARGET} onClose={vi.fn()} />);
    await waitFor(() => screen.getByText("project_tasks"));

    expect(screen.getByText("canvas_read")).toBeTruthy();
    expect(screen.getByText("memory_read")).toBeTruthy();
  });

  it("revoking a single scope calls the revoke-agent route with just that scope, then refreshes", async () => {
    vi.mocked(listAgentGrants)
      .mockResolvedValueOnce([makeGrant({ scope: "project_tasks", project_id: "proj-1" })])
      .mockResolvedValueOnce([]);
    vi.mocked(revokeAgentProjectGrants).mockImplementation((pid, cid, scopes) =>
      Promise.resolve(okRevoke(pid, scopes as string[])),
    );

    render(<AgentGrantsPanel target={TARGET} onClose={vi.fn()} />);
    const revokeBtn = await screen.findByRole("button", { name: /revoke project_tasks/i });

    await act(async () => {
      fireEvent.click(revokeBtn);
    });

    expect(revokeAgentProjectGrants).toHaveBeenCalledWith(
      "proj-1",
      TARGET.canonical_id,
      ["project_tasks"],
    );
    // listAgentGrants is re-called to refresh; the empty result renders the empty state.
    await waitFor(() => screen.getByText(/no active grants/i));
  });

  it("revoking all on a project posts an empty scopes list", async () => {
    vi.mocked(listAgentGrants).mockResolvedValue([
      makeGrant({ scope: "project_tasks", project_id: "proj-1" }),
      makeGrant({ scope: "canvas_read", project_id: "proj-1" }),
    ]);
    vi.mocked(revokeAgentProjectGrants).mockImplementation((pid, cid, scopes) =>
      Promise.resolve(okRevoke(pid, scopes as string[])),
    );

    render(<AgentGrantsPanel target={TARGET} onClose={vi.fn()} />);
    const revokeAll = await screen.findByRole("button", { name: /revoke all on proj-1/i });

    await act(async () => {
      fireEvent.click(revokeAll);
    });

    expect(revokeAgentProjectGrants).toHaveBeenCalledWith("proj-1", TARGET.canonical_id, []);
  });

  it("excludes expired grants from the active list", async () => {
    vi.mocked(listAgentGrants).mockResolvedValue([
      makeGrant({ scope: "active_scope", project_id: "proj-1" }),
      makeGrant({
        scope: "expired_scope",
        project_id: "proj-1",
        expires_at: new Date(Date.now() - 1000).toISOString(),
      }),
    ]);

    render(<AgentGrantsPanel target={TARGET} onClose={vi.fn()} />);
    await waitFor(() => screen.getByText("active_scope"));

    expect(screen.queryByText("expired_scope")).toBeNull();
  });

  it("shows the empty state when the agent has no active grants", async () => {
    vi.mocked(listAgentGrants).mockResolvedValue([]);

    render(<AgentGrantsPanel target={TARGET} onClose={vi.fn()} />);
    await waitFor(() => screen.getByText(/no active grants/i));
  });

  it("does not offer a revoke action for global (non project-bound) grants", async () => {
    vi.mocked(listAgentGrants).mockResolvedValue([
      makeGrant({ scope: "a2a_send", project_id: null }),
    ]);

    render(<AgentGrantsPanel target={TARGET} onClose={vi.fn()} />);
    await waitFor(() => screen.getByText("a2a_send"));

    expect(screen.queryByRole("button", { name: /revoke a2a_send/i })).toBeNull();
  });

  it("uses no em dashes in the panel's user-facing text or labels", async () => {
    vi.mocked(listAgentGrants).mockResolvedValue([
      makeGrant({ scope: "project_tasks", project_id: "proj-1" }),
      makeGrant({ scope: "a2a_send", project_id: null }),
    ]);

    render(<AgentGrantsPanel target={TARGET} onClose={vi.fn()} />);
    await waitFor(() => screen.getByText("project_tasks"));
    await waitFor(() => screen.getByText("a2a_send"));

    // The panel portals into document.body, so read the live DOM. Scope the
    // sweep to the dialog itself: visible text plus every attribute, since
    // aria-label/title strings are read by assistive tech and tooltips.
    const dialogs = Array.from(document.querySelectorAll('[role="dialog"]'));
    expect(dialogs.length).toBeGreaterThan(0);
    const nodes = dialogs.flatMap((d) => [d, ...Array.from(d.querySelectorAll("*"))]);
    const haystack = nodes.flatMap((el) => [
      el.textContent ?? "",
      ...Array.from(el.attributes).map((a) => a.value),
    ]);

    const emDash = "\u2014";
    expect(haystack.filter((s) => s.includes(emDash))).toEqual([]);
  });

  it("moves focus into the dialog on open and restores it to the opener on close", async () => {
    const opener = document.createElement("button");
    opener.textContent = "Manage grants";
    document.body.appendChild(opener);
    opener.focus();
    expect(document.activeElement).toBe(opener);

    const { unmount } = render(<AgentGrantsPanel target={TARGET} onClose={vi.fn()} />);
    await waitFor(() => screen.getByText(/no active grants/i));

    const dialog = document.querySelector('[role="dialog"]');
    expect(dialog).not.toBeNull();
    expect(dialog!.contains(document.activeElement)).toBe(true);

    unmount();
    expect(document.activeElement).toBe(opener);
    opener.remove();
  });

  it("keeps Tab inside the dialog by wrapping from the last control to the first", async () => {
    vi.mocked(listAgentGrants).mockResolvedValue([
      makeGrant({ scope: "project_tasks", project_id: "proj-1" }),
    ]);

    render(<AgentGrantsPanel target={TARGET} onClose={vi.fn()} />);
    await waitFor(() => screen.getByText("project_tasks"));

    const dialog = document.querySelector('[role="dialog"]') as HTMLElement;
    const focusable = Array.from(
      dialog.querySelectorAll<HTMLElement>(
        'a[href], button:not([disabled]), input:not([disabled]), select:not([disabled]), textarea:not([disabled]), [tabindex]:not([tabindex="-1"])',
      ),
    );
    expect(focusable.length).toBeGreaterThan(1);
    const first = focusable[0]!;
    const last = focusable[focusable.length - 1]!;

    last.focus();
    await act(async () => {
      dialog.dispatchEvent(new KeyboardEvent("keydown", { key: "Tab", bubbles: true }));
    });

    expect(document.activeElement).toBe(first);
  });

  it("labels a grant on an archived project from the archived page, not its raw id", async () => {
    vi.mocked(listAgentGrants).mockResolvedValue([
      makeGrant({ scope: "project_tasks", project_id: "proj-archived" }),
    ]);
    // `list()` defaults to active projects only, so the archived project has to
    // come from an explicit status page; without it the raw id stands in.
    vi.mocked(projectsApi.list).mockImplementation((status?: string) =>
      Promise.resolve(
        status === "archived"
          ? [
              {
                id: "proj-archived",
                name: "Legacy Project",
                slug: "legacy",
                status: "archived",
              } as Project,
            ]
          : [],
      ),
    );

    render(<AgentGrantsPanel target={TARGET} onClose={vi.fn()} />);
    await waitFor(() => screen.getByText("project_tasks"));

    expect(screen.getByText("Legacy Project")).toBeTruthy();
    expect(screen.queryByText("proj-archived")).toBeNull();
    expect(vi.mocked(projectsApi.list)).toHaveBeenCalledWith("archived");
    // The label is cosmetic; revoke still addresses the project by id.
    expect(
      screen.getByRole("button", { name: /revoke all on Legacy Project/i }),
    ).toBeTruthy();
  });

  it("still renders grants when the project list fails (labels are cosmetic)", async () => {
    vi.mocked(listAgentGrants).mockResolvedValue([
      makeGrant({ scope: "project_tasks", project_id: "proj-1" }),
    ]);
    vi.mocked(projectsApi.list).mockRejectedValue(new Error("HTTP 500"));

    render(<AgentGrantsPanel target={TARGET} onClose={vi.fn()} />);

    await waitFor(() => screen.getByText("project_tasks"));
    // The raw project id stands in for the missing label, and revoke still works.
    expect(screen.getByText("proj-1")).toBeTruthy();
    expect(screen.getByRole("button", { name: /revoke project_tasks/i })).toBeTruthy();
    expect(screen.queryByText(/failed to load grants/i)).toBeNull();
  });
});