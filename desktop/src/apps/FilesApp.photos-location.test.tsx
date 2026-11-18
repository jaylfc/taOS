import { render, screen, act } from "@testing-library/react";
import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { FilesApp } from "./FilesApp";

/**
 * The Photos location is decided by the server: GET /api/files/locations lists
 * only the built-in locations whose base directory exists (the phone's Pictures
 * folder), and the Files sidebar renders whatever it returns. Nothing here is
 * keyed off a device check in the frontend.
 */
function stubFetch(locations: { id: string; label: string }[]) {
  const urls: string[] = [];
  const fetchMock = vi.fn().mockImplementation((url: string) => {
    urls.push(String(url));
    const body = String(url).startsWith("/api/files/locations")
      ? locations
      : [];
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
});
