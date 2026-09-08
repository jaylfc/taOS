import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { describe, it, expect, vi, afterEach, beforeEach } from "vitest";
import { LibraryApp } from "./LibraryApp";
import type { LibraryJob } from "../lib/library";

const MOCK_KNOWLEDGE_ITEMS = [
  {
    id: "lib-item-1",
    title: "YouTube Video",
    source_type: "youtube",
    source_url: "https://youtube.com/watch?v=1",
    source_id: "yt-1",
    author: "YT Author",
    summary: "",
    content: "",
    media_path: null,
    thumbnail: null,
    categories: [],
    tags: [],
    metadata: {},
    status: "ready",
    monitor: { current_interval: 0, frequency: 0, decay_rate: 0, pinned: false, last_poll: null, last_hash: "" },
    created_at: 1700007200,
    updated_at: 1700007200,
  },
];

const MOCK_JOBS: LibraryJob[] = [
  {
    id: "job-active-1",
    item_id: "lib-item-1",
    stage: "metadata",
    state: "processing",
    error: "",
    created_at: 1700007200,
    updated_at: 1700007200,
  },
  {
    id: "job-failed-1",
    item_id: "lib-item-1",
    stage: "transcript",
    state: "error",
    error: "Subtitles not available on video",
    created_at: 1700007100,
    updated_at: 1700007100,
  },
];

function jsonResponse(obj: unknown) {
  return Promise.resolve({
    ok: true,
    status: 200,
    headers: new Map([["content-type", "application/json"]]),
    json: () => Promise.resolve(obj),
  } as Response);
}

function makeFetchMock(getJobs: () => LibraryJob[]) {
  const retryJobs: string[] = [];
  const fetchMock = vi.fn((input: RequestInfo | URL, init?: RequestInit) => {
    const url = String(input);
    const method = (init?.method ?? "GET").toUpperCase();

    if (url === "/api/library/jobs" && method === "GET") {
      return jsonResponse({ jobs: getJobs() });
    }
    if (method === "POST" && /^\/api\/library\/jobs\/[^/]+\/retry$/.test(url)) {
      const jobId = url.split("/")[4];
      retryJobs.push(jobId);
      return jsonResponse({ status: "retried", job_id: jobId });
    }

    if (url.startsWith("/api/knowledge/items")) {
      return jsonResponse({ items: MOCK_KNOWLEDGE_ITEMS, count: MOCK_KNOWLEDGE_ITEMS.length });
    }
    if (url === "/api/agents") {
      return jsonResponse([]);
    }
    if (url === "/api/knowledge/subscriptions") {
      return jsonResponse({ subscriptions: [] });
    }
    if (url === "/api/knowledge/rules") {
      return jsonResponse({ rules: [] });
    }

    return Promise.resolve({
      ok: false,
      status: 404,
      headers: new Map([["content-type", "application/json"]]),
      json: () => Promise.resolve({}),
    } as Response);
  });
  return { fetchMock, getRetryJobs: () => retryJobs };
}

