/**
 * TaosAgentActivity: the Settings > taOS agent > Activity list (tsk-jku5a5).
 * Reads the taos-agent trace's external_write and local_fix rows.
 */
import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { render, screen, waitFor, within, fireEvent } from "@testing-library/react";
import React from "react";

import { TaosAgentActivity, collapseActivity } from "../agents/TaosAgentActivity";
import { AgentTracesPanel } from "../agents/AgentTracesPanel";

const PENDING = {
  id: "e1", kind: "external_write", created_at: 1_700_000_000,
  payload: { action: "Open a pull request", state: "pending", action_id: "a1",
             repo: "jaylfc/taOS", url: "", target: "github_pr" },
};
const EXECUTED = {
  id: "e2", kind: "external_write", created_at: 1_700_000_100,
  payload: { action: "Open a pull request", state: "executed", action_id: "a1",
             repo: "jaylfc/taOS", url: "https://github.com/jaylfc/taOS/pull/9", target: "github_pr" },
};
const FIX = {
  id: "e3", kind: "local_fix", created_at: 1_700_000_050,
  payload: { tool: "restart_service", target: "qmd", undo: "none", user_id: "u1" },
};

function mockFetch(byKind: Record<string, unknown[]>) {
  return vi.fn(async (url: string) => {
    const kind = new URL(url, "http://x").searchParams.get("kind") ?? "";
    return { ok: true, json: async () => ({ events: byKind[kind] ?? [] }) } as Response;
  });
}

describe("<TaosAgentActivity />", () => {
  afterEach(() => vi.unstubAllGlobals());

  it("lists one line per action with when, what, where and an Open link", async () => {
    const fetchMock = mockFetch({ external_write: [EXECUTED, PENDING], local_fix: [FIX] });
    vi.stubGlobal("fetch", fetchMock);
    render(<TaosAgentActivity />);

    const list = await screen.findByRole("list", { name: "taOS agent activity" });
    const items = within(list).getAllByRole("listitem");
    expect(items).toHaveLength(2);
    expect(items[0]).toHaveTextContent("Open a pull request (done)");
    expect(items[0]).toHaveTextContent("jaylfc/taOS");
    expect(within(items[0]).getByRole("link", { name: /Open/ })).toHaveAttribute(
      "href", "https://github.com/jaylfc/taOS/pull/9",
    );
    expect(items[1]).toHaveTextContent("restart_service on qmd");
    expect(within(items[1]).queryByRole("link")).toBeNull();

    const urls = fetchMock.mock.calls.map((c) => String(c[0]));
    expect(urls).toContain("/api/agents/taos-agent/trace?kind=external_write&limit=200");
    expect(urls).toContain("/api/agents/taos-agent/trace?kind=local_fix&limit=200");
  });

  it("shows an empty state", async () => {
    vi.stubGlobal("fetch", mockFetch({}));
    render(<TaosAgentActivity />);
    expect(await screen.findByText(/Nothing yet/)).toBeInTheDocument();
  });

  it("never links a non-https url", async () => {
    const evil = { ...EXECUTED, payload: { ...EXECUTED.payload, url: "javascript:alert(1)" } };
    vi.stubGlobal("fetch", mockFetch({ external_write: [evil] }));
    render(<TaosAgentActivity />);
    const list = await screen.findByRole("list", { name: "taOS agent activity" });
    expect(within(list).queryByRole("link")).toBeNull();
  });

  it("collapses an action's rows to its newest", () => {
    expect(collapseActivity([PENDING, EXECUTED]).map((e) => e.id)).toEqual(["e2"]);
  });
});

describe("<AgentTracesPanel /> kind filter", () => {
  beforeEach(() => vi.unstubAllGlobals());
  afterEach(() => vi.unstubAllGlobals());

  it("re-queries the trace with the chosen kind", async () => {
    const fetchMock = mockFetch({ external_write: [EXECUTED] });
    vi.stubGlobal("fetch", fetchMock);
    render(<AgentTracesPanel agentName="taos-agent" />);

    const select = await screen.findByLabelText("Filter traces by kind");
    fireEvent.change(select, { target: { value: "external_write" } });

    await waitFor(() =>
      expect(fetchMock.mock.calls.map((c) => String(c[0]))).toContain(
        "/api/agents/taos-agent/trace?limit=100&kind=external_write",
      ),
    );
    expect(await screen.findByText("Open a pull request (executed)")).toBeInTheDocument();
  });
});
