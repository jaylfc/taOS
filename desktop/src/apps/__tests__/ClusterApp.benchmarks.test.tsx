import { render, screen, fireEvent, act } from "@testing-library/react";
import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { WorkerBenchmarksSection, formatBenchmarkValue } from "../ClusterApp.benchmarks";

function jsonResponse(status: number, body: unknown) {
  return {
    ok: status >= 200 && status < 300,
    status,
    json: async () => body,
  };
}

const LATEST = [
  {
    worker_id: "pi4",
    capability: "llm-chat",
    model: "qwen3-1.7b-q4",
    metric: "tokens_per_sec",
    value: 42.5,
    unit: "tok/s",
    status: "ok",
    measured_at: Date.now() / 1000 - 120,
    first_join: true,
  },
];

describe("formatBenchmarkValue", () => {
  it("renders the unit, rounds large numbers and never invents a value", () => {
    expect(formatBenchmarkValue(42.456, "tok/s")).toBe("42.46 tok/s");
    expect(formatBenchmarkValue(1234.5, "tok/s")).toBe("1235 tok/s");
    expect(formatBenchmarkValue(null, "tok/s")).toBe("\u2014");
    expect(formatBenchmarkValue(undefined, null)).toBe("\u2014");
    expect(formatBenchmarkValue(7, null)).toBe("7.00");
  });
});

describe("WorkerBenchmarksSection", () => {
  beforeEach(() => {
    globalThis.fetch = vi.fn().mockResolvedValue(
      jsonResponse(200, { latest: LATEST, history: [LATEST[0]], pending: null }),
    ) as unknown as typeof fetch;
  });

  afterEach(() => {
    vi.restoreAllMocks();
  });

  it("shows the latest measurement per capability with its run count", async () => {
    render(<WorkerBenchmarksSection workerName="pi4" />);

    expect(await screen.findByText("tokens_per_sec")).toBeInTheDocument();
    expect(screen.getByText("qwen3-1.7b-q4")).toBeInTheDocument();
    expect(screen.getByText("42.50 tok/s")).toBeInTheDocument();
    expect(screen.getByText(/first run/)).toBeInTheDocument();
    expect(screen.getByText(/1 recorded measurement/)).toBeInTheDocument();
    // Regression (#3240 review): an escape sequence written directly in JSX
    // text renders literally, so the capability separator must come from a JS
    // expression.
    expect(screen.queryByText(/\\u00b7/)).not.toBeInTheDocument();
    expect(screen.getByText(/\u00b7 llm-chat/)).toBeInTheDocument();
  });

  it("says so when the worker has no results yet", async () => {
    globalThis.fetch = vi.fn().mockResolvedValue(
      jsonResponse(200, { latest: [], history: [], pending: null }),
    ) as unknown as typeof fetch;

    render(<WorkerBenchmarksSection workerName="pi4" />);

    expect(await screen.findByText(/No benchmark results yet/)).toBeInTheDocument();
  });

  it("queues a manual run and reports it as queued, not running", async () => {
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce(jsonResponse(200, { latest: LATEST, history: [LATEST[0]], pending: null }))
      .mockResolvedValueOnce(jsonResponse(202, { status: "queued", worker_id: "pi4" }))
      .mockResolvedValue(
        jsonResponse(200, {
          latest: LATEST,
          history: [LATEST[0]],
          pending: { worker_id: "pi4", requested_at: Date.now() / 1000 },
        }),
      );
    globalThis.fetch = fetchMock as unknown as typeof fetch;

    render(<WorkerBenchmarksSection workerName="pi4" />);
    const button = await screen.findByRole("button", { name: /Re-run benchmarks on pi4/i });

    await act(async () => {
      fireEvent.click(button);
    });

    expect(await screen.findByText(/Benchmark queued/)).toBeInTheDocument();
    const [url, init] = fetchMock.mock.calls[1] as [string, RequestInit];
    expect(url).toBe("/api/workers/pi4/benchmark");
    expect(init.method).toBe("POST");
    expect(JSON.parse(String(init.body))).toEqual({ force: false });
  });

  it("replaces an already-queued run instead of tripping the 409 guard", async () => {
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce(
        jsonResponse(200, {
          latest: LATEST,
          history: [LATEST[0]],
          pending: { worker_id: "pi4", requested_at: Date.now() / 1000 },
        }),
      )
      .mockResolvedValue(jsonResponse(202, { status: "queued", worker_id: "pi4" }));
    globalThis.fetch = fetchMock as unknown as typeof fetch;

    render(<WorkerBenchmarksSection workerName="pi4" />);
    const button = await screen.findByRole("button", { name: /Re-run benchmarks on pi4/i });
    await act(async () => {
      fireEvent.click(button);
    });

    const [, init] = fetchMock.mock.calls[1] as [string, RequestInit];
    expect(JSON.parse(String(init.body))).toEqual({ force: true });
  });

  it("stops calling a queued run imminent once it is old", async () => {
    globalThis.fetch = vi.fn().mockResolvedValue(
      jsonResponse(200, {
        latest: LATEST,
        history: [LATEST[0]],
        pending: { worker_id: "pi4", requested_at: Date.now() / 1000 - 900 },
      }),
    ) as unknown as typeof fetch;

    render(<WorkerBenchmarksSection workerName="pi4" />);

    expect(await screen.findByText(/has not picked it up/)).toBeInTheDocument();
  });

  it("surfaces a refused trigger", async () => {
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce(jsonResponse(200, { latest: LATEST, history: [LATEST[0]], pending: null }))
      .mockResolvedValueOnce(jsonResponse(409, { error: "Worker 'pi4' is not online (status=offline)" }));
    globalThis.fetch = fetchMock as unknown as typeof fetch;

    render(<WorkerBenchmarksSection workerName="pi4" />);
    const button = await screen.findByRole("button", { name: /Re-run benchmarks on pi4/i });
    await act(async () => {
      fireEvent.click(button);
    });

    expect(await screen.findByRole("alert")).toHaveTextContent(/not online/);
  });
});
