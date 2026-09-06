import { render, screen, fireEvent, act, waitFor } from "@testing-library/react";
import { describe, it, expect, vi, beforeEach } from "vitest";
import { FeedbackApp } from "./FeedbackApp";

/* ------------------------------------------------------------------ */
/*  Fetch mock helpers                                                 */
/* ------------------------------------------------------------------ */

function mockFetch(responses: Record<string, { ok: boolean; status?: number; body: unknown }>) {
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

/* ------------------------------------------------------------------ */
/*  Tests                                                              */
/* ------------------------------------------------------------------ */

describe("FeedbackApp", () => {
  beforeEach(() => {
    vi.restoreAllMocks();
  });

  it("renders the form with Bug Report selected by default", async () => {
    vi.stubGlobal(
      "fetch",
      mockFetch({ "GET /api/feedback": { ok: true, body: [] } }),
    );
    render(<FeedbackApp windowId="w1" />);
    await flush();

    expect(screen.getByRole("group", { name: /feedback type/i })).toBeTruthy();
    expect(screen.getByRole("button", { name: /bug report/i })).toBeTruthy();
    expect(screen.getByRole("button", { name: /feature request/i })).toBeTruthy();
    expect(screen.getByLabelText(/title/i)).toBeTruthy();
    expect(screen.getByRole("button", { name: /submit/i })).toBeTruthy();
  });

  it("toggles between bug and feature types", async () => {
    vi.stubGlobal(
      "fetch",
      mockFetch({ "GET /api/feedback": { ok: true, body: [] } }),
    );
    render(<FeedbackApp windowId="w1" />);
    await flush();

    const bugBtn = screen.getByRole("button", { name: /bug report/i });
    const featureBtn = screen.getByRole("button", { name: /feature request/i });

    // Bug is default
    expect(bugBtn.getAttribute("aria-pressed")).toBe("true");
    expect(featureBtn.getAttribute("aria-pressed")).toBe("false");

    // Click feature
    fireEvent.click(featureBtn);
    expect(bugBtn.getAttribute("aria-pressed")).toBe("false");
    expect(featureBtn.getAttribute("aria-pressed")).toBe("true");
  });

  it("shows validation error when title is empty and submit is clicked", async () => {
    vi.stubGlobal(
      "fetch",
      mockFetch({ "GET /api/feedback": { ok: true, body: [] } }),
    );
    render(<FeedbackApp windowId="w1" />);
    await flush();

    fireEvent.click(screen.getByRole("button", { name: /submit/i }));
    await flush();

    expect(screen.getByRole("alert").textContent).toMatch(/title is required/i);
  });

  it("posts to /api/feedback on valid submit and shows success", async () => {
    const fetchMock = mockFetch({
      "GET /api/feedback": { ok: true, body: [] },
      "POST /api/feedback": {
        ok: true,
        status: 201,
        body: {
          id: "abc",
          type: "bug",
          title: "Login broken",
          body: "",
          app: "",
          created_at: new Date().toISOString(),
          has_screenshot: false,
        },
      },
    });
    vi.stubGlobal("fetch", fetchMock);
    render(<FeedbackApp windowId="w1" />);
    await flush();

    fireEvent.change(screen.getByLabelText(/title/i), {
      target: { value: "Login broken" },
    });
    fireEvent.click(screen.getByRole("button", { name: /submit/i }));
    await flush();

    // Should show success message
    await waitFor(() =>
      expect(screen.getByRole("status").textContent).toMatch(/thanks for the feedback/i),
    );

    // POST should have been called with the right body
    const postCall = fetchMock.mock.calls.find(
      (c) => (c[1] as RequestInit)?.method === "POST",
    );
    expect(postCall).toBeTruthy();
    const sentBody = JSON.parse((postCall![1] as RequestInit).body as string);
    expect(sentBody.type).toBe("bug");
    expect(sentBody.title).toBe("Login broken");
  });

  it("shows error message when the server returns a failure", async () => {
    const fetchMock = mockFetch({
      "GET /api/feedback": { ok: true, body: [] },
      "POST /api/feedback": {
        ok: false,
        status: 422,
        body: { detail: "title must not be empty" },
      },
    });
    vi.stubGlobal("fetch", fetchMock);
    render(<FeedbackApp windowId="w1" />);
    await flush();

    fireEvent.change(screen.getByLabelText(/title/i), {
      target: { value: "  x  " },
    });
    fireEvent.click(screen.getByRole("button", { name: /submit/i }));
    await flush();

    await waitFor(() =>
      expect(screen.getByRole("alert").textContent).toMatch(/title must not be empty/i),
    );
  });

  it("renders past submissions returned by GET /api/feedback", async () => {
    const items = [
      {
        id: "1",
        type: "bug",
        title: "Dark mode flicker",
        body: "",
        app: "",
        created_at: new Date(Date.now() - 3600_000).toISOString(),
        has_screenshot: true,
      },
      {
        id: "2",
        type: "feature",
        title: "Export to PDF",
        body: "",
        app: "",
        created_at: new Date(Date.now() - 86400_000 * 2).toISOString(),
        has_screenshot: false,
      },
    ];
    vi.stubGlobal(
      "fetch",
      mockFetch({ "GET /api/feedback": { ok: true, body: items } }),
    );
    render(<FeedbackApp windowId="w1" />);
    await flush();

    await waitFor(() => {
      expect(screen.getByText("Dark mode flicker")).toBeTruthy();
      expect(screen.getByText("Export to PDF")).toBeTruthy();
    });
    // Screenshot indicator for item 1
    expect(screen.getByText(/has screenshot/i)).toBeTruthy();
  });
});
