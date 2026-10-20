import { render, screen, act, fireEvent, within } from "@testing-library/react";
import { describe, it, expect, vi, beforeEach } from "vitest";
import { ProjectMembers } from "./ProjectMembers";
import type { Project } from "@/lib/projects";

function mockFetch(
  responses: Record<string, { ok: boolean; status?: number; body: unknown }>,
) {
  return vi.fn().mockImplementation((input: string) => {
    const hit = responses[input] ?? responses["*"];
    if (!hit) throw new Error(`Unmocked fetch: ${input}`);
    return Promise.resolve({
      ok: hit.ok,
      status: hit.status ?? (hit.ok ? 200 : 500),
      json: () => Promise.resolve(hit.body),
      text: () => Promise.resolve(""),
    });
  });
}

async function flush() {
  await act(async () => {
    await new Promise((r) => setTimeout(r, 0));
  });
}

const baseProject = { id: "prj-test", name: "taOS", slug: "taos" } as unknown as Project;

const agentA = { id: "agent-a", name: "Alpha", display_name: "Alpha", emoji: "🅰️" };
const agentB = { id: "agent-b", name: "Beta", display_name: "Beta", emoji: "🅱️" };

function memberRow(memberId: string) {
  return {
    project_id: "prj-test",
    member_id: memberId,
    member_kind: "native",
    role: "member",
    is_lead: 0,
    can_edit_canvas: 0,
    can_read_canvas: 0,
  };
}

describe("ProjectMembers external-agent categorisation", () => {
  beforeEach(() => {
    vi.restoreAllMocks();
  });

  it("shows a consent-flow agent (empty handle, member keyed by canonical id) under External / Connected agents", async () => {
    vi.stubGlobal(
      "fetch",
      mockFetch({
        "/api/projects/prj-test/members": { ok: true, body: { items: [externalMember] } },
        "/api/agents": { ok: true, body: [] },
        "/api/agents/registry": { ok: true, body: [registryEntry] },
      }),
    );

    render(<ProjectMembers project={project} onChanged={vi.fn()} />);
    await flush();

    // The section heading only renders when at least one member is classified
    // external, so its presence is the regression guard: before the canonical-id
    // match this agent fell into the plain Members list.
    const externalSection = screen.getByText("External / Connected agents").closest("section")!;
    expect(externalSection).toBeInTheDocument();
    expect(within(externalSection!).getByText("grok-taOS")).toBeInTheDocument();
    // The framework badge resolves via the canonical-id keyed lookup, and "grok"
    // (not only "grok-build") maps to the friendly Grok label.
    expect(within(externalSection!).getByText("Grok")).toBeInTheDocument();
  });
});

describe("ProjectMembers canvas capability checkboxes (slice 6)", () => {
  beforeEach(() => {
    vi.restoreAllMocks();
  });

  it("renders both a read and an edit canvas checkbox for an agent member", async () => {
    vi.stubGlobal(
      "fetch",
      mockFetch({
        "/api/projects/prj-test/members": {
          ok: true,
          body: { items: [memberRow("agent-a")] },
        },
        "/api/agents": { ok: true, body: [agentA] },
        "/api/agents/registry": { ok: true, body: [] },
      }),
    );

    render(<ProjectMembers project={baseProject} onChanged={vi.fn()} />);
    await flush();

    expect(screen.getByText("Can read canvas")).toBeInTheDocument();
    expect(screen.getByText("Can edit canvas")).toBeInTheDocument();
    expect(
      screen.getByLabelText("Can read canvas for Alpha"),
    ).toBeInTheDocument();
    expect(
      screen.getByLabelText("Can edit canvas for Alpha"),
    ).toBeInTheDocument();
  });
});

