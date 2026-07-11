import { render, screen, act } from "@testing-library/react";
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
    expect(screen.getByText("External / Connected agents")).toBeInTheDocument();
    expect(screen.getByText("grok-taOS")).toBeInTheDocument();
    // The framework badge resolves via the canonical-id keyed lookup, and "grok"
    // (not only "grok-build") maps to the friendly Grok label.
    expect(screen.getByText("Grok")).toBeInTheDocument();
  });
});
