import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { describe, it, expect, vi, afterEach } from "vitest";
import { LibraryApp } from "./LibraryApp";

const MOCK_ITEMS = [
  {
    id: "lib-item-1",
    title: "YouTube Video",
    source_type: "youtube",
    source_url: "https://youtube.com/watch?v=1",
    source_id: "yt-1",
    author: "YT Author",
    summary: "Summary 1",
    content: "Content 1",
    media_path: null,
    thumbnail: null,
    categories: [],
    tags: [],
    metadata: {},
    status: "ready",
    monitor: { current_interval: 0, frequency: 0, decay_rate: 0, pinned: false, last_poll: null, last_hash: "" },
    created_at: 1700000000,
    updated_at: 1700000000,
  },
];

const MOCK_USAGE = {
  total_count: 3,
  total_bytes: 6000,
  by_kind: {},
  by_source: {
    youtube: { count: 1, bytes: 3000 },
    reddit: { count: 1, bytes: 2000 },
    github: { count: 1, bytes: 1000 },
  },
  sources: [
    { source_type: "youtube", bytes: 3000, item_count: 1 },
    { source_type: "reddit", bytes: 2000, item_count: 1 },
    { source_type: "github", bytes: 1000, item_count: 1 },
  ],
  items: [
    { id: "lib-item-1", title: "YouTube Video", source_type: "youtube", bytes: 3000 },
    { id: "lib-item-2", title: "Reddit Post", source_type: "reddit", bytes: 2000 },
    { id: "lib-item-3", title: "GitHub Repo", source_type: "github", bytes: 1000 },
  ],
  cap_bytes: 50 * 1024 * 1024 * 1024,
  paused_at_cap: false,
};

function jsonResponse(body: unknown, status = 200) {
  return Promise.resolve({
    ok: status >= 200 && status < 300,
    status,
    headers: new Map([["content-type", "application/json"]]),
    json: () => Promise.resolve(body),
  } as Response);
}

function createFetchMock(usage: Record<string, unknown> = MOCK_USAGE) {
  return vi.fn((input: RequestInfo | URL) => {
    const url = String(input);
    if (url.startsWith("/api/knowledge/items")) {
      return jsonResponse({ items: MOCK_ITEMS, count: MOCK_ITEMS.length });
    }
    if (url === "/api/agents") {
      return jsonResponse([{ name: "Agent 1", color: "#ff0000" }]);
    }
    if (url === "/api/knowledge/subscriptions") {
      return jsonResponse({ subscriptions: [] });
    }
    if (url === "/api/library/usage") {
      return jsonResponse(usage);
    }
    if (url === "/api/library/settings") {
      return jsonResponse({
        preferred_quality: "720",
        storage_cap_bytes: 50 * 1024 * 1024 * 1024,
      });
    }
    if (url === "/api/library/rules") {
      return jsonResponse({ rules: [], count: 0 });
    }
    return jsonResponse({}, 404);
  });
}

async function openStorage() {
  await waitFor(() => screen.getByRole("radio", { name: "storage" }));
  fireEvent.click(screen.getByRole("radio", { name: "storage" }));
  await waitFor(() => screen.getByText("Storage Accounting"), { timeout: 5000 });
}

describe("LibraryApp storage view", () => {
  afterEach(() => {
    vi.unstubAllGlobals();
    vi.restoreAllMocks();
  });

  it("renders the view mode toggle and switches to storage view", async () => {
    vi.stubGlobal("fetch", createFetchMock() as unknown as typeof fetch);
    render(<LibraryApp windowId="test-win" />);
    await openStorage();
  });

  it("shows per-source totals from the usage API", async () => {
    vi.stubGlobal("fetch", createFetchMock() as unknown as typeof fetch);
    render(<LibraryApp windowId="test-win" />);
    await openStorage();
    await waitFor(() => screen.getAllByText("YouTube"), { timeout: 5000 });
    expect(screen.getAllByText("YouTube").length).toBeGreaterThanOrEqual(1);
    expect(screen.getAllByText("Reddit").length).toBeGreaterThanOrEqual(1);
    expect(screen.getAllByText("GitHub").length).toBeGreaterThanOrEqual(1);
  });

  it("shows per-item rows from the usage API", async () => {
    vi.stubGlobal("fetch", createFetchMock() as unknown as typeof fetch);
    render(<LibraryApp windowId="test-win" />);
    await openStorage();
    await waitFor(() => screen.getByText("Reddit Post"), { timeout: 5000 });
    expect(screen.getByText("YouTube Video")).toBeInTheDocument();
    expect(screen.getByText("GitHub Repo")).toBeInTheDocument();
  });

  it("does not show paused-at-cap when the usage API says not paused", async () => {
    vi.stubGlobal("fetch", createFetchMock() as unknown as typeof fetch);
    render(<LibraryApp windowId="test-win" />);
    await openStorage();
    await waitFor(() => screen.getByText("Reddit Post"), { timeout: 5000 });
    expect(screen.queryByText(/Paused at cap/)).toBeNull();
  });

  it("shows paused-at-cap warning when the usage API reports paused", async () => {
    const usage = { ...MOCK_USAGE, cap_bytes: 5000, paused_at_cap: true };
    vi.stubGlobal("fetch", createFetchMock(usage) as unknown as typeof fetch);
    render(<LibraryApp windowId="test-win" />);
    await openStorage();
    await waitFor(() => screen.getByText(/Paused at cap/), { timeout: 5000 });
  });
});
