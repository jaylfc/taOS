import { render, act } from "@testing-library/react";
import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { FilesApp } from "./FilesApp";

/**
 * The refresh-on-focus hook calls its refetch with NO arguments. FilesApp's
 * fetchFiles defaults its path to "" (the workspace root), so a refetch that
 * forgets to pass currentPath silently replaces the listing of the directory
 * the user is actually in with the root's contents, while the breadcrumb and
 * navigation state still point at the sub-directory.
 */
describe("FilesApp refresh-on-focus", () => {
  beforeEach(() => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
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
    vi.useRealTimers();
    vi.unstubAllGlobals();
  });

  it("refetches the directory the user is in, not the workspace root", async () => {
    const urls: string[] = [];
    const fetchMock = vi.fn().mockImplementation((url: string) => {
      urls.push(String(url));
      return Promise.resolve({
        ok: true,
        status: 200,
        headers: { get: () => "application/json" },
        json: () => Promise.resolve([]),
        text: () => Promise.resolve(""),
      });
    });
    vi.stubGlobal("fetch", fetchMock);

    render(<FilesApp windowId="w1" path="docs/spec" />);
    await act(async () => {
      await Promise.resolve();
    });

    const listCalls = () =>
      urls.filter((u) => u.startsWith("/api/workspace/files"));
    expect(listCalls().length).toBeGreaterThan(0);
    const before = listCalls().length;

    window.dispatchEvent(new Event("focus"));
    await act(async () => {
      vi.advanceTimersByTime(1100);
      await Promise.resolve();
    });

    const after = listCalls();
    expect(after.length).toBe(before + 1);
    expect(after[after.length - 1]).toBe(
      "/api/workspace/files?path=docs%2Fspec",
    );
  });
});
