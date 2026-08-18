import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { describe, it, expect, vi, afterEach, beforeEach } from "vitest";
import { LibraryApp } from "./LibraryApp";

const MOCK_ITEMS = [
  {
    id: "lib-1",
    kind: "url:youtube",
    source_url: "https://youtube.com/watch?v=1",
    title: "Test Video",
    status: "ready",
    storage_path: "",
    bytes: 1024,
    meta_json: JSON.stringify({ preview: "Preview text" }),
    created_at: Date.now() / 1000 - 86400,
    updated_at: Date.now() / 1000 - 3600,
  },
];

function createFetchMock(items: typeof MOCK_ITEMS = MOCK_ITEMS) {
  return vi.fn((input: RequestInfo | URL) => {
    const url = String(input);
    if (url.startsWith("/api/library/items")) {
      return Promise.resolve({
        ok: true,
        status: 200,
        headers: new Map([["content-type", "application/json"]]),
        json: () => Promise.resolve({ items, count: items.length }),
      } as Response);
    }
    if (url === "/api/library/ingest") {
      return Promise.resolve({
        ok: true,
        status: 200,
        headers: new Map([["content-type", "application/json"]]),
        json: () => Promise.resolve({ item_id: "new-1", status: "pending" }),
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
  beforeEach(() => {
    if (typeof globalThis.DataTransfer === "undefined") {
      (globalThis as any).DataTransfer = class MockDataTransfer {
        files: File[] = [];
        items: any = {
          add: (file: File) => {
            this.files.push(file);
          },
        };
        types: string[] = [];
        dropEffect = "";
        effectAllowed = "";
        clearData() {}
        getData() { return ""; }
        setData() {}
        setDragImage() {}
      };
    }
  });

  afterEach(() => {
    vi.unstubAllGlobals();
    vi.restoreAllMocks();
  });

  it("renders items fetched from the API", async () => {
    vi.stubGlobal("fetch", createFetchMock() as unknown as typeof fetch);
    render(<LibraryApp windowId="test-win" />);

    await waitFor(() => screen.getByText("Test Video"), { timeout: 5000 });
    expect(screen.getByText("Test Video")).toBeInTheDocument();
  });

  it("shows an empty state when the API returns no items", async () => {
    vi.stubGlobal(
      "fetch",
      createFetchMock([]) as unknown as typeof fetch,
    );
    render(<LibraryApp windowId="test-win" />);

    await waitFor(() => screen.getByText("Your library is empty"), { timeout: 5000 });
  });

  it("handles file drops by ingesting the dropped file", async () => {
    vi.stubGlobal("fetch", createFetchMock() as unknown as typeof fetch);
    render(<LibraryApp windowId="test-win" />);

    await waitFor(() => screen.getByText("Test Video"), { timeout: 5000 });

    const dropZone = screen.getByRole("region", { name: "Library drop zone" });

    const file = new File(["content"], "test.txt", { type: "text/plain" });
    const dataTransfer = new DataTransfer();
    dataTransfer.items.add(file);

    fireEvent.dragOver(dropZone, { dataTransfer });
    fireEvent.drop(dropZone, { dataTransfer });

    await waitFor(() => {
      const fetchMock = (globalThis as any).fetch as ReturnType<typeof vi.fn>;
      const ingestCalls = fetchMock.mock.calls.filter(([url]: [string]) => String(url).includes("/api/library/ingest"));
      expect(ingestCalls.length).toBeGreaterThanOrEqual(1);
    });
  });
});
