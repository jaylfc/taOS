import {
  render,
  screen,
  fireEvent,
  act,
  waitFor,
  within,
} from "@testing-library/react";
import { describe, it, expect, vi, beforeEach } from "vitest";
import { DecisionsApp } from "./DecisionsApp";

function mockFetch(
  responses: Record<string, { ok: boolean; status?: number; body: unknown }>,
) {
  return vi.fn().mockImplementation((input: string, init?: RequestInit) => {
    const method = (init?.method ?? "GET").toUpperCase();
    const key = `${method} ${input}`;
    const hit = responses[key] ?? responses[input] ?? responses["*"];
    if (!hit) throw new Error(`Unmocked fetch: ${key}`);
    return Promise.resolve({
      ok: hit.ok,
      status: hit.status ?? (hit.ok ? 200 : 422),
      json: () => Promise.resolve(hit.body),
    });
  });
}

async function flush() {
  await act(async () => {
    await new Promise((r) => setTimeout(r, 0));
  });
}

const singleSelect = {
  id: "dec-1",
  from_agent: "@taOS-dev",
  project_id: "prj-abc",
  question: "Which canvas engine should replace tldraw?",
  type: "single_select",
  options: [
    { label: "Excalidraw", value: "excalidraw", recommended: true, rationale: "MIT licensed" },
    { label: "Konva", value: "konva" },
  ],
  context: "tldraw is buggy and license-incompatible.",
  priority: "normal",
  status: "pending",
  created_at: Date.now() / 1000,
};

