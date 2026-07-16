import { render, screen, act, waitFor, fireEvent } from "@testing-library/react";
import { describe, it, expect, vi, beforeEach } from "vitest";
import { SecretsApp } from "./SecretsApp";

// Mock the GitHub integration so its on-mount identity fetch does not
// interfere with the Secrets fetch assertions.
vi.mock("@/lib/github", () => ({
  startDeviceFlow: vi.fn(),
  pollDeviceFlow: vi.fn(),
  listIdentities: vi.fn().mockResolvedValue([]),
  deleteIdentity: vi.fn(),
}));

function mockFetch(
  responses: Record<string, { ok: boolean; status?: number; body: unknown; contentType?: string }>,
) {
  return vi.fn().mockImplementation((input: string) => {
    const hit = responses[input] ?? responses["*"];
    if (!hit) throw new Error(`Unmocked fetch: ${input}`);
    return Promise.resolve({
      ok: hit.ok,
      status: hit.status ?? (hit.ok ? 200 : 500),
      headers: {
        get: (name: string) =>
          name.toLowerCase() === "content-type"
            ? hit.contentType ?? "application/json"
            : null,
      },
      json: () => Promise.resolve(hit.body),
    });
  });
}

async function flush() {
  await act(async () => {
    await new Promise((r) => setTimeout(r, 0));
  });
}

const exampleSecret = {
  name: "OPENAI_API_KEY",
  category: "api-key",
  description: "OpenAI key",
  agents: ["research-agent"],
};

