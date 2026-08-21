import { render, act } from "@testing-library/react";
import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { ModelsApp } from "./ModelsApp";

describe("ModelsApp refresh-on-focus", () => {
  beforeEach(() => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
  });
  afterEach(() => {
    vi.useRealTimers();
    vi.unstubAllGlobals();
  });

  it("refetches /api/models on window focus with the same URL as mount", async () => {
    const urls: string[] = [];
    const fetchMock = vi.fn().mockImplementation((url: string) => {
      urls.push(String(url));
      if (url === "/api/models") {
        return Promise.resolve({
          ok: true,
          status: 200,
          headers: { get: () => "application/json" },
          json: () => Promise.resolve({ models: [], downloaded_files: [], hardware_profile_id: "p1" }),
          text: () => Promise.resolve(""),
        });
      }
      if (url.startsWith("/api/models/downloads/")) {
        return Promise.resolve({
          ok: true,
          status: 200,
          headers: { get: () => "application/json" },
          json: () => Promise.resolve({ status: "complete", percent: 100 }),
          text: () => Promise.resolve(""),
        });
      }
      if (url === "/api/models/download") {
        return Promise.resolve({
          ok: true,
          status: 200,
          headers: { get: () => "application/json" },
          json: () => Promise.resolve({ status: "started", download_id: "dl-1" }),
          text: () => Promise.resolve(""),
        });
      }
      return Promise.resolve({
        ok: true,
        status: 200,
        headers: { get: () => "application/json" },
        json: () => Promise.resolve({}),
        text: () => Promise.resolve(""),
      });
    });
    vi.stubGlobal("fetch", fetchMock);

    render(<ModelsApp windowId="w1" />);
    await act(async () => {
      await Promise.resolve();
    });

    const modelCalls = () => urls.filter((u) => u === "/api/models");
    expect(modelCalls().length).toBeGreaterThan(0);
    const before = modelCalls().length;

    window.dispatchEvent(new Event("focus"));
    await act(async () => {
      vi.advanceTimersByTime(1100);
      await Promise.resolve();
    });

    const after = modelCalls();
    expect(after.length).toBe(before + 1);
  });
});
