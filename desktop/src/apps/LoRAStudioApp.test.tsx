import { render, screen, fireEvent, act } from "@testing-library/react";
import { describe, it, expect, vi, beforeEach } from "vitest";
import { LoRAStudioApp } from "./LoRAStudioApp";

/* ------------------------------------------------------------------ */
/*  Fetch mock helpers                                                 */
/* ------------------------------------------------------------------ */

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
      headers: { get: (name: string) => (name === "content-type" ? "application/json" : null) },
      json: () => Promise.resolve(hit.body),
    });
  });
}

async function flush() {
  await act(async () => {
    await new Promise((r) => setTimeout(r, 0));
  });
}

function renderApp() {
  return render(<LoRAStudioApp windowId="test-window" />);
}

const READY_LORA = {
  id: "lora-detail-tuner",
  source_url: "https://civitai.com/models/2851174",
  provider: "civitai",
  civitai_model_id: 2851174,
  civitai_version_id: 3200000,
  name: "Detail Tuner",
  description: "Adds fine detail to renders.",
  creator: "someartist",
  base_model: "SDXL 1.0",
  trigger_words: ["detailtuner"],
  tags: ["detail", "realism"],
  nsfw: false,
  file_path: "/data/models/loras/detail-tuner/detail-tuner.safetensors",
  file_name: "detail-tuner.safetensors",
  sha256: "abc123",
  bytes: 150_000_000,
  preview_paths: ["previews/00.jpg"],
  meta_json: { type: "LORA" },
  status: "ready",
  error: null,
  created_at: 1000,
  updated_at: 1000,
};

const FAILED_LORA = {
  ...READY_LORA,
  id: "lora-geo-blocked",
  name: "Geo Blocked LoRA",
  status: "failed",
  error: "Civitai geo-blocked this request (HTTP 451). Set lora_ingest_proxy_url…",
  preview_paths: [],
};

/* ------------------------------------------------------------------ */
/*  Tests                                                              */
/* ------------------------------------------------------------------ */