describe("DecisionsApp", () => {
  beforeEach(() => {
    vi.restoreAllMocks();
  });

  it("renders pending decisions with the recommended option highlighted", async () => {
    vi.stubGlobal(
      "fetch",
      mockFetch({
        "GET /api/decisions?status=pending": { ok: true, body: [singleSelect] },
        "GET /api/decisions?status=answered": { ok: true, body: [] },
      }),
    );
    render(<DecisionsApp windowId="w1" />);
    await flush();

    await waitFor(() =>
      expect(screen.getByText(/which canvas engine/i)).toBeTruthy(),
    );
    expect(screen.getByText("Excalidraw")).toBeTruthy();
    expect(screen.getByText(/recommended/i)).toBeTruthy();
    expect(screen.getByText(/MIT licensed/i)).toBeTruthy();
    expect(screen.getByText("@taOS-dev")).toBeTruthy();
  });

  it("posts the chosen value to the answer endpoint", async () => {
    const fetchMock = mockFetch({
      "GET /api/decisions?status=pending": { ok: true, body: [singleSelect] },
      "GET /api/decisions?status=answered": { ok: true, body: [] },
      "POST /api/decisions/dec-1/answer": {
        ok: true,
        body: { ...singleSelect, status: "answered", answer: { value: "excalidraw" } },
      },
    });
    vi.stubGlobal("fetch", fetchMock);
    render(<DecisionsApp windowId="w1" />);
    await flush();

    await waitFor(() => expect(screen.getByText("Excalidraw")).toBeTruthy());
    fireEvent.click(screen.getByText("Excalidraw"));
    await flush();

    const post = fetchMock.mock.calls.find(
      (c) => (c[1] as RequestInit)?.method === "POST",
    );
    expect(post).toBeTruthy();
    expect(post![0]).toBe("/api/decisions/dec-1/answer");
    const sent = JSON.parse((post![1] as RequestInit).body as string);
    expect(sent.value).toBe("excalidraw");
  });

  it("shows an empty state when no decisions are pending", async () => {
    vi.stubGlobal(
      "fetch",
      mockFetch({
        "GET /api/decisions?status=pending": { ok: true, body: [] },
        "GET /api/decisions?status=answered": { ok: true, body: [] },
      }),
    );
    render(<DecisionsApp windowId="w1" />);
    await flush();

    await waitFor(() =>
      expect(screen.getByText(/no decisions waiting on you/i)).toBeTruthy(),
    );
  });

  it("renders approve/deny actions for an approve_deny decision", async () => {
    const approveDeny = {
      ...singleSelect,
      id: "dec-2",
      type: "approve_deny",
      options: [],
      question: "Merge PR #1331?",
      priority: "blocking",
    };
    const fetchMock = mockFetch({
      "GET /api/decisions?status=pending": { ok: true, body: [approveDeny] },
      "GET /api/decisions?status=answered": { ok: true, body: [] },
      "POST /api/decisions/dec-2/answer": {
        ok: true,
        body: { ...approveDeny, status: "answered", answer: { value: "approve" } },
      },
    });
    vi.stubGlobal("fetch", fetchMock);
    render(<DecisionsApp windowId="w1" />);
    await flush();

    await waitFor(() => expect(screen.getByText(/merge pr #1331/i)).toBeTruthy());
    expect(screen.getByText(/blocking/i)).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: /approve/i }));
    await flush();

    const post = fetchMock.mock.calls.find(
      (c) => (c[1] as RequestInit)?.method === "POST",
    );
    const sent = JSON.parse((post![1] as RequestInit).body as string);
    expect(sent.value).toBe("approve");
  });

  it("shows answered decisions in the archive tab", async () => {
    const answered = {
      ...singleSelect,
      status: "answered",
      answer: { value: "excalidraw", answered_by: "jay" },
    };
    vi.stubGlobal(
      "fetch",
      mockFetch({
        "GET /api/decisions?status=pending": { ok: true, body: [] },
        "GET /api/decisions?status=answered": { ok: true, body: [answered] },
      }),
    );
    render(<DecisionsApp windowId="w1" />);
    await flush();

    fireEvent.click(screen.getByRole("button", { name: /archive/i }));
    await flush();

    await waitFor(() => expect(screen.getByText("Excalidraw")).toBeTruthy());
  });

  it("offers no history affordance for an original (no parent) decision", async () => {
    const answered = {
      ...singleSelect,
      status: "answered",
      answer: { value: "excalidraw", answered_by: "jay" },
    };
    vi.stubGlobal(
      "fetch",
      mockFetch({
        "GET /api/decisions?status=pending": { ok: true, body: [] },
        "GET /api/decisions?status=answered": { ok: true, body: [answered] },
      }),
    );
    render(<DecisionsApp windowId="w1" />);
    await flush();
    fireEvent.click(screen.getByRole("button", { name: /archive/i }));
    await flush();

    await waitFor(() => expect(screen.getByText("Excalidraw")).toBeTruthy());
    expect(screen.queryByRole("button", { name: /view history/i })).toBeNull();
  });

  it("loads and renders the supersession lineage oldest first on demand", async () => {
    const revision = {
      ...singleSelect,
      id: "dec-2",
      status: "answered",
      question: "Revised tldraw replacement pick",
      answer: { value: "excalidraw", answered_by: "jay" },
      parent_decision_id: "dec-1",
    };
    const original = {
      ...singleSelect,
      id: "dec-1",
      status: "superseded",
      question: "Original tldraw replacement pick",
    };
    const fetchMock = mockFetch({
      "GET /api/decisions?status=pending": { ok: true, body: [] },
      "GET /api/decisions?status=answered": { ok: true, body: [revision] },
      "GET /api/decisions/dec-2/history": {
        ok: true,
        body: { items: [original, revision] },
      },
    });
    vi.stubGlobal("fetch", fetchMock);
    render(<DecisionsApp windowId="w1" />);
    await flush();
    fireEvent.click(screen.getByRole("button", { name: /archive/i }));
    await flush();

    const historyBtn = await waitFor(() =>
      screen.getByRole("button", { name: /view history/i }),
    );
    fireEvent.click(historyBtn);
    await flush();

    // The chain is fetched lazily only on expand.
    const historyCall = fetchMock.mock.calls.find(
      (c) => c[0] === "/api/decisions/dec-2/history",
    );
    expect(historyCall).toBeTruthy();

    // It must render oldest first: the original (superseded) before the revision.
    const trail = await waitFor(() => screen.getByTestId("decision-history"));
    const steps = within(trail).getAllByRole("listitem");
    expect(steps[0].textContent).toContain("Original tldraw replacement pick");
    expect(steps[1].textContent).toContain("Revised tldraw replacement pick");
  });
});