describe("SecretsApp", () => {
  beforeEach(() => {
    vi.restoreAllMocks();
  });

  it("fetches /api/secrets on mount", async () => {
    const fetchMock = mockFetch({
      "/api/secrets": { ok: true, body: [] },
    });
    vi.stubGlobal("fetch", fetchMock);
    render(<SecretsApp windowId="win-1" />);
    await flush();
    expect(fetchMock).toHaveBeenCalledWith("/api/secrets", {
      headers: { Accept: "application/json" },
      signal: expect.any(Object),
    });
  });

  it("shows a loading state before the fetch resolves", () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockReturnValue(new Promise(() => {})),
    );
    render(<SecretsApp windowId="win-1" />);
    expect(screen.getByText(/loading secrets/i)).toBeTruthy();
  });

  it("renders the secrets returned by the API", async () => {
    vi.stubGlobal(
      "fetch",
      mockFetch({
        "/api/secrets": { ok: true, body: [exampleSecret] },
      }),
    );
    render(<SecretsApp windowId="win-1" />);
    await flush();

    await waitFor(() => expect(screen.getByText("OPENAI_API_KEY")).toBeTruthy());
    // Values are masked until revealed.
    expect(screen.getByText("\u2022\u2022\u2022\u2022\u2022\u2022\u2022\u2022")).toBeTruthy();
    expect(screen.getByText("api-key")).toBeTruthy();
    expect(screen.getByText("OpenAI key")).toBeTruthy();
    expect(screen.getByText("research-agent")).toBeTruthy();
    expect(screen.getByText(/1 stored/i)).toBeTruthy();
  });

  it("shows the empty state when there are no secrets", async () => {
    vi.stubGlobal(
      "fetch",
      mockFetch({
        "/api/secrets": { ok: true, body: [] },
      }),
    );
    render(<SecretsApp windowId="win-1" />);
    await flush();
    await waitFor(() => expect(screen.getByText(/no secrets stored/i)).toBeTruthy());
  });

  it("falls back to the empty state when the fetch fails", async () => {
    vi.stubGlobal(
      "fetch",
      mockFetch({
        "/api/secrets": { ok: false, status: 500, body: {} },
      }),
    );
    render(<SecretsApp windowId="win-1" />);
    await flush();
    await waitFor(() => expect(screen.getByText(/no secrets stored/i)).toBeTruthy());
  });

  it("reveals a secret's value via the per-secret API", async () => {
    const fetchMock = mockFetch({
      "/api/secrets": { ok: true, body: [exampleSecret] },
      "/api/secrets/OPENAI_API_KEY": { ok: true, body: { value: "sk-real-key" } },
    });
    vi.stubGlobal("fetch", fetchMock);
    render(<SecretsApp windowId="win-1" />);
    await flush();
    await waitFor(() => expect(screen.getByText("OPENAI_API_KEY")).toBeTruthy());

    fireEvent.click(screen.getByLabelText(/reveal openai_api_key/i));
    await flush();

    expect(fetchMock).toHaveBeenCalledWith("/api/secrets/OPENAI_API_KEY", {
      headers: { Accept: "application/json" },
    });
    await waitFor(() => expect(screen.getByText("sk-real-key")).toBeTruthy());
  });

  it("hides a revealed secret again when toggled", async () => {
    vi.stubGlobal(
      "fetch",
      mockFetch({
        "/api/secrets": { ok: true, body: [exampleSecret] },
        "/api/secrets/OPENAI_API_KEY": { ok: true, body: { value: "sk-real-key" } },
      }),
    );
    render(<SecretsApp windowId="win-1" />);
    await flush();
    await waitFor(() => expect(screen.getByText("OPENAI_API_KEY")).toBeTruthy());

    const revealBtn = screen.getByLabelText(/reveal openai_api_key/i);
    fireEvent.click(revealBtn);
    await flush();
    await waitFor(() => expect(screen.getByText("sk-real-key")).toBeTruthy());

    fireEvent.click(screen.getByLabelText(/hide openai_api_key/i));
    await flush();
    expect(screen.getByText("\u2022\u2022\u2022\u2022\u2022\u2022\u2022\u2022")).toBeTruthy();
  });

  it("adds a new secret through the dialog", async () => {
    vi.stubGlobal(
      "fetch",
      mockFetch({
        "/api/secrets": { ok: true, body: [] },
      }),
    );
    render(<SecretsApp windowId="win-1" />);
    await flush();

    fireEvent.click(screen.getByLabelText(/add new secret/i));
    fireEvent.change(screen.getByLabelText(/name/i), {
      target: { value: "ANTHROPIC_API_KEY" },
    });
    fireEvent.change(screen.getByLabelText(/value/i), {
      target: { value: "sk-ant" },
    });
    fireEvent.click(screen.getByRole("button", { name: /^add$/i }));
    await flush();

    await waitFor(() => expect(screen.getByText("ANTHROPIC_API_KEY")).toBeTruthy());
    expect(screen.getByText(/1 stored/i)).toBeTruthy();
  });

  it("deletes a secret", async () => {
    vi.stubGlobal(
      "fetch",
      mockFetch({
        "/api/secrets": { ok: true, body: [exampleSecret] },
      }),
    );
    render(<SecretsApp windowId="win-1" />);
    await flush();
    await waitFor(() => expect(screen.getByText("OPENAI_API_KEY")).toBeTruthy());

    fireEvent.click(screen.getByLabelText(/delete openai_api_key/i));
    await flush();

    expect(screen.queryByText("OPENAI_API_KEY")).toBeNull();
    expect(screen.getByText(/no secrets stored/i)).toBeTruthy();
  });

  it("filters secrets by category", async () => {
    vi.stubGlobal(
      "fetch",
      mockFetch({
        "/api/secrets": {
          ok: true,
          body: [
            exampleSecret,
            { name: "DB_TOKEN", category: "token", description: "", agents: [] },
          ],
        },
      }),
    );
    render(<SecretsApp windowId="win-1" />);
    await flush();
    await waitFor(() => expect(screen.getByText("OPENAI_API_KEY")).toBeTruthy());
    expect(screen.getByText("DB_TOKEN")).toBeTruthy();

    fireEvent.change(screen.getByLabelText(/filter by category/i), {
      target: { value: "config" },
    });
    await flush();

    expect(screen.queryByText("OPENAI_API_KEY")).toBeNull();
    expect(screen.queryByText("DB_TOKEN")).toBeNull();
    expect(screen.getByText(/no secrets match this filter/i)).toBeTruthy();
  });
});