describe("ProjectMembers exclusive Lead selector (D7)", () => {
  beforeEach(() => {
    vi.restoreAllMocks();
  });

  it("reflects the current lead and promotes a new one exclusively via setLead", async () => {
    const fetchMock = mockFetch({
      "/api/projects/prj-test/members": {
        ok: true,
        body: { items: [memberRow("agent-a"), memberRow("agent-b")] },
      },
      "/api/agents": { ok: true, body: [agentA, agentB] },
      "/api/agents/registry": { ok: true, body: [] },
      "/api/projects/prj-test/lead": { ok: true, body: { ok: true, lead_member_id: "agent-b" } },
    });
    vi.stubGlobal("fetch", fetchMock);

    const project = {
      ...baseProject,
      lead_member_id: "agent-a",
    } as unknown as Project;

    render(<ProjectMembers project={project} onChanged={vi.fn()} />);
    await flush();

    const select = screen.getByLabelText("Project lead") as HTMLSelectElement;
    // The project's exclusive lead is pre-selected.
    expect(select.value).toBe("agent-a");

    // Promote Beta. Because the lead is a single project pointer, only one
    // option can ever be selected, so setting a new lead is inherently
    // exclusive (the server unsets the previous one).
    await act(async () => {
      fireEvent.change(select, { target: { value: "agent-b" } });
      await flush();
    });

    expect(fetchMock).toHaveBeenCalledWith(
      "/api/projects/prj-test/lead",
      expect.objectContaining({
        method: "PATCH",
        body: JSON.stringify({ member_id: "agent-b" }),
      }),
    );
    expect((screen.getByLabelText("Project lead") as HTMLSelectElement).value).toBe("agent-b");
  });

  it("offers a 'No lead' option that clears the lead via setLead(null)", async () => {
    const fetchMock = mockFetch({
      "/api/projects/prj-test/members": {
        ok: true,
        body: { items: [memberRow("agent-a")] },
      },
      "/api/agents": { ok: true, body: [agentA] },
      "/api/agents/registry": { ok: true, body: [] },
      "/api/projects/prj-test/lead": { ok: true, body: { ok: true, lead_member_id: null } },
    });
    vi.stubGlobal("fetch", fetchMock);

    const project = {
      ...baseProject,
      lead_member_id: "agent-a",
    } as unknown as Project;

    render(<ProjectMembers project={project} onChanged={vi.fn()} />);
    await flush();

    const select = screen.getByLabelText("Project lead") as HTMLSelectElement;
    expect(select.value).toBe("agent-a");

    await act(async () => {
      fireEvent.change(select, { target: { value: "" } });
      await flush();
    });

    expect(fetchMock).toHaveBeenCalledWith(
      "/api/projects/prj-test/lead",
      expect.objectContaining({
        method: "PATCH",
        body: JSON.stringify({ member_id: null }),
      }),
    );
  });
});

describe("ProjectMembers remove confirmation", () => {
  beforeEach(() => {
    vi.restoreAllMocks();
  });

  it("asks for confirmation before removing a member and only removes on confirm", async () => {
    const fetchMock = mockFetch({
      "/api/projects/prj-test/members": { ok: true, body: { items: [memberRow("agent-a")] } },
      "/api/agents": { ok: true, body: [agentA] },
      "/api/agents/registry": { ok: true, body: [] },
      "/api/projects/prj-test/members/agent-a": { ok: true, body: { ok: true } },
    });
    vi.stubGlobal("fetch", fetchMock);

    render(<ProjectMembers project={baseProject} onChanged={vi.fn()} />);
    await flush();

    // Clicking Remove opens a confirm dialog and does NOT delete yet.
    fireEvent.click(screen.getByLabelText("Remove Alpha"));
    expect(screen.getByRole("dialog", { name: "Remove member" })).toBeInTheDocument();
    expect(fetchMock).not.toHaveBeenCalledWith(
      "/api/projects/prj-test/members/agent-a",
      expect.objectContaining({ method: "DELETE" }),
    );

    // Confirming fires the DELETE.
    const dialog = screen.getByRole("dialog", { name: "Remove member" });
    await act(async () => {
      fireEvent.click(within(dialog).getByRole("button", { name: "Remove" }));
      await flush();
    });
    expect(fetchMock).toHaveBeenCalledWith(
      "/api/projects/prj-test/members/agent-a",
      expect.objectContaining({ method: "DELETE" }),
    );
  });
});

