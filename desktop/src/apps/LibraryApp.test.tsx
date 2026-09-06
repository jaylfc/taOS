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
    categories: ["video"],
    tags: [],
    metadata: {},
    status: "ready",
    monitor: { current_interval: 3600, frequency: 3600, decay_rate: 0, pinned: false, last_poll: 1700000000, last_hash: "" },
    created_at: 1700007200,
    updated_at: 1700007300,
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
    categories: ["social"],
    tags: [],
    metadata: {},
    status: "ready",
    monitor: { current_interval: 0, frequency: 0, decay_rate: 0, pinned: false, last_poll: null, last_hash: "" },
    created_at: 1700003600,
    updated_at: 1700007200,
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
    created_at: 1700000000,
    updated_at: 1700000000,
  },
];

const MOCK_AGENTS = [
  { name: "Agent 1", color: "#ff0000" },
  { name: "Agent 2", color: "#00ff00" },
];

const MOCK_SNAPSHOTS = [
  {
    id: 1,
    item_id: "lib-item-1",
    snapshot_at: 1700000100,
    content_hash: "abc123",
    diff_json: { title: "Updated" },
    metadata_json: {},
  },
];

function createFetchMock(overrides: Record<string, Promise<Response>> = {}) {
  return vi.fn((input: RequestInfo | URL) => {
    const url = String(input);

    for (const [prefix, response] of Object.entries(overrides)) {
      if (url.startsWith(prefix)) return response;
    }

    if (url.includes("/snapshots")) {
      return Promise.resolve({
        ok: true,
        status: 200,
        headers: new Map([["content-type", "application/json"]]),
        json: () => Promise.resolve({ snapshots: MOCK_SNAPSHOTS }),
      } as Response);
    }

    if (url.startsWith("/api/knowledge/items/")) {
      const itemId = url.split("/").pop();
      const item = MOCK_ITEMS.find((i) => i.id === itemId);
      return Promise.resolve({
        ok: true,
        status: 200,
        headers: new Map([["content-type", "application/json"]]),
        json: () => Promise.resolve(item ?? null),
      } as Response);
    }

    if (url.startsWith("/api/knowledge/items")) {
      const qs = new URLSearchParams(url.split("?")[1] || "");
      const sourceType = qs.get("source_type");
      const status = qs.get("status");
      let items = MOCK_ITEMS;
      if (sourceType) items = items.filter((i) => i.source_type === sourceType);
      if (status) items = items.filter((i) => i.status === status);
      return Promise.resolve({
        ok: true,
        status: 200,
        headers: new Map([["content-type", "application/json"]]),
        json: () => Promise.resolve({ items, count: items.length }),
      } as Response);
    }

    if (url.startsWith("/api/knowledge/search")) {
      const qs = new URLSearchParams(url.split("?")[1] || "");
      const q = qs.get("q")?.toLowerCase() || "";
      const results = q ? MOCK_ITEMS.filter((i) => i.title.toLowerCase().includes(q)) : MOCK_ITEMS;
      return Promise.resolve({
        ok: true,
        status: 200,
        headers: new Map([["content-type", "application/json"]]),
        json: () => Promise.resolve({ results, mode: "keyword" }),
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
}

describe("LibraryApp", () => {
  afterEach(() => {
    vi.unstubAllGlobals();
    vi.restoreAllMocks();
  });

  it("renders items fetched from the API", async () => {
    vi.stubGlobal("fetch", createFetchMock() as unknown as typeof fetch);
    render(<LibraryApp windowId="test-win" />);

    await waitFor(() => screen.getByText("YouTube Video"), { timeout: 5000 });
    expect(screen.getByText("YouTube Video")).toBeInTheDocument();
    expect(screen.getByText("Reddit Post")).toBeInTheDocument();
    expect(screen.getByText("GitHub Repo")).toBeInTheDocument();
  });

  it("shows an empty state when the API returns no items", async () => {
    vi.stubGlobal(
      "fetch",
      createFetchMock({
        "/api/knowledge/items": Promise.resolve({
          ok: true,
          status: 200,
          headers: new Map([["content-type", "application/json"]]),
          json: () => Promise.resolve({ items: [], count: 0 }),
        } as Response),
      }) as unknown as typeof fetch,
    );
    render(<LibraryApp windowId="test-win" />);

    await waitFor(() => screen.getByText("No items in library"), { timeout: 5000 });
  });

  it("filters items by search query", async () => {
    vi.stubGlobal("fetch", createFetchMock() as unknown as typeof fetch);
    render(<LibraryApp windowId="test-win" />);

    await waitFor(() => screen.getByText("YouTube Video"), { timeout: 5000 });
    const searchInput = screen.getByPlaceholderText("Search knowledge base...");
    fireEvent.change(searchInput, { target: { value: "YouTube" } });

    await waitFor(() => screen.getByText("YouTube Video"), { timeout: 5000 });
    expect(screen.queryByText("Reddit Post")).toBeNull();
    expect(screen.queryByText("GitHub Repo")).toBeNull();
  });

  it("switches to semantic search mode", async () => {
    vi.stubGlobal("fetch", createFetchMock() as unknown as typeof fetch);
    render(<LibraryApp windowId="test-win" />);

    await waitFor(() => screen.getByText("YouTube Video"), { timeout: 5000 });
    const semanticButton = screen.getByRole("radio", { name: "semantic" });
    fireEvent.click(semanticButton);
    expect(semanticButton).toHaveAttribute("aria-checked", "true");
  });

  it("sorts items by newest first by default", async () => {
    vi.stubGlobal("fetch", createFetchMock() as unknown as typeof fetch);
    render(<LibraryApp windowId="test-win" />);

    await waitFor(() => screen.getByText("YouTube Video"), { timeout: 5000 });
    const items = screen.getAllByRole("button", { name: /Open/i });
    expect(items[0]).toHaveAccessibleName("Open YouTube Video");
  });

  it("sorts items alphabetically when the A-Z button is clicked", async () => {
    vi.stubGlobal("fetch", createFetchMock() as unknown as typeof fetch);
    render(<LibraryApp windowId="test-win" />);

    await waitFor(() => screen.getByText("YouTube Video"), { timeout: 5000 });
    const alphaButton = screen.getByRole("radio", { name: "A–Z" });
    fireEvent.click(alphaButton);

    await waitFor(() => screen.getByText("GitHub Repo"), { timeout: 5000 });
    const items = screen.getAllByRole("button", { name: /Open/i });
    expect(items[0]).toHaveAccessibleName("Open GitHub Repo");
  });

  it("toggles a source type filter", async () => {
    vi.stubGlobal("fetch", createFetchMock() as unknown as typeof fetch);
    render(<LibraryApp windowId="test-win" />);

    await waitFor(() => screen.getByText("YouTube Video"), { timeout: 5000 });
    const youtubeButton = screen.getByRole("button", { name: "YouTube" });
    fireEvent.click(youtubeButton);

    await waitFor(() => expect(screen.queryByText("Reddit Post")).toBeNull(), { timeout: 5000 });
    expect(screen.queryByText("GitHub Repo")).toBeNull();
  });

  it("toggles a status filter", async () => {
    vi.stubGlobal("fetch", createFetchMock() as unknown as typeof fetch);
    render(<LibraryApp windowId="test-win" />);

    await waitFor(() => screen.getByText("YouTube Video"), { timeout: 5000 });
    const processingButton = screen.getByRole("button", { name: "processing" });
    fireEvent.click(processingButton);

    await waitFor(() => expect(screen.queryByText("YouTube Video")).toBeNull(), { timeout: 5000 });
    expect(screen.queryByText("Reddit Post")).toBeNull();
  });

  it("opens detail view when an item is clicked", async () => {
    vi.stubGlobal("fetch", createFetchMock() as unknown as typeof fetch);
    render(<LibraryApp windowId="test-win" />);

    await waitFor(() => screen.getByText("YouTube Video"), { timeout: 5000 });
    fireEvent.click(screen.getByRole("button", { name: /Open YouTube Video/i }));

    await waitFor(() => screen.getByText("Content 1"), { timeout: 5000 });
    expect(screen.getByRole("button", { name: "Back to library" })).toBeInTheDocument();
  });

  it("shows snapshots in the detail view", async () => {
    vi.stubGlobal("fetch", createFetchMock() as unknown as typeof fetch);
    render(<LibraryApp windowId="test-win" />);

    await waitFor(() => screen.getByText("YouTube Video"), { timeout: 5000 });
    fireEvent.click(screen.getByRole("button", { name: /Open YouTube Video/i }));

    await waitFor(() => screen.getByText("History (1)"), { timeout: 5000 });
    expect(screen.getByText("History (1)")).toBeInTheDocument();
  });

  it("deletes an item after confirmation", async () => {
    vi.stubGlobal("fetch", createFetchMock() as unknown as typeof fetch);
    render(<LibraryApp windowId="test-win" />);

    await waitFor(() => screen.getByText("YouTube Video"), { timeout: 5000 });
    fireEvent.click(screen.getByRole("button", { name: /Open YouTube Video/i }));

    await waitFor(() => screen.getByRole("button", { name: "Back to library" }), { timeout: 5000 });
    fireEvent.click(screen.getByRole("button", { name: /Delete/i }));

    await waitFor(() => screen.getByText("Confirm delete?"), { timeout: 5000 });
    fireEvent.click(screen.getByRole("button", { name: "Confirm delete item" }));

    await waitFor(() => expect(screen.queryByText("YouTube Video")).toBeNull(), { timeout: 5000 });
  });

  it("switches to the storage view", async () => {
    vi.stubGlobal("fetch", createFetchMock() as unknown as typeof fetch);
    render(<LibraryApp windowId="test-win" />);

    await waitFor(() => screen.getByRole("radio", { name: "storage" }), { timeout: 5000 });
    fireEvent.click(screen.getByRole("radio", { name: "storage" }));

    await waitFor(() => screen.getByText("Storage Accounting"), { timeout: 5000 });
    expect(screen.getByText("Storage Accounting")).toBeInTheDocument();
  });
});
