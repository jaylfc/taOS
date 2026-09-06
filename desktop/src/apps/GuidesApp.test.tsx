import { render, screen, act, waitFor, fireEvent } from "@testing-library/react";
import { describe, it, expect, vi, beforeEach } from "vitest";
import { GuidesApp } from "./GuidesApp";

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

const tiers = {
  "tier-1": { label: "Small", description: "Single node", icon: "cpu" },
  "tier-2": { label: "Large", description: "Cluster", icon: "server" },
};

const useCases = {
  "uc-1": { label: "Coding", description: "AI coding", icon: "code" },
  "uc-2": { label: "Research", description: "Deep research", icon: "research" },
};

const recommendationsResponse = {
  hardware: "tier-1",
  use_case: "uc-1",
  recommendations: [
    { model: "Model A", reason: "Best for coding", note: "Very fast" },
    { model: "Model B", reason: "Most capable" },
  ],
};

describe("GuidesApp", () => {
  beforeEach(() => {
    vi.restoreAllMocks();
  });

  it("fetches tiers and use cases on mount", async () => {
    const fetchMock = mockFetch({
      "/api/guides/tiers": { ok: true, body: { tiers } },
      "/api/guides/use-cases": { ok: true, body: { use_cases: useCases } },
    });
    vi.stubGlobal("fetch", fetchMock);
    render(<GuidesApp windowId="w1" />);
    await flush();
    expect(fetchMock).toHaveBeenCalledWith("/api/guides/tiers");
    expect(fetchMock).toHaveBeenCalledWith("/api/guides/use-cases");
  });

  it("renders the header, guideline banner, and initial prompt", async () => {
    vi.stubGlobal(
      "fetch",
      mockFetch({
        "/api/guides/tiers": { ok: true, body: { tiers } },
        "/api/guides/use-cases": { ok: true, body: { use_cases: useCases } },
      }),
    );
    render(<GuidesApp windowId="w1" />);
    await flush();

    expect(screen.getByRole("heading", { name: "Model Guides" })).toBeTruthy();
    expect(
      screen.getByText(/opinionated, curated recommendations/i),
    ).toBeTruthy();
    expect(
      screen.getByText(/select your hardware tier and use case above/i),
    ).toBeTruthy();
  });

  it("renders tier and use case options after metadata loads", async () => {
    vi.stubGlobal(
      "fetch",
      mockFetch({
        "/api/guides/tiers": { ok: true, body: { tiers } },
        "/api/guides/use-cases": { ok: true, body: { use_cases: useCases } },
      }),
    );
    render(<GuidesApp windowId="w1" />);
    await flush();

    expect(screen.getByText(/single node/i)).toBeTruthy();
    expect(screen.getByText(/cluster/i)).toBeTruthy();
    expect(screen.getByText(/ai coding/i)).toBeTruthy();
    expect(screen.getByText(/deep research/i)).toBeTruthy();
  });

  it("disables the Get Recommendations button until both selectors are set", async () => {
    vi.stubGlobal(
      "fetch",
      mockFetch({
        "/api/guides/tiers": { ok: true, body: { tiers } },
        "/api/guides/use-cases": { ok: true, body: { use_cases: useCases } },
      }),
    );
    render(<GuidesApp windowId="w1" />);
    await flush();

    const button = screen.getByRole("button", { name: /get recommendations/i });
    expect(button).toBeDisabled();
  });

  it("fetches recommendations and renders them when a tier and use case are selected", async () => {
    const fetchMock = mockFetch({
      "/api/guides/tiers": { ok: true, body: { tiers } },
      "/api/guides/use-cases": { ok: true, body: { use_cases: useCases } },
      "/api/guides/recommendations?hardware=tier-1&use_case=uc-1": {
        ok: true,
        body: recommendationsResponse,
      },
    });
    vi.stubGlobal("fetch", fetchMock);
    render(<GuidesApp windowId="w1" />);
    await flush();

    const [tierSelect, caseSelect] = screen.getAllByRole("combobox");
    fireEvent.change(tierSelect, { target: { value: "tier-1" } });
    fireEvent.change(caseSelect, { target: { value: "uc-1" } });
    await flush();

    await waitFor(() => {
      expect(fetchMock).toHaveBeenCalledWith(
        "/api/guides/recommendations?hardware=tier-1&use_case=uc-1",
      );
    });
    expect(screen.getByText("Model A")).toBeTruthy();
    expect(screen.getByText("Model B")).toBeTruthy();
    expect(screen.getByText(/best for coding/i)).toBeTruthy();
    expect(screen.getByText("Very fast")).toBeTruthy();
    expect(screen.getByText(/recommended for/i)).toBeTruthy();
    expect(
      screen.getByRole("button", { name: /get recommendations/i }),
    ).toBeEnabled();
  });

  it("shows an error when the recommendations fetch fails", async () => {
    vi.stubGlobal(
      "fetch",
      mockFetch({
        "/api/guides/tiers": { ok: true, body: { tiers } },
        "/api/guides/use-cases": { ok: true, body: { use_cases: useCases } },
        "/api/guides/recommendations?hardware=tier-1&use_case=uc-1": {
          ok: false,
          status: 500,
          body: { detail: "Internal error" },
        },
      }),
    );
    render(<GuidesApp windowId="w1" />);
    await flush();

    const [tierSelect, caseSelect] = screen.getAllByRole("combobox");
    fireEvent.change(tierSelect, { target: { value: "tier-1" } });
    fireEvent.change(caseSelect, { target: { value: "uc-1" } });
    await flush();

    await waitFor(() => {
      expect(screen.getByText(/internal error/i)).toBeTruthy();
    });
    expect(
      screen.queryByText(/no recommendations yet/i),
    ).toBeNull();
  });

  it("shows empty state when the recommendations list is empty", async () => {
    vi.stubGlobal(
      "fetch",
      mockFetch({
        "/api/guides/tiers": { ok: true, body: { tiers } },
        "/api/guides/use-cases": { ok: true, body: { use_cases: useCases } },
        "/api/guides/recommendations?hardware=tier-1&use_case=uc-1": {
          ok: true,
          body: {
            hardware: "tier-1",
            use_case: "uc-1",
            recommendations: [],
          },
        },
      }),
    );
    render(<GuidesApp windowId="w1" />);
    await flush();

    const [tierSelect, caseSelect] = screen.getAllByRole("combobox");
    fireEvent.change(tierSelect, { target: { value: "tier-1" } });
    fireEvent.change(caseSelect, { target: { value: "uc-1" } });
    await flush();

    await waitFor(() => {
      expect(screen.getByText(/no recommendations yet/i)).toBeTruthy();
    });
  });
});