describe("LibraryApp queue view", () => {
  afterEach(() => {
    vi.unstubAllGlobals();
    vi.restoreAllMocks();
  });

  it("renders the queue header and lists jobs with stage and error text", async () => {
    const { fetchMock } = makeFetchMock(() => MOCK_JOBS);
    vi.stubGlobal("fetch", fetchMock);
    render(<LibraryApp windowId="test-win" />);

    fireEvent.click(screen.getByRole("radio", { name: "queue" }));

    await waitFor(() => screen.getByText("Ingest Queue"), { timeout: 5000 });
    expect(screen.getByText("Ingest Queue")).toBeInTheDocument();

    // Stage column renders the job's stage
    expect(screen.getByText("metadata")).toBeInTheDocument();
    expect(screen.getByText("transcript")).toBeInTheDocument();

    // State badges
    expect(screen.getByText("processing")).toBeInTheDocument();
    expect(screen.getByText("error")).toBeInTheDocument();

    // Error text is surfaced for failed jobs
    expect(screen.getByText("Subtitles not available on video")).toBeInTheDocument();
  });

  it("POSTs to retry a failed job when its retry button is clicked", async () => {
    const { fetchMock, getRetryJobs } = makeFetchMock(() => MOCK_JOBS);
    vi.stubGlobal("fetch", fetchMock);
    render(<LibraryApp windowId="test-win" />);

    fireEvent.click(screen.getByRole("radio", { name: "queue" }));

    await waitFor(() => screen.getByRole("button", { name: "Retry job job-failed-1" }), { timeout: 5000 });
    fireEvent.click(screen.getByRole("button", { name: "Retry job job-failed-1" }));

    await waitFor(() => {
      expect(fetchMock).toHaveBeenCalledWith(
        "/api/library/jobs/job-failed-1/retry",
        expect.objectContaining({ method: "POST" }),
      );
    }, { timeout: 5000 });
    expect(getRetryJobs()).toEqual(["job-failed-1"]);
  });

  it("does not show a retry button for active jobs", async () => {
    const { fetchMock } = makeFetchMock(() => MOCK_JOBS);
    vi.stubGlobal("fetch", fetchMock);
    render(<LibraryApp windowId="test-win" />);

    fireEvent.click(screen.getByRole("radio", { name: "queue" }));

    await waitFor(() => screen.getByText("Ingest Queue"), { timeout: 5000 });
    expect(screen.queryByRole("button", { name: /Retry job job-active-1/ })).toBeNull();
    expect(screen.getByRole("button", { name: "Retry job job-failed-1" })).toBeInTheDocument();
  });

  it("shows an empty state when the queue has no active or failed jobs", async () => {
    const { fetchMock } = makeFetchMock(() => []);
    vi.stubGlobal("fetch", fetchMock);
    render(<LibraryApp windowId="test-win" />);

    fireEvent.click(screen.getByRole("radio", { name: "queue" }));

    await waitFor(() => screen.getByText("No active or failed jobs"), { timeout: 5000 });
  });

  it("renders an unavailable state when the jobs endpoint returns 404", async () => {
    const fetchMock = vi.fn((input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      const method = (init?.method ?? "GET").toUpperCase();

      if (url === "/api/library/jobs" && method === "GET") {
        return Promise.resolve({
          ok: false,
          status: 404,
          headers: new Map([["content-type", "application/json"]]),
          json: () => Promise.resolve({}),
        } as Response);
      }
      if (method === "POST" && /^\/api\/library\/jobs\/[^/]+\/retry$/.test(url)) {
        return Promise.resolve({
          ok: false,
          status: 404,
          headers: new Map([["content-type", "application/json"]]),
          json: () => Promise.resolve({}),
        } as Response);
      }

      if (url.startsWith("/api/knowledge/items")) {
        return Promise.resolve({
          ok: true,
          status: 200,
          headers: new Map([["content-type", "application/json"]]),
          json: () => Promise.resolve({ items: [], count: 0 }),
        } as Response);
      }
      if (url === "/api/agents") {
        return Promise.resolve({
          ok: true,
          status: 200,
          headers: new Map([["content-type", "application/json"]]),
          json: () => Promise.resolve([]),
        } as Response);
      }
      if (url === "/api/knowledge/subscriptions") {
        return Promise.resolve({
          ok: true,
          status: 200,
          headers: new Map([["content-type", "application/json"]]),
          json: () => Promise.resolve({ subscriptions: [] }),
        } as Response);
      }
      if (url === "/api/knowledge/rules") {
        return Promise.resolve({
          ok: true,
          status: 200,
          headers: new Map([["content-type", "application/json"]]),
          json: () => Promise.resolve({ rules: [] }),
        } as Response);
      }

      return Promise.resolve({
        ok: false,
        status: 404,
        headers: new Map([["content-type", "application/json"]]),
        json: () => Promise.resolve({}),
      } as Response);
    });

    vi.stubGlobal("fetch", fetchMock);
    render(<LibraryApp windowId="test-win" />);

    fireEvent.click(screen.getByRole("radio", { name: "queue" }));

    await waitFor(() => screen.getByText("Queue unavailable"), { timeout: 5000 });
    expect(screen.queryByText("No active or failed jobs")).not.toBeInTheDocument();
  });
});

