import { render, screen, act, fireEvent, within } from "@testing-library/react";
import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { FilesApp } from "./FilesApp";

/**
 * A Files listing entry, matching the shape the backend `_list_dir` returns.
 */
type ListingEntry = {
  name: string;
  path: string;
  is_dir: boolean;
  size: number;
  modified: number;
};

interface SharedFolderStub {
  id: number;
  name: string;
  description: string;
  agents: { name: string; permission: string }[];
}

interface RecycleItemStub {
  id: string;
  agent_name: string;
  original_path: string;
  deleted_at: string;
}

interface WorkspaceTrashItemStub {
  id: string;
  name: string;
  original_path: string;
  is_dir: boolean;
  deleted_at: string;
}

interface PhotosTrashItemStub {
  id: string;
  original_path: string;
  deleted_at: string;
}

function stubFetch(
  locations: { id: string; label: string }[],
  listings: Record<string, ListingEntry[]> = {},
  sharedFolders: SharedFolderStub[] = [],
  recycleItems: RecycleItemStub[] = [],
  workspaceTrashItems: WorkspaceTrashItemStub[] = [],
  photosTrashItems: PhotosTrashItemStub[] = [],
): string[] {
  const urls: string[] = [];
  const emptyListing: ListingEntry[] = [];
  const fetchMock = vi.fn().mockImplementation((url: string) => {
    urls.push(String(url));
    const key = String(url);
    let body: unknown = emptyListing;
    if (key.startsWith("/api/files/locations")) {
      body = locations;
    } else if (key === "/api/shared-folders") {
      body = sharedFolders;
    } else if (key === "/api/recycle") {
      body = { items: recycleItems, status: "ok" };
    } else if (key === "/api/workspace/trash") {
      body = { items: workspaceTrashItems };
    } else if (key === "/api/photos/trash") {
      body = { items: photosTrashItems };
    } else {
      const match = Object.keys(listings).find((prefix) => key.startsWith(prefix));
      if (match) body = listings[match];
    }
    return Promise.resolve({
      ok: true,
      status: 200,
      headers: { get: () => "application/json" },
      json: () => Promise.resolve(body),
      text: () => Promise.resolve(""),
    });
  });
  vi.stubGlobal("fetch", fetchMock);
  return urls;
}

function stubFetchWithPhotosForbidden(
  locations: { id: string; label: string }[],
  listings: Record<string, ListingEntry[]> = {},
  sharedFolders: SharedFolderStub[] = [],
  recycleItems: RecycleItemStub[] = [],
  workspaceTrashItems: WorkspaceTrashItemStub[] = [],
): string[] {
  const urls: string[] = [];
  const emptyListing: ListingEntry[] = [];
  const fetchMock = vi.fn().mockImplementation((url: string) => {
    urls.push(String(url));
    const key = String(url);
    let body: unknown = emptyListing;
    if (key.startsWith("/api/files/locations")) {
      body = locations;
    } else if (key === "/api/shared-folders") {
      body = sharedFolders;
    } else if (key === "/api/recycle") {
      body = { items: recycleItems, status: "ok" };
    } else if (key === "/api/workspace/trash") {
      body = { items: workspaceTrashItems };
    } else if (key === "/api/photos/trash") {
      return Promise.resolve({
        ok: false,
        status: 403,
        headers: { get: () => "application/json" },
        json: () => Promise.resolve({ error: "forbidden" }),
        text: () => Promise.resolve("forbidden"),
      });
    } else {
      const match = Object.keys(listings).find((prefix) => key.startsWith(prefix));
      if (match) body = listings[match];
    }
    return Promise.resolve({
      ok: true,
      status: 200,
      headers: { get: () => "application/json" },
      json: () => Promise.resolve(body),
      text: () => Promise.resolve(""),
    });
  });
  vi.stubGlobal("fetch", fetchMock);
  return urls;
}

async function flush() {
  await act(async () => {
    await Promise.resolve();
    await Promise.resolve();
  });
}

async function openRecycleBin() {
  await act(async () => {
    fireEvent.click(screen.getByLabelText("Recycle Bin"));
    await Promise.resolve();
    await Promise.resolve();
  });
}

