import { render, screen, waitFor, act } from "@testing-library/react";
import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { DecisionsApp } from "@/apps/DecisionsApp";
import { DecisionBlock } from "@/apps/MessagesApp";
import { useDecisionEventsStore } from "@/stores/decision-events-store";

// tsk-mul5pa: the approver must see what a gate decision grants, not only the
// asker's free-text question. Both approval surfaces (the Decisions app card
// and the inline chat block) render the grant-bearing metadata fields.

const executionGate = {
  id: "dec-gate",
  from_agent: "agent-a",
  project_id: null,
  question: "Can I add a note to the board?",
  type: "approve_deny",
  options: [],
  context: null,
  priority: "blocking",
  status: "pending",
  created_at: Date.now() / 1000,
  metadata: {
    _server_raised: true,
    kind: "execution_gate",
    agent_name: "agent-a",
    action_class: "code-exec",
    tool: "code_exec",
  },
};

const appGrant = {
  ...executionGate,
  id: "dec-app",
  type: "multi_select",
  options: [{ label: "Network access", value: "app.net" }],
  metadata: {
    _server_raised: true,
    kind: "app_grant",
    app_id: "stream-chat",
    capabilities: ["app.net", "app.memory"],
  },
};

const ordinary = {
  ...executionGate,
  id: "dec-plain",
  question: "Ship it?",
  metadata: { kind: "decision" },
};

function mockFetch(routes: Record<string, unknown>) {
  return vi.fn().mockImplementation((input: string, init?: RequestInit) => {
    const method = (init?.method ?? "GET").toUpperCase();
    const body = routes[`${method} ${input}`] ?? routes[input];
    if (body === undefined) throw new Error(`Unmocked fetch: ${method} ${input}`);
    return Promise.resolve({ ok: true, status: 200, json: () => Promise.resolve(body) });
  });
}

function inboxRoutes(pending: unknown[]) {
  return {
    "GET /api/decisions?status=pending": pending,
    "GET /api/decisions?status=answered": [],
    "GET /api/agents/auth-requests?status=pending": { requests: [] },
    "GET /api/agents/scope-requests?status=pending": { requests: [] },
    "GET /api/agents/scope-vocabulary": { valid_scopes: [], project_scopes: [] },
  };
}

async function flush() {
  await act(async () => {
    await new Promise((r) => setTimeout(r, 0));
  });
}

describe("gate decision grant details", () => {
  beforeEach(() => {
    useDecisionEventsStore.setState({ answeredEpoch: 0, lastAnsweredId: null });
    const Es = vi.fn().mockImplementation(function (this: Record<string, unknown>) {
      this.close = vi.fn();
      this.readyState = 0;
    });
    Object.assign(Es, { CONNECTING: 0, OPEN: 1, CLOSED: 2 });
    vi.stubGlobal("EventSource", Es);
  });

  afterEach(() => {
    vi.restoreAllMocks();
    vi.unstubAllGlobals();
  });

  it("Decisions app shows what an execution gate grants beside the question", async () => {
    vi.stubGlobal("fetch", mockFetch(inboxRoutes([executionGate])));
    render(<DecisionsApp windowId="w1" />);
    await flush();
    await waitFor(() => expect(screen.getByText(/add a note to the board/i)).toBeTruthy());

    const grant = screen.getByRole("group", { name: /what approving grants/i });
    expect(grant.textContent).toContain("code-exec");
    expect(grant.textContent).toContain("code_exec");
    expect(grant.textContent).toContain("agent-a");
  });

  it("Decisions app lists every capability an app grant asks for", async () => {
    vi.stubGlobal("fetch", mockFetch(inboxRoutes([appGrant])));
    render(<DecisionsApp windowId="w1" />);
    await flush();
    await waitFor(() => expect(screen.getByText(/add a note to the board/i)).toBeTruthy());

    const grant = screen.getByRole("group", { name: /what approving grants/i });
    expect(grant.textContent).toContain("stream-chat");
    expect(grant.textContent).toContain("app.net, app.memory");
  });

  it("Decisions app shows no grant panel for an ordinary decision", async () => {
    vi.stubGlobal("fetch", mockFetch(inboxRoutes([ordinary])));
    render(<DecisionsApp windowId="w1" />);
    await flush();
    await waitFor(() => expect(screen.getByText("Ship it?")).toBeTruthy());

    expect(screen.queryByRole("group", { name: /what approving grants/i })).toBeNull();
  });

  it("a kind naming an inherited Object property still renders the card", async () => {
    const proto = { ...ordinary, metadata: { kind: "toString" } };
    vi.stubGlobal("fetch", mockFetch(inboxRoutes([proto])));
    render(<DecisionsApp windowId="w1" />);
    await flush();
    await waitFor(() => expect(screen.getByText("Ship it?")).toBeTruthy());

    expect(screen.queryByRole("group", { name: /what approving grants/i })).toBeNull();
  });

  it("chat decision block shows what an execution gate grants", async () => {
    vi.stubGlobal("fetch", mockFetch({ "GET /api/decisions/dec-gate": executionGate }));
    render(<DecisionBlock block={{ kind: "decision", decision_id: "dec-gate" }} />);
    await waitFor(() => expect(screen.getByText(/add a note to the board/i)).toBeTruthy());

    const grant = screen.getByRole("group", { name: /what approving grants/i });
    expect(grant.textContent).toContain("code-exec");
  });
});