describe("LibraryApp queue polling", () => {
  beforeEach(() => {
    vi.useFakeTimers();
  });

  afterEach(() => {
    vi.useRealTimers();
    vi.unstubAllGlobals();
    vi.restoreAllMocks();
  });

  const countJobFetches = (fetchMock: ReturnType<typeof vi.fn>) =>
    fetchMock.mock.calls.filter((c) => String(c[0]) === "/api/library/jobs").length;

  async function activateQueue(fetchMock: ReturnType<typeof makeFetchMock>["fetchMock"]) {
    render(<LibraryApp windowId="test-win" />);
    fireEvent.click(screen.getByRole("radio", { name: "queue" }));
    // Flush mount effects + the initial queue fetch + interval arming.
    await vi.advanceTimersByTimeAsync(50);
    await vi.advanceTimersByTimeAsync(0);
    return fetchMock;
  }

  it("starts polling every 3s while jobs are active", async () => {
    const activeJobs: LibraryJob[] = [
      {
        id: "job-active-1",
        item_id: "lib-item-1",
        stage: "metadata",
        state: "processing",
        error: "",
        created_at: 1700007200,
        updated_at: 1700007200,
      },
    ];
    const { fetchMock } = makeFetchMock(() => activeJobs);
    vi.stubGlobal("fetch", fetchMock);

    await activateQueue(fetchMock);

    expect(countJobFetches(fetchMock)).toBeGreaterThanOrEqual(1);
    const before = countJobFetches(fetchMock);

    // A poll tick fires at the 3s interval and refetches.
    await vi.advanceTimersByTimeAsync(3000);
    expect(countJobFetches(fetchMock)).toBeGreaterThanOrEqual(before + 1);

    // Another tick fires at the next 3s interval — polling continues while active.
    await vi.advanceTimersByTimeAsync(3000);
    expect(countJobFetches(fetchMock)).toBeGreaterThanOrEqual(before + 2);
  });

  it("stops polling once the queue goes idle", async () => {
    let currentJobs: LibraryJob[] = [
      {
        id: "job-active-1",
        item_id: "lib-item-1",
        stage: "metadata",
        state: "queued",
        error: "",
        created_at: 1700007200,
        updated_at: 1700007200,
      },
    ];
    const { fetchMock } = makeFetchMock(() => currentJobs);
    vi.stubGlobal("fetch", fetchMock);

    await activateQueue(fetchMock);

    // Let it poll a couple of times while still active.
    await vi.advanceTimersByTimeAsync(3000);
    await vi.advanceTimersByTimeAsync(3000);
    const activeCount = countJobFetches(fetchMock);
    expect(activeCount).toBeGreaterThanOrEqual(1);

    // Queue drains to idle (no queued/processing jobs) on the next poll.
    currentJobs = [];

    await vi.advanceTimersByTimeAsync(3000);
    const afterIdle = countJobFetches(fetchMock);
    expect(afterIdle).toBeGreaterThanOrEqual(activeCount + 1);

    // No further refetches once idle — polling stopped.
    await vi.advanceTimersByTimeAsync(3000);
    await vi.advanceTimersByTimeAsync(3000);
    expect(countJobFetches(fetchMock)).toBe(afterIdle);
  });

  it("does not start polling when there are no active jobs", async () => {
    const idleJobs: LibraryJob[] = [
      {
        id: "job-failed-1",
        item_id: "lib-item-1",
        stage: "transcript",
        state: "error",
        error: "boom",
        created_at: 1700007100,
        updated_at: 1700007100,
      },
    ];
    const { fetchMock } = makeFetchMock(() => idleJobs);
    vi.stubGlobal("fetch", fetchMock);

    await activateQueue(fetchMock);

    await vi.advanceTimersByTimeAsync(0);
    const initial = countJobFetches(fetchMock);
    expect(initial).toBeGreaterThanOrEqual(1); // initial load happens

    await vi.advanceTimersByTimeAsync(3000);
    await vi.advanceTimersByTimeAsync(3000);
    // No poll ticks fire when there are no active jobs.
    expect(countJobFetches(fetchMock)).toBe(initial);
  });
});