describe("FilesApp Recycle Bin Photos trash", () => {
  beforeEach(() => {
    vi.stubGlobal(
      "EventSource",
      class {
        onmessage: unknown = null;
        onerror: unknown = null;
        close() {}
        addEventListener() {}
        removeEventListener() {}
      },
    );
    vi.spyOn(window, "confirm").mockReturnValue(true);
  });

  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("shows a Photos section when photos trash has items", async () => {
    const photosTrash: PhotosTrashItemStub[] = [
      {
        id: "photo-1",
        original_path: "IMG_0001.jpg",
        deleted_at: new Date(Date.now() - 1000 * 60 * 60).toISOString(),
      },
    ];
    stubFetch(
      [{ id: "photos", label: "Photos" }],
      {},
      [],
      [],
      [],
      photosTrash,
    );

    render(<FilesApp windowId="w1" />);
    await flush();

    await openRecycleBin();
    await flush();

    // The Photos section in the recycle bin has aria-label "Trashed items from Photos"
    expect(screen.getByLabelText("Trashed items from Photos")).toBeTruthy();
    // The deleted photo should be listed
    expect(screen.getByText("IMG_0001.jpg")).toBeTruthy();
  });

  it("restore calls the photos restore route", async () => {
    const photosTrash: PhotosTrashItemStub[] = [
      {
        id: "photo-1",
        original_path: "IMG_0001.jpg",
        deleted_at: new Date(Date.now() - 1000 * 60 * 60).toISOString(),
      },
    ];
    const urls = stubFetch(
      [{ id: "photos", label: "Photos" }],
      {},
      [],
      [],
      [],
      photosTrash,
    );

    render(<FilesApp windowId="w1" />);
    await flush();

    await openRecycleBin();
    await flush();

    // Find and click the restore button for the photo
    const restoreBtn = screen.getByLabelText("Restore IMG_0001.jpg");
    await act(async () => {
      fireEvent.click(restoreBtn);
      await Promise.resolve();
      await Promise.resolve();
    });

    // The restore call should go to /api/photos/trash/photo-1/restore
    expect(urls.some((u) => u.includes("/api/photos/trash/photo-1/restore"))).toBe(true);
  });

  it("purge calls the photos delete route", async () => {
    const photosTrash: PhotosTrashItemStub[] = [
      {
        id: "photo-1",
        original_path: "IMG_0001.jpg",
        deleted_at: new Date(Date.now() - 1000 * 60 * 60).toISOString(),
      },
    ];
    const urls = stubFetch(
      [{ id: "photos", label: "Photos" }],
      {},
      [],
      [],
      [],
      photosTrash,
    );

    render(<FilesApp windowId="w1" />);
    await flush();

    await openRecycleBin();
    await flush();

    // Find and click the purge button for the photo
    const purgeBtn = screen.getByLabelText("Permanently delete IMG_0001.jpg");
    await act(async () => {
      fireEvent.click(purgeBtn);
      await Promise.resolve();
      await Promise.resolve();
    });

    // The purge call should go to /api/photos/trash/photo-1
    expect(urls.some((u) => u.includes("/api/photos/trash/photo-1"))).toBe(true);
  });

  it("shows no Photos section when user has no Photos access (403)", async () => {
    const urls = stubFetchWithPhotosForbidden(
      [{ id: "photos", label: "Photos" }],
      {},
      [],
      [],
      [],
    );

    render(<FilesApp windowId="w1" />);
    await flush();

    await openRecycleBin();
    await flush();

    // The Photos section in the recycle bin should not be present
    // (the sidebar still shows the Photos location, but the recycle bin section should not)
    expect(screen.queryByLabelText("Trashed items from Photos")).toBeNull();
  });

  it("shows Photos section alongside My Files and agent sections", async () => {
    const photosTrash: PhotosTrashItemStub[] = [
      {
        id: "photo-1",
        original_path: "IMG_0001.jpg",
        deleted_at: new Date(Date.now() - 1000 * 60 * 60).toISOString(),
      },
    ];
    const workspaceTrash: WorkspaceTrashItemStub[] = [
      {
        id: "ws-1",
        name: "doc.txt",
        original_path: "doc.txt",
        is_dir: false,
        deleted_at: new Date(Date.now() - 1000 * 60 * 30).toISOString(),
      },
    ];
    const recycleItems: RecycleItemStub[] = [
      {
        id: "agent-1",
        agent_name: "my-agent",
        original_path: "agent-file.txt",
        deleted_at: new Date(Date.now() - 1000 * 60 * 10).toISOString(),
      },
    ];

    stubFetch(
      [{ id: "photos", label: "Photos" }],
      {},
      [],
      recycleItems,
      workspaceTrash,
      photosTrash,
    );

    render(<FilesApp windowId="w1" />);
    await flush();

    await openRecycleBin();
    await flush();

    // All three sections should be present (using their aria-labels)
    expect(screen.getByLabelText("Trashed items from your workspace")).toBeTruthy();
    expect(screen.getByLabelText("Trashed items from Photos")).toBeTruthy();
    expect(screen.getByLabelText("Recycle bin items for agent my-agent")).toBeTruthy();

    // Items should be in their respective sections
    expect(screen.getByText("doc.txt")).toBeTruthy();
    expect(screen.getByText("IMG_0001.jpg")).toBeTruthy();
    expect(screen.getByText("agent-file.txt")).toBeTruthy();
  });
});