import { render, screen, fireEvent, waitFor, within } from "@testing-library/react";
import { describe, it, expect, vi, afterEach, beforeEach } from "vitest";
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

function createFetchMock() {
  return vi.fn((input: RequestInfo | URL) => {
    const url = String(input);
    if (url.startsWith("/api/knowledge/items")) {
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
        json: () => Promise.resolve([{ name: "Agent 1", color: "#ff0000" }]),
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

describe("LibraryApp settings view", () => {
  beforeEach(() => {
    localStorage.clear();
  });

  afterEach(() => {
    vi.unstubAllGlobals();
    vi.restoreAllMocks();
  });

  it("renders the view mode toggle and switches to settings view", async () => {
    vi.stubGlobal("fetch", createFetchMock() as unknown as typeof fetch);
    render(<LibraryApp windowId="test-win" />);

    await waitFor(() => screen.getByRole("radio", { name: "settings" }));
    fireEvent.click(screen.getByRole("radio", { name: "settings" }));

    await waitFor(() => screen.getByText("Library Settings"), { timeout: 5000 });
  });

  it("shows default preferred quality select", async () => {
    vi.stubGlobal("fetch", createFetchMock() as unknown as typeof fetch);
    render(<LibraryApp windowId="test-win" />);

    await waitFor(() => screen.getByRole("radio", { name: "settings" }));
    fireEvent.click(screen.getByRole("radio", { name: "settings" }));

    await waitFor(() => screen.getByText("Library Settings"), { timeout: 5000 });
    const select = screen.getByLabelText("Default download quality");
    expect(select).toBeInTheDocument();
    expect(select).toHaveValue("high");
  });

  it("shows storage cap input with formatted default", async () => {
    vi.stubGlobal("fetch", createFetchMock() as unknown as typeof fetch);
    render(<LibraryApp windowId="test-win" />);

    await waitFor(() => screen.getByRole("radio", { name: "settings" }));
    fireEvent.click(screen.getByRole("radio", { name: "settings" }));

    await waitFor(() => screen.getByText("Library Settings"), { timeout: 5000 });
    const input = screen.getByLabelText("Maximum storage (bytes)");
    expect(input).toBeInTheDocument();
    expect(screen.getByText("50.00 GB")).toBeInTheDocument();
  });

  it("shows empty per-source rules table", async () => {
    vi.stubGlobal("fetch", createFetchMock() as unknown as typeof fetch);
    render(<LibraryApp windowId="test-win" />);

    await waitFor(() => screen.getByRole("radio", { name: "settings" }));
    fireEvent.click(screen.getByRole("radio", { name: "settings" }));

    await waitFor(() => screen.getByText("Library Settings"), { timeout: 5000 });
    expect(screen.getByText("No source rules yet")).toBeInTheDocument();
  });

  it("adds a per-source rule and shows it in the table", async () => {
    vi.stubGlobal("fetch", createFetchMock() as unknown as typeof fetch);
    render(<LibraryApp windowId="test-win" />);

    await waitFor(() => screen.getByRole("radio", { name: "settings" }));
    fireEvent.click(screen.getByRole("radio", { name: "settings" }));

    await waitFor(() => screen.getByText("Library Settings"), { timeout: 5000 });

    const sourceSelect = screen.getByLabelText("Source");
    const actionSelect = screen.getByLabelText("Action");
    const qualitySelect = screen.getByLabelText("Quality");

    fireEvent.change(sourceSelect, { target: { value: "youtube" } });
    fireEvent.change(actionSelect, { target: { value: "skip" } });
    fireEvent.change(qualitySelect, { target: { value: "low" } });

    fireEvent.click(screen.getByRole("button", { name: "Add source rule" }));

    const table = document.querySelector("table");
    expect(table).toBeTruthy();
    const tableBody = within(table!);
    expect(tableBody.getByText("youtube")).toBeInTheDocument();
    expect(tableBody.getByText("skip")).toBeInTheDocument();
    expect(tableBody.getByText("low")).toBeInTheDocument();
    expect(screen.queryByText("No source rules yet")).toBeNull();
  });

  it("deletes a per-source rule", async () => {
    vi.stubGlobal("fetch", createFetchMock() as unknown as typeof fetch);
    render(<LibraryApp windowId="test-win" />);

    await waitFor(() => screen.getByRole("radio", { name: "settings" }));
    fireEvent.click(screen.getByRole("radio", { name: "settings" }));

    await waitFor(() => screen.getByText("Library Settings"), { timeout: 5000 });

    const sourceSelect = screen.getByLabelText("Source");
    const actionSelect = screen.getByLabelText("Action");
    const qualitySelect = screen.getByLabelText("Quality");

    fireEvent.change(sourceSelect, { target: { value: "youtube" } });
    fireEvent.change(actionSelect, { target: { value: "skip" } });
    fireEvent.change(qualitySelect, { target: { value: "low" } });

    fireEvent.click(screen.getByRole("button", { name: "Add source rule" }));

    await waitFor(() => screen.getByText("Library Settings"), { timeout: 5000 });

    const table = document.querySelector("table");
    expect(table).toBeTruthy();
    const tableBody = within(table!);
    fireEvent.click(tableBody.getByRole("button", { name: "Delete rule for youtube" }));

    await waitFor(() => screen.getByText("No source rules yet"), { timeout: 5000 });
  });

  it("persists settings to localStorage", async () => {
    vi.stubGlobal("fetch", createFetchMock() as unknown as typeof fetch);
    render(<LibraryApp windowId="test-win" />);

    await waitFor(() => screen.getByRole("radio", { name: "settings" }));
    fireEvent.click(screen.getByRole("radio", { name: "settings" }));

    await waitFor(() => screen.getByText("Library Settings"), { timeout: 5000 });

    const qualitySelect = screen.getByLabelText("Default download quality");
    fireEvent.change(qualitySelect, { target: { value: "best" } });

    const raw = localStorage.getItem("taos-library-settings");
    expect(raw).toBeTruthy();
    const parsed = JSON.parse(raw!);
    expect(parsed.preferred_quality).toBe("best");
  });
});
