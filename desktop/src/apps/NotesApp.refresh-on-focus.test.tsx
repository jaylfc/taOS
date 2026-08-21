import { render, act } from "@testing-library/react";
import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { NotesApp } from "./NotesApp";

describe("NotesApp refresh-on-focus", () => {
  beforeEach(() => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
  });
  afterEach(() => {
    vi.useRealTimers();
    vi.unstubAllGlobals();
  });

  it("refetches /api/notes on window focus with the same URL as mount", async () => {
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

    render(<NotesApp windowId="w1" />);
    await act(async () => {
      await Promise.resolve();
    });

    const notesCalls = () => urls.filter((u) => u === "/api/notes");
    expect(notesCalls().length).toBeGreaterThan(0);
    const before = notesCalls().length;

    window.dispatchEvent(new Event("focus"));
    await act(async () => {
      vi.advanceTimersByTime(1100);
      await Promise.resolve();
    });

    const after = notesCalls();
    expect(after.length).toBe(before + 1);
  });
});
