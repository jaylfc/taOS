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
  listAppInstallations: vi.fn().mockResolvedValue([]),
  beginAppInstallation: vi.fn(),
  deleteAppInstallation: vi.fn(),
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

  it("renders Keenable toggle unchecked when preference is absent", async () => {
    const fetchMock = mockFetch({
      "/api/secrets": { ok: true, body: [] },
      "/api/preferences/broker": { ok: true, body: {} },
    });
    vi.stubGlobal("fetch", fetchMock);
    render(<SecretsApp windowId="win-1" />);
    await flush();
    // Wait for keenable preference to load
    await waitFor(() =>
      expect(screen.getByLabelText(/allow keenable keyless search/i)).toBeTruthy(),
    );
    const toggle = screen.getByRole("switch", { name: /allow keenable keyless search/i });
    expect(toggle).not.toBeChecked();
  });

  it("renders Keenable toggle checked when preference is true", async () => {
    const fetchMock = mockFetch({
      "/api/secrets": { ok: true, body: [] },
      "/api/preferences/broker": { ok: true, body: { keenable_public_fallback: true } },
    });
    vi.stubGlobal("fetch", fetchMock);
    render(<SecretsApp windowId="win-1" />);
    await flush();
    await waitFor(() =>
      expect(screen.getByLabelText(/allow keenable keyless search/i)).toBeTruthy(),
    );
    const toggle = screen.getByRole("switch", { name: /allow keenable keyless search/i });
    expect(toggle).toBeChecked();
  });

  it("toggles Keenable preference and PUTs merged object", async () => {
    const putMock = vi.fn().mockResolvedValue({
      ok: true,
      status: 200,
      headers: { get: () => "application/json" },
      json: () => Promise.resolve({ ok: true }),
    });
    const fetchMock = mockFetch({
      "/api/secrets": { ok: true, body: [] },
      "/api/preferences/broker": { ok: true, body: { other_key: "value" } },
    });
    // Override PUT to capture the call
    vi.stubGlobal("fetch", vi.fn().mockImplementation((input: string, init?: RequestInit) => {
      if (init?.method === "PUT" && input === "/api/preferences/broker") {
        return putMock(input, init);
      }
      return fetchMock(input, init);
    }));

    render(<SecretsApp windowId="win-1" />);
    await flush();
    await waitFor(() =>
      expect(screen.getByLabelText(/allow keenable keyless search/i)).toBeTruthy(),
    );

    const toggle = screen.getByRole("switch", { name: /allow keenable keyless search/i });
    expect(toggle).not.toBeChecked();

    fireEvent.click(toggle);
    await flush();

    expect(putMock).toHaveBeenCalled();
    const putCall = putMock.mock.calls[0];
    const body = JSON.parse(putCall[1]?.body as string);
    expect(body).toEqual({ other_key: "value", keenable_public_fallback: true });
  });

  it("keeps switch checked and shows alert when PUT returns 403", async () => {
    const putMock = vi.fn().mockResolvedValue({
      ok: false,
      status: 403,
      headers: { get: () => "application/json" },
      json: () => Promise.resolve({ error: "forbidden" }),
    });
    const fetchMock = mockFetch({
      "/api/secrets": { ok: true, body: [] },
      "/api/preferences/broker": { ok: true, body: { keenable_public_fallback: true } },
    });
    vi.stubGlobal("fetch", vi.fn().mockImplementation((input: string, init?: RequestInit) => {
      if (init?.method === "PUT" && input === "/api/preferences/broker") {
        return putMock(input, init);
      }
      return fetchMock(input, init);
    }));

    render(<SecretsApp windowId="win-1" />);
    await flush();
    await waitFor(() =>
      expect(screen.getByLabelText(/allow keenable keyless search/i)).toBeTruthy(),
    );

    const toggle = screen.getByRole("switch", { name: /allow keenable keyless search/i });
    expect(toggle).toBeChecked();

    fireEvent.click(toggle);
    await flush();

    // Switch should remain checked (previous value)
    expect(toggle).toBeChecked();
    // Alert should be shown
    expect(screen.getByRole("alert")).toBeTruthy();
  });

  it("does not send PUT when merge GET fails", async () => {
    const putMock = vi.fn().mockResolvedValue({
      ok: true,
      status: 200,
      headers: { get: () => "application/json" },
      json: () => Promise.resolve({ ok: true }),
    });
    const fetchMock = mockFetch({
      "/api/secrets": { ok: true, body: [] },
      "/api/preferences/broker": { ok: true, body: { keenable_public_fallback: false, other_key: "value" } },
    });
    // Override GET for the merge to return 500
    vi.stubGlobal("fetch", vi.fn().mockImplementation((input: string, init?: RequestInit) => {
      if (init?.method === "PUT" && input === "/api/preferences/broker") {
        return putMock(input, init);
      }
      if (input === "/api/preferences/broker" && !init?.method) {
        // This is the merge GET - return 500
        return Promise.resolve({
          ok: false,
          status: 500,
          headers: { get: () => "application/json" },
          json: () => Promise.resolve({ error: "server error" }),
        });
      }
      return fetchMock(input, init);
    }));

    render(<SecretsApp windowId="win-1" />);
    await flush();
    await waitFor(() =>
      expect(screen.getByLabelText(/allow keenable keyless search/i)).toBeTruthy(),
    );

    const toggle = screen.getByRole("switch", { name: /allow keenable keyless search/i });
    expect(toggle).not.toBeChecked();

    fireEvent.click(toggle);
    await flush();

    // PUT should not have been called
    expect(putMock).not.toHaveBeenCalled();
    // Alert should be shown
    expect(screen.getByRole("alert")).toBeTruthy();
  });
});
