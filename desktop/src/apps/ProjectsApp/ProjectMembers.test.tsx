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