describe("LoRAStudioApp", () => {
  beforeEach(() => {
    vi.restoreAllMocks();
  });

  it("renders cards from GET /api/loras", async () => {
    vi.stubGlobal(
      "fetch",
      mockFetch({ "GET /api/loras": { ok: true, body: { loras: [READY_LORA] } } }),
    );
    renderApp();
    await flush();

    expect(screen.getByText("LoRA Studio")).toBeTruthy();
    expect(
      screen.getByRole("button", { name: "Open LoRA: Detail Tuner" }),
    ).toBeTruthy();
    expect(screen.getByText("SDXL 1.0")).toBeTruthy();
  });

  it("shows an empty state when there are no LoRAs", async () => {
    vi.stubGlobal("fetch", mockFetch({ "GET /api/loras": { ok: true, body: { loras: [] } } }));
    renderApp();
    await flush();

    expect(screen.getByText("No LoRAs yet")).toBeTruthy();
  });

  it("posts Add-by-URL as form-encoded, not JSON", async () => {
    const fetchMock = mockFetch({
      "GET /api/loras": { ok: true, body: { loras: [] } },
      "POST /api/loras/ingest": {
        ok: true,
        body: { ...READY_LORA, id: "lora-new", name: "New LoRA", status: "pending" },
      },
    });
    vi.stubGlobal("fetch", fetchMock);
    renderApp();
    await flush();

    fireEvent.change(screen.getByLabelText("Civitai model URL"), {
      target: { value: "https://civitai.com/models/2851174" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Add LoRA by URL" }));
    await flush();

    const call = fetchMock.mock.calls.find(
      ([url]: [string]) => url === "/api/loras/ingest",
    );
    expect(call).toBeTruthy();
    const [, init] = call as [string, RequestInit];
    expect(init.method).toBe("POST");
    // Form-encoded body, not JSON: a URLSearchParams body, and no
    // application/json content-type header set by the caller.
    expect(init.body).toBeInstanceOf(URLSearchParams);
    expect((init.body as URLSearchParams).toString()).toBe(
      `url=${encodeURIComponent("https://civitai.com/models/2851174")}`,
    );
    const headers = new Headers(init.headers);
    expect(headers.get("Content-Type")).not.toBe("application/json");

    // The returned pending row is shown immediately (optimistic card).
    expect(screen.getByRole("button", { name: "Open LoRA: New LoRA" })).toBeTruthy();
  });

  it("renders the failure reason on a failed row, including the geo-block message", async () => {
    vi.stubGlobal(
      "fetch",
      mockFetch({ "GET /api/loras": { ok: true, body: { loras: [FAILED_LORA] } } }),
    );
    renderApp();
    await flush();

    expect(screen.getByText(/geo-blocked this request \(HTTP 451\)/)).toBeTruthy();

    fireEvent.click(screen.getByRole("button", { name: "Open LoRA: Geo Blocked LoRA" }));
    await flush();

    // The same reason also appears in the detail panel's alert.
    const alerts = screen.getAllByRole("alert");
    expect(
      alerts.some((el) => /geo-blocked this request \(HTTP 451\)/.test(el.textContent ?? "")),
    ).toBe(true);
  });

  it("polls the list while a row is pending", async () => {
    vi.useFakeTimers();
    const fetchMock = mockFetch({
      "GET /api/loras": { ok: true, body: { loras: [{ ...READY_LORA, status: "pending" }] } },
    });
    vi.stubGlobal("fetch", fetchMock);
    renderApp();
    await act(async () => {
      await Promise.resolve();
    });

    const initialCalls = fetchMock.mock.calls.filter(
      ([url]: [string]) => url === "/api/loras",
    ).length;
    expect(initialCalls).toBeGreaterThan(0);

    await act(async () => {
      vi.advanceTimersByTime(2000);
      await Promise.resolve();
    });
    const callsAfter = fetchMock.mock.calls.filter(([url]: [string]) => url === "/api/loras").length;
    expect(callsAfter).toBeGreaterThan(initialCalls);

    vi.useRealTimers();
  });

  it("stops polling once nothing is pending or downloading", async () => {
    vi.useFakeTimers();
    const fetchMock = mockFetch({
      "GET /api/loras": { ok: true, body: { loras: [READY_LORA] } },
    });
    vi.stubGlobal("fetch", fetchMock);
    renderApp();
    await act(async () => {
      await Promise.resolve();
    });

    const initialCalls = fetchMock.mock.calls.filter(
      ([url]: [string]) => url === "/api/loras",
    ).length;

    await act(async () => {
      vi.advanceTimersByTime(10000);
      await Promise.resolve();
    });
    const callsAfter = fetchMock.mock.calls.filter(([url]: [string]) => url === "/api/loras").length;
    expect(callsAfter).toBe(initialCalls);

    vi.useRealTimers();
  });
  it("surfaces a list failure instead of the empty state", async () => {
    // A failed GET must not look identical to an empty archive: fetchJson's
    // silent fallback made "server is down" render as "No LoRAs yet".
    vi.stubGlobal(
      "fetch",
      mockFetch({ "GET /api/loras": { ok: false, status: 500, body: {} } }),
    );
    renderApp();
    await flush();

    expect(screen.getByText(/Failed to load LoRAs/i)).toBeTruthy();
    expect(screen.queryByText("No LoRAs yet")).toBeNull();
  });

  it("keeps the card's details when retry returns only {id, status}", async () => {
    vi.stubGlobal(
      "fetch",
      mockFetch({
        "GET /api/loras": { ok: true, body: { loras: [FAILED_LORA] } },
        "POST /api/loras/lora-geo-blocked/retry": {
          ok: true,
          status: 202,
          body: { id: "lora-geo-blocked", status: "pending" },
        },
      }),
    );
    renderApp();
    await flush();

    fireEvent.click(screen.getByLabelText("Open LoRA: Geo Blocked LoRA"));
    fireEvent.click(screen.getByLabelText("Retry ingest"));
    await flush();

    // The retry response carries no name/tags/previews -- replacing the row
    // with it would blank the card until the next poll.
    expect(screen.getAllByText("Geo Blocked LoRA").length).toBeGreaterThan(0);
  });
});