describe("ProjectMembers rename external agent", () => {
  beforeEach(() => {
    vi.restoreAllMocks();
  });

  it("renames an external agent via PATCH /api/agents/registry/{id}", async () => {
    const fetchMock = mockFetch({
      "/api/projects/prj-test/members": { ok: true, body: { items: [externalMember] } },
      "/api/agents": { ok: true, body: [] },
      "/api/agents/registry": { ok: true, body: [registryEntry] },
      "/api/agents/registry/grok-taos-20260711-000736": { ok: true, body: { ok: true } },
    });
    vi.stubGlobal("fetch", fetchMock);

    render(<ProjectMembers project={project} onChanged={vi.fn()} />);
    await flush();

    fireEvent.click(screen.getByLabelText("Rename grok-taOS"));
    const input = screen.getByLabelText("New name for grok-taOS") as HTMLInputElement;
    fireEvent.change(input, { target: { value: "hy3 (grok)" } });
    await act(async () => {
      fireEvent.click(screen.getByRole("button", { name: "Save" }));
      await flush();
    });

    expect(fetchMock).toHaveBeenCalledWith(
      "/api/agents/registry/grok-taos-20260711-000736",
      expect.objectContaining({
        method: "PATCH",
        body: JSON.stringify({ display_name: "hy3 (grok)" }),
      }),
    );
  });

  it("disables Save and ignores Enter while the rename request is in flight", async () => {
    let resolveRename!: (value: { ok: boolean }) => void;
    const renamePromise = new Promise<{ ok: boolean }>((resolve) => {
      resolveRename = resolve;
    });

    const fetchMock = vi.fn().mockImplementation((input: string, init?: RequestInit) => {
      if (input === "/api/agents/registry/grok-taos-20260711-000736" && init?.method === "PATCH") {
        return renamePromise.then(() => ({
          ok: true,
          json: async () => ({ ok: true }),
          text: async () => "",
        }));
      }
      if (input === "/api/projects/prj-test/members") {
        return Promise.resolve({
          ok: true,
          json: async () => ({ items: [externalMember] }),
          text: async () => "",
        });
      }
      if (input === "/api/agents") {
        return Promise.resolve({ ok: true, json: async () => [], text: async () => "" });
      }
      if (input === "/api/agents/registry") {
        return Promise.resolve({
          ok: true,
          json: async () => [registryEntry],
          text: async () => "",
        });
      }
      throw new Error(`Unmocked fetch: ${input}`);
    });

    vi.stubGlobal("fetch", fetchMock);
    render(<ProjectMembers project={project} onChanged={vi.fn()} />);
    await flush();

    fireEvent.click(screen.getByLabelText("Rename grok-taOS"));
    const input = screen.getByLabelText("New name for grok-taOS");
    fireEvent.change(input, { target: { value: "hy3 (grok)" } });

    fireEvent.click(screen.getByRole("button", { name: "Save" }));

    const saveButton = screen.getByRole("button", { name: "Saving..." });
    expect(saveButton).toBeDisabled();

    fireEvent.keyDown(input, { key: "Enter" });

    resolveRename({ ok: true });
    await flush();

    const patchCalls = fetchMock.mock.calls.filter(
      (c: [string, RequestInit]) => c[1]?.method === "PATCH",
    );
    expect(patchCalls).toHaveLength(1);
  });

  it("shows an error when the rename PATCH rejects", async () => {
    const fetchMock = mockFetch({
      "/api/projects/prj-test/members": { ok: true, body: { items: [externalMember] } },
      "/api/agents": { ok: true, body: [] },
      "/api/agents/registry": { ok: true, body: [registryEntry] },
      "/api/agents/registry/grok-taos-20260711-000736": { ok: false, status: 400, body: { error: "Bad request" } },
    });
    vi.stubGlobal("fetch", fetchMock);

    render(<ProjectMembers project={project} onChanged={vi.fn()} />);
    await flush();

    fireEvent.click(screen.getByLabelText("Rename grok-taOS"));
    const input = screen.getByLabelText("New name for grok-taOS");
    fireEvent.change(input, { target: { value: "new-name" } });
    await act(async () => {
      fireEvent.click(screen.getByRole("button", { name: "Save" }));
      await flush();
    });

    expect(screen.getByText("Bad request")).toBeInTheDocument();
  });

  it("reloads the registry and refreshes the label after a successful rename", async () => {
    const registryCalls: string[] = [];
    const fetchMock = vi.fn().mockImplementation((input: string) => {
      if (input === "/api/projects/prj-test/members") {
        return Promise.resolve({
          ok: true,
          json: async () => ({ items: [externalMember] }),
          text: async () => "",
        });
      }
      if (input === "/api/agents") {
        return Promise.resolve({ ok: true, json: async () => [], text: async () => "" });
      }
      if (input === "/api/agents/registry") {
        registryCalls.push(input);
        const displayName = registryCalls.filter((c) => c === input).length === 1
          ? "grok-taOS"
          : "hy3 (grok)";
        return Promise.resolve({
          ok: true,
          json: async () => [{ ...registryEntry, display_name: displayName }],
          text: async () => "",
        });
      }
      if (input === "/api/agents/registry/grok-taos-20260711-000736") {
        return Promise.resolve({ ok: true, json: async () => ({ ok: true }), text: async () => "" });
      }
      throw new Error(`Unmocked fetch: ${input}`);
    });

    vi.stubGlobal("fetch", fetchMock);
    render(<ProjectMembers project={project} onChanged={vi.fn()} />);
    await flush();

    const externalSection = screen.getByText("External / Connected agents").closest("section")!;
    expect(within(externalSection).getByText("grok-taOS")).toBeInTheDocument();

    fireEvent.click(screen.getByLabelText("Rename grok-taOS"));
    const input = screen.getByLabelText("New name for grok-taOS");
    fireEvent.change(input, { target: { value: "hy3 (grok)" } });
    await act(async () => {
      fireEvent.click(screen.getByRole("button", { name: "Save" }));
      await flush();
    });

    expect(registryCalls.filter((c) => c === "/api/agents/registry").length).toBeGreaterThanOrEqual(2);
    expect(within(externalSection).getByText("hy3 (grok)")).toBeInTheDocument();
  });
});

