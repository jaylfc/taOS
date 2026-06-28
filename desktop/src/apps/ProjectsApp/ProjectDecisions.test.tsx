import { render, screen, act, waitFor } from "@testing-library/react";
import { describe, it, expect, vi, beforeEach } from "vitest";
import { ProjectDecisions } from "./ProjectDecisions";

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
    });
  });
}

async function flush() {
  await act(async () => {
    await new Promise((r) => setTimeout(r, 0));
  });
}

const answered = {
  id: "dec-1",
  from_agent: "@taOS-dev",
  question: "Which canvas engine should replace tldraw?",
  status: "answered",
  options: [{ label: "Excalidraw", value: "excalidraw" }],
  answer: { value: "excalidraw" },
  created_at: Date.now() / 1000,
};

const pending = {
  id: "dec-2",
  from_agent: "@taOSmd-dev",
  question: "Promote a node to control plane?",
  status: "pending",
  options: [],
  created_at: Date.now() / 1000,
};

describe("ProjectDecisions", () => {
  beforeEach(() => {
    vi.restoreAllMocks();
  });

  it("fetches decisions scoped to the project id", async () => {
    const fetchMock = mockFetch({
      "/api/decisions?project_id=prj-7": { ok: true, body: { items: [] } },
    });
    vi.stubGlobal("fetch", fetchMock);
    render(<ProjectDecisions projectId="prj-7" />);
    await flush();
    expect(fetchMock).toHaveBeenCalledWith("/api/decisions?project_id=prj-7");
  });

  it("renders the project's decisions with status and the chosen answer", async () => {
    vi.stubGlobal(
      "fetch",
      mockFetch({
        "/api/decisions?project_id=prj-7": {
          ok: true,
          body: { items: [answered, pending] },
        },
      }),
    );
    render(<ProjectDecisions projectId="prj-7" />);
    await flush();

    await waitFor(() =>
      expect(screen.getByText(/which canvas engine/i)).toBeTruthy(),
    );
    expect(screen.getByText(/promote a node/i)).toBeTruthy();
    // The answered decision resolves its option value to the label.
    expect(screen.getByText(/answer: excalidraw/i)).toBeTruthy();
    expect(screen.getByText("@taOS-dev")).toBeTruthy();
    expect(screen.getByText("answered")).toBeTruthy();
    expect(screen.getByText("pending")).toBeTruthy();
  });

  it("shows an empty state when the project has no decisions", async () => {
    vi.stubGlobal(
      "fetch",
      mockFetch({
        "/api/decisions?project_id=prj-7": { ok: true, body: { items: [] } },
      }),
    );
    render(<ProjectDecisions projectId="prj-7" />);
    await flush();
    await waitFor(() =>
      expect(screen.getByText(/no decisions for this project/i)).toBeTruthy(),
    );
  });

  it("shows an error (not the empty state) when the fetch fails", async () => {
    vi.stubGlobal(
      "fetch",
      mockFetch({
        "/api/decisions?project_id=prj-7": { ok: false, status: 500, body: {} },
      }),
    );
    render(<ProjectDecisions projectId="prj-7" />);
    await flush();
    await waitFor(() =>
      expect(screen.getByText(/could not load decisions/i)).toBeTruthy(),
    );
    expect(screen.queryByText(/no decisions for this project/i)).toBeNull();
  });

  it("shows the recorded answer for a superseded decision too", async () => {
    const superseded = {
      ...answered,
      id: "dec-3",
      status: "superseded",
      question: "Superseded engine pick",
    };
    vi.stubGlobal(
      "fetch",
      mockFetch({
        "/api/decisions?project_id=prj-7": { ok: true, body: { items: [superseded] } },
      }),
    );
    render(<ProjectDecisions projectId="prj-7" />);
    await flush();
    await waitFor(() =>
      expect(screen.getByText(/answer: excalidraw/i)).toBeTruthy(),
    );
  });

  it("encodes the project id into the query", async () => {
    const fetchMock = mockFetch({
      "*": { ok: true, body: { items: [] } },
    });
    vi.stubGlobal("fetch", fetchMock);
    render(<ProjectDecisions projectId="a/b c" />);
    await flush();
    expect(fetchMock).toHaveBeenCalledWith("/api/decisions?project_id=a%2Fb%20c");
  });
});
