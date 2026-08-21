import {
  render,
  screen,
  fireEvent,
  act,
  waitFor,
  within,
} from "@testing-library/react";
import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { DecisionsApp } from "./DecisionsApp";
import { useDecisionEventsStore } from "@/stores/decision-events-store";

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
    useDecisionEventsStore.setState({ answeredEpoch: 0, lastAnsweredId: null });
  });

  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("renders pending decisions with the recommended option highlighted", async () => {
    vi.stubGlobal(
      "fetch",
      mockFetch({
        "GET /api/decisions?status=pending": { ok: true, body: [singleSelect] },
        "GET /api/decisions?status=answered": { ok: true, body: [] },
        "GET /api/agents/auth-requests?status=pending": { ok: true, body: { requests: [] } },
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
      "GET /api/agents/auth-requests?status=pending": { ok: true, body: { requests: [] } },
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

    const submitBtn = await waitFor(() =>
      screen.getByRole("button", { name: /submit/i }),
    );
    fireEvent.click(submitBtn);
    await flush();

    const post = fetchMock.mock.calls.find(
      (c) => (c[1] as RequestInit)?.method === "POST",
    );
    expect(post).toBeTruthy();
    expect(post![0]).toBe("/api/decisions/dec-1/answer");
    const sent = JSON.parse((post![1] as RequestInit).body as string);
    expect(sent.value).toBe("excalidraw");
  });

  it("submits other_value and note when Other is chosen on single_select", async () => {
    const fetchMock = mockFetch({
      "GET /api/decisions?status=pending": { ok: true, body: [singleSelect] },
      "GET /api/decisions?status=answered": { ok: true, body: [] },
      "GET /api/agents/auth-requests?status=pending": { ok: true, body: { requests: [] } },
      "POST /api/decisions/dec-1/answer": {
        ok: true,
        body: { ...singleSelect, status: "answered", answer: { value: "my answer", other_value: "my answer", note: "caveat" } },
      },
    });
    vi.stubGlobal("fetch", fetchMock);
    render(<DecisionsApp windowId="w1" />);
    await flush();

    await waitFor(() => expect(screen.getByText("Other")).toBeTruthy());
    fireEvent.click(screen.getByText("Other"));
    await flush();

    const textarea = screen.getByLabelText(/other answer/i);
    fireEvent.change(textarea, { target: { value: "my answer" } });
    const noteInput = screen.getByLabelText(/answer note/i);
    fireEvent.change(noteInput, { target: { value: "caveat" } });

    const submitBtn = screen.getByRole("button", { name: /submit/i });
    fireEvent.click(submitBtn);
    await flush();

    const post = fetchMock.mock.calls.find(
      (c) => (c[1] as RequestInit)?.method === "POST",
    );
    expect(post).toBeTruthy();
    const sent = JSON.parse((post![1] as RequestInit).body as string);
    expect(sent.other_value).toBe("my answer");
    expect(sent.note).toBe("caveat");
  });

  it("submits other_value and selected options on multi_select", async () => {
    const multiSelect = {
      ...singleSelect,
      type: "multi_select",
    };
    const fetchMock = mockFetch({
      "GET /api/decisions?status=pending": { ok: true, body: [multiSelect] },
      "GET /api/decisions?status=answered": { ok: true, body: [] },
      "GET /api/agents/auth-requests?status=pending": { ok: true, body: { requests: [] } },
      "POST /api/decisions/dec-1/answer": {
        ok: true,
        body: { ...multiSelect, status: "answered", answer: { value: ["excalidraw", "other text"] } },
      },
    });
    vi.stubGlobal("fetch", fetchMock);
    render(<DecisionsApp windowId="w1" />);
    await flush();

    fireEvent.click(screen.getByText("Excalidraw"));
    await flush();

    fireEvent.click(screen.getByText("Other"));
    await flush();

    const textarea = screen.getByLabelText(/other answer/i);
    fireEvent.change(textarea, { target: { value: "other text" } });

    const submitBtn = screen.getByRole("button", { name: /submit/i });
    fireEvent.click(submitBtn);
    await flush();

    const post = fetchMock.mock.calls.find(
      (c) => (c[1] as RequestInit)?.method === "POST",
    );
    expect(post).toBeTruthy();
    const sent = JSON.parse((post![1] as RequestInit).body as string);
    expect(sent.value).toEqual(["excalidraw"]);
    expect(sent.other_value).toBe("other text");
  });

  it("shows an empty state when no decisions are pending", async () => {
    vi.stubGlobal(
      "fetch",
      mockFetch({
        "GET /api/decisions?status=pending": { ok: true, body: [] },
        "GET /api/decisions?status=answered": { ok: true, body: [] },
        "GET /api/agents/auth-requests?status=pending": { ok: true, body: { requests: [] } },
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
      "GET /api/agents/auth-requests?status=pending": { ok: true, body: { requests: [] } },
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

  it("lists a pending access request and approves it via the consent action", async () => {
    const fetchMock = mockFetch({
      "GET /api/decisions?status=pending": { ok: true, body: [] },
      "GET /api/decisions?status=answered": { ok: true, body: [] },
      "GET /api/agents/auth-requests?status=pending": {
        ok: true,
        body: {
          requests: [
            {
              id: "req-1",
              identity_claim: "owl@lab",
              framework: "smolagents",
              requested_scopes: ["memory_read", "a2a_send"],
            },
          ],
        },
      },
      "POST /api/agents/auth-requests/req-1/approve": { ok: true, body: { status: "accepted" } },
    });
    vi.stubGlobal("fetch", fetchMock);
    render(<DecisionsApp windowId="w1" />);
    await flush();

    await waitFor(() => expect(screen.getByText("owl@lab")).toBeTruthy());
    expect(screen.getByText(/access requests/i)).toBeTruthy();

    fireEvent.click(screen.getByRole("button", { name: /allow/i }));
    await flush();

    const post = fetchMock.mock.calls.find(
      (c) => (c[1] as RequestInit)?.method === "POST",
    );
    expect(post![0]).toBe("/api/agents/auth-requests/req-1/approve");
    const sent = JSON.parse((post![1] as RequestInit).body as string);
    expect(sent.granted_scopes).toEqual(["memory_read", "a2a_send"]);
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
        "GET /api/agents/auth-requests?status=pending": { ok: true, body: { requests: [] } },
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
        "GET /api/agents/auth-requests?status=pending": { ok: true, body: { requests: [] } },
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
      "GET /api/agents/auth-requests?status=pending": { ok: true, body: { requests: [] } },
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

  it("refreshes when the window regains focus", async () => {
    const fetchMock = mockFetch({
      "GET /api/decisions?status=pending": { ok: true, body: [] },
      "GET /api/decisions?status=answered": { ok: true, body: [] },
      "GET /api/agents/auth-requests?status=pending": { ok: true, body: { requests: [] } },
    });
    vi.stubGlobal("fetch", fetchMock);
    render(<DecisionsApp windowId="w1" />);
    await flush();

    const pendingCalls = (url: string) =>
      fetchMock.mock.calls.filter((c) => c[0] === url).length;
    const initialPending = pendingCalls("/api/decisions?status=pending");

    window.dispatchEvent(new Event("focus"));
    await act(async () => {
      await new Promise((r) => setTimeout(r, 1100));
    });

    expect(pendingCalls("/api/decisions?status=pending")).toBe(initialPending + 1);
  });

  it("keeps decisions on screen while the focus refresh is in flight", async () => {
    vi.stubGlobal(
      "fetch",
      mockFetch({
        "GET /api/decisions?status=pending": { ok: true, body: [singleSelect] },
        "GET /api/decisions?status=answered": { ok: true, body: [] },
        "GET /api/agents/auth-requests?status=pending": { ok: true, body: { requests: [] } },
      }),
    );
    render(<DecisionsApp windowId="w1" />);
    await flush();
    expect(screen.getByText(singleSelect.question)).toBeTruthy();

    // Hold the refresh open so the in-flight state is observable rather than
    // racing past: a background refetch must not blank what the user is
    // reading, and this app already has load({ silent: true }) for that.
    const held: Array<() => void> = [];
    vi.stubGlobal(
      "fetch",
      vi.fn().mockImplementation(
        () =>
          new Promise((resolve) => {
            held.push(() =>
              resolve({
                ok: true,
                status: 200,
                json: () => Promise.resolve([]),
              }),
            );
          }),
      ),
    );

    window.dispatchEvent(new Event("focus"));
    await act(async () => {
      await new Promise((r) => setTimeout(r, 1100));
    });

    expect(screen.queryByText("Loading...")).toBeNull();
    expect(screen.getByText(singleSelect.question)).toBeTruthy();

    await act(async () => {
      held.forEach((release) => release());
      await new Promise((r) => setTimeout(r, 0));
    });
  });

  it("last-started load wins over an older stale response", async () => {
    const olderPending = [singleSelect];
    const newerPending: Decision[] = [];
    let callCount = 0;
    const heldResolvers: Array<() => void> = [];

    const fetchMock = vi.fn().mockImplementation((input: string) => {
      callCount++;
      if (callCount <= 3) {
        return new Promise((resolve) => {
          heldResolvers.push(() =>
            resolve({
              ok: true,
              status: 200,
              json: () => {
                if (input.includes("status=pending") && !input.includes("auth-requests"))
                  return Promise.resolve(olderPending);
                if (input.includes("status=answered"))
                  return Promise.resolve([]);
                return Promise.resolve({ requests: [] });
              },
            }),
          );
        });
      }
      return Promise.resolve({
        ok: true,
        status: 200,
        json: () => {
          if (input.includes("status=pending") && !input.includes("auth-requests"))
            return Promise.resolve(newerPending);
          if (input.includes("status=answered"))
            return Promise.resolve([]);
          return Promise.resolve({ requests: [] });
        },
      });
    });

    vi.stubGlobal("fetch", fetchMock);
    render(<DecisionsApp windowId="w1" />);

    // Mount load A starts but its fetches are held pending.
    await act(async () => {
      await new Promise((r) => setTimeout(r, 50));
    });

    // Focus refresh triggers load B after the 1 s debounce.
    window.dispatchEvent(new Event("focus"));

    // Wait for load B to land.
    await act(async () => {
      await new Promise((r) => setTimeout(r, 1200));
    });

    // Now release the held load A with older data.
    await act(async () => {
      heldResolvers.forEach((r) => r());
      await new Promise((r) => setTimeout(r, 0));
    });

    // Guard against a vacuous pass: if the mount load's loss of the race left
    // `loading` stuck true, the list is not rendered at all and the stale-data
    // assert below would pass no matter what state holds.
    expect(screen.queryByText("Loading...")).toBeNull();
    // The rendered list must still reflect load B's newer data, not load A's stale data.
    expect(screen.queryByText(singleSelect.question)).toBeNull();
  });

  it("stale response held at json() parse must not overwrite a newer load", async () => {
    // Unlike the test above, load A's FETCHES resolve immediately — it is the
    // awaited json() parse that is held. The seq guard has already been
    // evaluated by then, so a check placed before the await cannot catch this.
    const olderPending = [singleSelect];
    const newerPending: Decision[] = [];
    let callCount = 0;
    const heldJsonResolvers: Array<() => void> = [];

    const fetchMock = vi.fn().mockImplementation((input: string) => {
      callCount++;
      const bodyFor = (data: Decision[] | never[]) => {
        if (input.includes("status=pending") && !input.includes("auth-requests")) return data;
        if (input.includes("status=answered")) return [];
        return { requests: [] };
      };
      if (callCount <= 3) {
        return Promise.resolve({
          ok: true,
          status: 200,
          json: () =>
            new Promise((resolve) => {
              heldJsonResolvers.push(() => resolve(bodyFor(olderPending)));
            }),
        });
      }
      return Promise.resolve({
        ok: true,
        status: 200,
        json: () => Promise.resolve(bodyFor(newerPending)),
      });
    });

    vi.stubGlobal("fetch", fetchMock);
    render(<DecisionsApp windowId="w1" />);

    // Load A's fetches resolve; A is now suspended inside await json().
    await act(async () => {
      await new Promise((r) => setTimeout(r, 50));
    });

    // Focus refresh triggers load B after the 1 s debounce; B completes fully.
    window.dispatchEvent(new Event("focus"));
    await act(async () => {
      await new Promise((r) => setTimeout(r, 1200));
    });

    // Release A's held json() bodies with the older data. A awaits the three
    // bodies sequentially, so each release lets it register the next held
    // json(); drain until none remain so A runs to completion (incl. finally).
    await act(async () => {
      while (heldJsonResolvers.length > 0) {
        heldJsonResolvers.splice(0).forEach((r) => r());
        await new Promise((r) => setTimeout(r, 0));
      }
    });

    // Same vacuity guard as above: the list must actually be rendered.
    expect(screen.queryByText("Loading...")).toBeNull();
    // B's newer (empty) list must survive; A's stale parse must not land.
    expect(screen.queryByText(singleSelect.question)).toBeNull();
  });

  it("refreshes the list live when a decision is answered from another surface", async () => {
    // Initially the decision is pending; after the SSE event it moves to answered.
    let answeredElsewhere = false;
    const fetchMock = vi.fn().mockImplementation((input: string, init?: RequestInit) => {
      const method = (init?.method ?? "GET").toUpperCase();
      if (method === "GET" && input === "/api/decisions?status=pending") {
        const body = answeredElsewhere ? [] : [singleSelect];
        return Promise.resolve({ ok: true, json: () => Promise.resolve(body) });
      }
      if (method === "GET" && input === "/api/decisions?status=answered") {
        const answered = answeredElsewhere
          ? [{ ...singleSelect, status: "answered", answer: { value: "excalidraw", answered_by: "jay" } }]
          : [];
        return Promise.resolve({ ok: true, json: () => Promise.resolve(answered) });
      }
      if (method === "GET" && input === "/api/agents/auth-requests?status=pending") {
        return Promise.resolve({ ok: true, json: () => Promise.resolve({ requests: [] }) });
      }
      return Promise.resolve({ ok: true, json: () => Promise.resolve([]) });
    });
    vi.stubGlobal("fetch", fetchMock);

    render(<DecisionsApp windowId="w1" />);
    await flush();

    // The pending decision is visible
    await waitFor(() => expect(screen.getByText(/which canvas engine/i)).toBeTruthy());

    // Simulate an SSE decision.answered event for this decision
    answeredElsewhere = true;
    useDecisionEventsStore.getState().recordAnswered("dec-1");

    // After the silent re-fetch, the pending list is empty
    await waitFor(() => {
      expect(screen.queryByText(/which canvas engine/i)).toBeNull();
    });
  });
});