const project = { id: "prj-test", name: "taOS", slug: "taos" } as unknown as Project;

// A consent-flow external agent: the member row keys on the canonical id, and
// the registry entry has an EMPTY handle (this is what the approve flow writes).
const externalMember = {
  project_id: "prj-test",
  member_id: "grok-taos-20260711-000736",
  member_kind: "native",
  role: "member",
  is_lead: 0,
  can_edit_canvas: 0,
};

const registryEntry = {
  canonical_id: "grok-taos-20260711-000736",
  handle: "",
  display_name: "grok-taOS",
  framework: "grok",
  origin: "external-selfjoin",
  status: "active",
};

describe("ProjectMembers external registry reload for late-approved agents", () => {
  beforeEach(() => {
    vi.restoreAllMocks();
  });

  it("moves a newly approved external agent into External / Connected agents after registry reload", async () => {
    let registryCallCount = 0;
    const fetchMock = vi.fn().mockImplementation((input: string) => {
      if (input === "/api/projects/prj-test/members") {
        return Promise.resolve({
          ok: true,
          json: async () => ({ items: [{ ...externalMember, member_id: "agent-new" }] }),
          text: async () => "",
        });
      }
      if (input === "/api/agents") {
        return Promise.resolve({ ok: true, json: async () => [], text: async () => "" });
      }
      if (input === "/api/agents/registry") {
        registryCallCount++;
        if (registryCallCount === 1) {
          return Promise.resolve({ ok: true, json: async () => [], text: async () => "" });
        }
        return Promise.resolve({
          ok: true,
          json: async () => [
            {
              origin: "external-selfjoin",
              status: "active",
              canonical_id: "agent-new",
              handle: "@new",
              display_name: "New",
            },
          ],
          text: async () => "",
        });
      }
      throw new Error(`Unmocked fetch: ${input}`);
    });
    vi.stubGlobal("fetch", fetchMock);

    render(<ProjectMembers project={project} onChanged={vi.fn()} />);
    await flush();

    // Wait for the effect to detect the unresolved member and reload the registry
    await act(async () => {
      await new Promise((r) => setTimeout(r, 100));
    });
    await flush();

    // After reload, the member should be in the External project members list
    const externalList = screen.getByRole("list", { name: "External project members" });
    expect(externalList).toBeInTheDocument();
    expect(within(externalList).getByText("New")).toBeInTheDocument();
    // The handle @new appears in the title attribute (tooltip), not as visible text
    expect(within(externalList).getByTitle("@new")).toBeInTheDocument();

    // And NOT in the main Project members list
    const mainList = screen.getByRole("list", { name: "Project members" });
    expect(within(mainList).queryByText("New")).not.toBeInTheDocument();
    expect(within(mainList).queryByText("agent-new")).not.toBeInTheDocument();
  });

  it("does not loop when registry never contains the member id (fetches at most twice)", async () => {
    let registryCallCount = 0;
    const fetchMock = vi.fn().mockImplementation((input: string) => {
      if (input === "/api/projects/prj-test/members") {
        return Promise.resolve({
          ok: true,
          json: async () => ({ items: [{ ...externalMember, member_id: "agent-unknown" }] }),
          text: async () => "",
        });
      }
      if (input === "/api/agents") {
        return Promise.resolve({ ok: true, json: async () => [], text: async () => "" });
      }
      if (input === "/api/agents/registry") {
        registryCallCount++;
        return Promise.resolve({ ok: true, json: async () => [], text: async () => "" });
      }
      throw new Error(`Unmocked fetch: ${input}`);
    });
    vi.stubGlobal("fetch", fetchMock);

    render(<ProjectMembers project={project} onChanged={vi.fn()} />);
    await flush();

    // Wait for potential reload attempts
    await act(async () => {
      await new Promise((r) => setTimeout(r, 200));
    });
    await flush();

    // Registry should be fetched at most twice (initial + one reload attempt)
    expect(registryCallCount).toBeLessThanOrEqual(2);

    // Member should stay in the main Project members list
    const mainList = screen.getByRole("list", { name: "Project members" });
    expect(within(mainList).getByText("agent-unknown")).toBeInTheDocument();

    // External section should not render (no external members)
    expect(screen.queryByText("External / Connected agents")).not.toBeInTheDocument();
  });
});

