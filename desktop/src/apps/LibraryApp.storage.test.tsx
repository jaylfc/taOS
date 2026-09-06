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
  {
    id: "lib-item-2",
    title: "Reddit Post",
    source_type: "reddit",
    source_url: "https://reddit.com/r/test",
    source_id: "rp-1",
    author: "Redditor",
    summary: "Summary 2",
    content: "Content 2",
    media_path: null,
    thumbnail: null,
    categories: [],
    tags: [],
    metadata: {},
    status: "ready",
    monitor: { current_interval: 0, frequency: 0, decay_rate: 0, pinned: false, last_poll: null, last_hash: "" },
    created_at: 1700003600,
    updated_at: 1700003600,
  },
  {
    id: "lib-item-3",
    title: "GitHub Repo",
    source_type: "github",
    source_url: "https://github.com/test/repo",
    source_id: "gh-1",
    author: "Dev",
    summary: "Summary 3",
    content: "Content 3",
    media_path: null,
    thumbnail: null,
    categories: [],
    tags: [],
    metadata: {},
    status: "processing",
    monitor: { current_interval: 0, frequency: 0, decay_rate: 0, pinned: false, last_poll: null, last_hash: "" },
    created_at: 1700007200,
    updated_at: 1700007200,
  },
];

const MOCK_AGENTS = [
  { name: "Agent 1", color: "#ff0000" },
  { name: "Agent 2", color: "#00ff00" },
];

function createFetchMock(overrides: Record<string, Promise<Response>> = {}) {
  return vi.fn((input: RequestInfo | URL) => {
    const url = String(input);
    if (url.startsWith("/api/knowledge/items")) {
      for (const [prefix, response] of Object.entries(overrides)) {
        if (url.startsWith(prefix)) return response;
      }
      return Promise.resolve({
        ok: true,
        status: 200,
        headers: new Map([["content-type", "application/json"]]),
        json: () => Promise.resolve({ items: MOCK_ITEMS, count: MOCK_ITEMS.length }),
      } as Response);
    }
    if (url === "/api/agents") {
      return Promise.resolve({
        ok: true,
        status: 200,
        headers: new Map([["content-type", "application/json"]]),
        json: () => Promise.resolve(MOCK_AGENTS),
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
    return Promise.resolve({
      ok: false,
      status: 404,
      headers: new Map([["content-type", "application/json"]]),
      json: () => Promise.resolve({}),
    } as Response);
  });
}

describe("LibraryApp storage view", () => {
  afterEach(() => {
    vi.unstubAllGlobals();
    vi.restoreAllMocks();
  });

  it("renders the view mode toggle and switches to storage view", async () => {
    vi.stubGlobal("fetch", createFetchMock() as unknown as typeof fetch);
    render(<LibraryApp windowId="test-win" />);

    await waitFor(() => screen.getByRole("radio", { name: "storage" }));
    fireEvent.click(screen.getByRole("radio", { name: "storage" }));

    await waitFor(() => screen.getByText("Storage Accounting"), { timeout: 5000 });
  });

  it("shows per-source totals sorted by bytes descending", async () => {
    vi.stubGlobal("fetch", createFetchMock() as unknown as typeof fetch);
    render(<LibraryApp windowId="test-win" />);

    await waitFor(() => screen.getByRole("radio", { name: "storage" }));
    fireEvent.click(screen.getByRole("radio", { name: "storage" }));

    await waitFor(() => screen.getByText("Storage Accounting"), { timeout: 5000 });
    expect(screen.getAllByText("YouTube").length).toBeGreaterThanOrEqual(1);
    expect(screen.getAllByText("Reddit").length).toBeGreaterThanOrEqual(1);
    expect(screen.getAllByText("GitHub").length).toBeGreaterThanOrEqual(1);
  });

  it("shows per-item rows sorted by bytes descending", async () => {
    vi.stubGlobal("fetch", createFetchMock() as unknown as typeof fetch);
    render(<LibraryApp windowId="test-win" />);

    await waitFor(() => screen.getByRole("radio", { name: "storage" }));
    fireEvent.click(screen.getByRole("radio", { name: "storage" }));

    await waitFor(() => screen.getByText("Storage Accounting"), { timeout: 5000 });
    expect(screen.getByText("YouTube Video")).toBeInTheDocument();
    expect(screen.getByText("Reddit Post")).toBeInTheDocument();
    expect(screen.getByText("GitHub Repo")).toBeInTheDocument();
  });

  it("does not show paused-at-cap when total is under cap", async () => {
    vi.stubGlobal("fetch", createFetchMock() as unknown as typeof fetch);
    render(<LibraryApp windowId="test-win" />);

    await waitFor(() => screen.getByRole("radio", { name: "storage" }));
    fireEvent.click(screen.getByRole("radio", { name: "storage" }));

    await waitFor(() => screen.getByText("Storage Accounting"), { timeout: 5000 });
    expect(screen.queryByText(/Paused at cap/)).toBeNull();
  });

  it("shows paused-at-cap warning when total exceeds cap", async () => {
    const manyItems = Array.from({ length: 60 }, (_, i) => ({
      ...MOCK_ITEMS[0],
      id: `overflow-${i}`,
      title: `Overflow Item ${i}`,
      source_type: "youtube",
      created_at: 1700000000 + i,
      updated_at: 1700000000 + i,
    }));

    const overrides: Record<string, Promise<Response>> = {
      "/api/knowledge/items": Promise.resolve({
        ok: true,
        status: 200,
        headers: new Map([["content-type", "application/json"]]),
        json: () => Promise.resolve({ items: manyItems, count: manyItems.length }),
      } as Response),
    };

    vi.stubGlobal("fetch", createFetchMock(overrides) as unknown as typeof fetch);
    render(<LibraryApp windowId="test-win" />);

    await waitFor(() => screen.getByRole("radio", { name: "storage" }));
    fireEvent.click(screen.getByRole("radio", { name: "storage" }));

    await waitFor(() => screen.getByText("Storage Accounting"), { timeout: 5000 });
    expect(screen.getByText(/Paused at cap/)).toBeInTheDocument();
  });
});
