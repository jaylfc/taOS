import { render, screen, act, fireEvent, within } from "@testing-library/react";
import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { FilesApp } from "./FilesApp";

/**
 * The Photos location is decided by the server: GET /api/files/locations lists
 * only the built-in locations whose base directory exists (the phone's Pictures
 * folder), and the Files sidebar renders whatever it returns. Nothing here is
 * keyed off a device check in the frontend.
 */
/**
 * A Files listing entry, matching the shape the backend `_list_dir` returns.
 * Modelled here so the test does not depend on FilesApp's internal type.
 */
type ListingEntry = {
  name: string;
  path: string;
  is_dir: boolean;
  size: number;
  modified: number;
};

function stubFetch(locations: { id: string; label: string }[]): string[];
function stubFetch(
  locations: { id: string; label: string }[],
  listings: Record<string, ListingEntry[]>,
): string[];
function stubFetch(
  locations: { id: string; label: string }[],
  listings: Record<string, ListingEntry[]> = {},
): string[] {
  const urls: string[] = [];
  const emptyListing: ListingEntry[] = [];
  const fetchMock = vi.fn().mockImplementation((url: string) => {
    urls.push(String(url));
    const key = String(url);
    let body: unknown = emptyListing;
    if (key.startsWith("/api/files/locations")) {
      body = locations;
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

describe("FilesApp Photos location", () => {
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
  });

  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("lists the Photos entry when the API reports the location", async () => {
    stubFetch([{ id: "photos", label: "Photos" }]);

    render(<FilesApp windowId="w1" />);
    await flush();

    const entry = screen.getByLabelText("Photos");
    expect(entry).toBeTruthy();
    // An image icon, not the generic folder icon used by shared folders.
    expect(entry.querySelector("svg")).toBeTruthy();
  });

  it("lists no Photos entry when the API does not report it", async () => {
    stubFetch([]);

    render(<FilesApp windowId="w1" />);
    await flush();

    expect(screen.queryByLabelText("Photos")).toBeNull();
  });

  it("fetches the Photos listing from the built-in location endpoint", async () => {
    const urls = stubFetch([{ id: "photos", label: "Photos" }]);

    render(<FilesApp windowId="w1" />);
    await flush();

    await act(async () => {
      screen.getByLabelText("Photos").dispatchEvent(new MouseEvent("click", { bubbles: true }));
      await Promise.resolve();
      await Promise.resolve();
    });

    expect(urls).toContain("/api/photos/files");
    expect(urls.some((u) => u.startsWith("/api/shared-folders/photos"))).toBe(false);
  });

  it("offers New folder for the workspace but not for the Photos location", async () => {
    // mkdir is driven off the New folder button, shown only for a writable
    // location. Asserting the button is present for the workspace keeps the
    // Photos assertion meaningful rather than trivially true.
    stubFetch([{ id: "photos", label: "Photos" }]);
    render(<FilesApp windowId="w1" />);
    await flush();

    // The app opens on the user workspace, which is writable.
    expect(screen.getByLabelText("New folder")).toBeTruthy();

    // Select the built-in Photos location.
    await act(async () => {
      fireEvent.click(screen.getByLabelText("Photos"));
    });
    await flush();

    // Photos has no backend mkdir endpoint, so no New folder control is shown.
    expect(screen.queryByLabelText("New folder")).toBeNull();
  });

  it("omits rename, cut and copy from a photo's context menu but keeps delete", async () => {
    // copy / move (rename) are gated off these actions; Photos must not offer
    // them because the backend exposes no copy or rename endpoint for it.
    const photo: ListingEntry = {
      name: "IMG_0001.jpg",
      path: "IMG_0001.jpg",
      is_dir: false,
      size: 1234,
      modified: 1,
    };
    stubFetch([{ id: "photos", label: "Photos" }], { "/api/photos/files": [photo] });
    render(<FilesApp windowId="w1" />);
    await flush();

    await act(async () => {
      fireEvent.click(screen.getByLabelText("Photos"));
    });
    await flush();

    const row = await screen.findByLabelText("File IMG_0001.jpg");
    await act(async () => {
      fireEvent.contextMenu(row, { clientX: 40, clientY: 40 });
    });

    const menu = await screen.findByRole("menu", { name: "Context menu" });
    expect(within(menu).queryByText("Rename")).toBeNull();
    expect(within(menu).queryByText("Cut")).toBeNull();
    expect(within(menu).queryByText("Copy")).toBeNull();
    // Read and delete are the operations Photos supports.
    expect(within(menu).getByText("Delete")).toBeTruthy();
  });
});