describe("ProjectMembers registry race condition guard", () => {
  beforeEach(() => {
    vi.restoreAllMocks();
  });

  it("ignores stale registry responses so an older empty reload cannot undo a newer one", async () => {
    // We need exactly two concurrent reloadExternalRegistry calls:
    // Call 1: unresolved-member effect (starts first, resolves LAST with empty)
    // Call 2: triggered by a user action (starts second, resolves FIRST with agent-new)
    // Use separate promises for each call to avoid cross-contamination
    let resolveCall1!: (value: unknown) => void;
    let resolveCall2!: (value: unknown) => void;
    const call1Promise = new Promise((resolve) => {
      resolveCall1 = resolve;
    });
    const call2Promise = new Promise((resolve) => {
      resolveCall2 = resolve;
    });

    let totalRegistryCallCount = 0;
    let reloadCallCount = 0;
    let call1Returned = false, call2Returned = false;
    const fetchMock = vi.fn().mockImplementation((input: string) => {
      if (input === "/api/projects/prj-test/members") {
        return Promise.resolve({
          ok: true,
          json: async () => ({ items: [externalMember, { ...externalMember, member_id: "agent-new" }] }),
          text: async () => "",
        });
      }
      if (input === "/api/agents") {
        return Promise.resolve({ ok: true, json: async () => [], text: async () => "" });
      }
      if (input === "/api/agents/registry") {
        totalRegistryCallCount++;
        if (totalRegistryCallCount === 1) {
          // Mount effect: resolve immediately with existing external agent
          return Promise.resolve({
            ok: true,
            json: async () => [registryEntry],
            text: async () => "",
          });
        }
        // This is a reloadExternalRegistry call
        reloadCallCount++;
        if (reloadCallCount === 1) {
          // First reload (unresolved-member effect) - deferred, resolves LAST with empty
          return call1Promise.then(() => {
            call1Returned = true;
            return {
              ok: true,
              json: async () => [],
              text: async () => "",
            };
          });
        }
        if (reloadCallCount > 2) {
          return Promise.resolve({ ok: true, json: async () => [], text: async () => "" });
        }
        // Second reload (triggered by user action) - deferred, resolves FIRST with agent-new
        return call2Promise.then(() => {
          call2Returned = true;
          return {
            ok: true,
            json: async () => [
              registryEntry,
              {
                origin: "external-selfjoin",
                status: "active",
                canonical_id: "agent-new",
                handle: "@new",
                display_name: "New Agent",
              },
            ],
            text: async () => "",
          };
        });
      }
      if (input === "/api/agents/registry/grok-taos-20260711-000736") {
        return Promise.resolve({ ok: true, json: async () => ({ ok: true }), text: async () => "" });
      }
      if (input === "/api/projects/prj-test/members/grok-taos-20260711-000736/read") {
        return Promise.resolve({ ok: true, json: async () => ({ ok: true }), text: async () => "" });
      }
      if (input === "/api/projects/prj-test/members/grok-taos-20260711-000736/edit") {
        return Promise.resolve({ ok: true, json: async () => ({ ok: true }), text: async () => "" });
      }
      if (input === "/api/projects/prj-test/canvas/permissions/grok-taos-20260711-000736") {
        return Promise.resolve({ ok: true, json: async () => ({ ok: true }), text: async () => "" });
      }
      throw new Error(`Unmocked fetch: ${input}`);
    });
    vi.stubGlobal("fetch", fetchMock);

    render(<ProjectMembers project={project} onChanged={vi.fn()} />);
    await flush();

    // Wait for mount effect to resolve and unresolved-member effect to trigger first reload
    await act(async () => {
      await new Promise((r) => setTimeout(r, 0));
    });
    await flush();

    // At this point: mount effect resolved, unresolved-member effect ran and started reload call 1 (deferred)
    // Now trigger a second reload by toggling a canvas permission on the external agent (calls onRefresh -> refresh)
    const readCheckbox = screen.getByLabelText("Can read canvas for grok-taOS") as HTMLInputElement;
    await act(async () => {
      fireEvent.click(readCheckbox);
      await new Promise((r) => setTimeout(r, 0));
    });
    await flush();

    // Verify both reloads are in flight before resolving call2
    expect(reloadCallCount).toBe(2);

    // Now we have two deferred reloads in flight: call 1 (unresolved-member) and call 2 (permission change -> refresh)
    // Resolve the SECOND reload first (newer response with the agent-new)
    resolveCall2({});
    await act(async () => {
      await new Promise((r) => setTimeout(r, 0));
    });
    await flush();

    // Record which call returned which payload for assertion messages
    // call2Returned should be true now (second reload resolved first)
    expect(call2Returned).toBe(true);
    expect(call1Returned).toBe(false);

    // Then resolve the FIRST reload (older empty response)
    resolveCall1({});
    await act(async () => {
      await new Promise((r) => setTimeout(r, 0));
    });
    await flush();

    expect(reloadCallCount).toBe(2);

    // call1Returned should be true now (first reload resolved after)
    expect(call1Returned).toBe(true);

    // After both settle, agent-new should be in External / Connected agents
    const externalList = screen.getByRole("list", { name: "External project members" });
    expect(externalList).toBeInTheDocument();
    expect(within(externalList).getByText("New Agent")).toBeInTheDocument();
    expect(within(externalList).getByTitle("@new")).toBeInTheDocument();

    // And NOT in the main Project members list
    const mainList = screen.getByRole("list", { name: "Project members" });
    expect(within(mainList).queryByText("New Agent")).not.toBeInTheDocument();
    expect(within(mainList).queryByText("agent-new")).not.toBeInTheDocument();
  });
});
describe("ProjectMembers refresh race condition with unresolved member", () => {
  beforeEach(() => {
    vi.restoreAllMocks();
  });

  it("keeps unresolved member in main list when refresh resolves before mount effect", async () => {
    // Race: mount registry fetch (call 1) deferred, user-action refresh (call 2) resolves first.
    // The unresolved member must remain in the main list after both settle.
    let resolveMountRegistry!: (value: unknown) => void;
    const mountRegistryPromise = new Promise((resolve) => {
      resolveMountRegistry = resolve;
    });

    let totalRegistryCallCount = 0;
    let reloadCallCount = 0;
    let refreshResolved = false;
    const fetchMock = vi.fn().mockImplementation((input: string) => {
      if (input === "/api/projects/prj-test/members") {
        // One native agent (in /api/agents) + one unresolved member (in no registry, not in /api/agents)
        return Promise.resolve({
          ok: true,
          json: async () => ({
            items: [memberRow("agent-a"), memberRow("unresolved-member")],
          }),
          text: async () => "",
        });
      }
      if (input === "/api/agents") {
        // agent-a exists so its canvas checkbox renders
        return Promise.resolve({ ok: true, json: async () => [agentA], text: async () => "" });
      }
      if (input === "/api/agents/registry") {
        totalRegistryCallCount++;
        if (totalRegistryCallCount === 1) {
          // Mount effect: deferred, resolves LAST with empty
          return mountRegistryPromise.then(() => {
            return { ok: true, json: async () => [], text: async () => "" };
          });
        }
        // Reload call (refresh from user action or unresolved-member effect)
        reloadCallCount++;
        // Refresh call (user action): resolves FIRST with empty
        if (reloadCallCount === 1) {
          refreshResolved = true;
          return Promise.resolve({ ok: true, json: async () => [], text: async () => "" });
        }
        // Unresolved-member effect reload: resolves with empty
        return Promise.resolve({ ok: true, json: async () => [], text: async () => "" });
      }
      // Mock the canvas permission PATCH that the checkbox handler sends
      if (input === "/api/projects/prj-test/canvas/permissions/agent-a") {
        return Promise.resolve({ ok: true, json: async () => ({ ok: true }), text: async () => "" });
      }
      throw new Error(`Unmocked fetch: ${input}`);
    });
    vi.stubGlobal("fetch", fetchMock);

    render(<ProjectMembers project={baseProject} onChanged={vi.fn()} />);
    await flush();

    // Wait for mount effect to start (it will hit the deferred promise)
    await act(async () => {
      await new Promise((r) => setTimeout(r, 0));
    });
    await flush();

    // Click the "Can read canvas" checkbox for agent-a -> triggers onRefresh -> refresh -> reloadExternalRegistry
    const readCheckbox = screen.getByLabelText("Can read canvas for Alpha") as HTMLInputElement;
    await act(async () => {
      fireEvent.click(readCheckbox);
      await new Promise((r) => setTimeout(r, 0));
    });
    await flush();

    // After refresh resolves, unresolved-member effect also fires (both are reload calls)
    expect(reloadCallCount).toBe(2);
    expect(refreshResolved).toBe(true);

    // Now resolve the MOUNT call (call 1) last
    resolveMountRegistry({});
    await act(async () => {
      await new Promise((r) => setTimeout(r, 0));
    });
    await flush();

    // Assert: exactly 2 reload calls were made (refresh + unresolved-member effect)
    expect(reloadCallCount).toBe(2);
    // Assert: unresolved-member is rendered in the main Project members list
    const mainList = screen.getByRole("list", { name: "Project members" });
    expect(within(mainList).getByText("unresolved-member")).toBeInTheDocument();
  });
});
