import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { TAOS_AGENT_CONFIG_URL } from "./taos-agent-api";

function jsonResponse(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

const { openUrlInShellMock, fetchMock } = vi.hoisted(() => ({
  openUrlInShellMock: vi.fn(),
  fetchMock: vi.fn(),
}));

vi.mock("@/lib/mobile-shell", async (importOriginal) => {
  const actual = await importOriginal();
  return {
    ...actual,
    openUrlInShell: openUrlInShellMock,
  };
});

describe("app-standalone-main shell link routing", () => {
  let configResolve: (value: Response) => void;
  let configPromise: Promise<Response>;

  beforeEach(() => {
    vi.resetModules();
    openUrlInShellMock.mockReset();
    openUrlInShellMock.mockResolvedValue(true);

    configPromise = new Promise((resolve) => {
      configResolve = resolve;
    });

    fetchMock.mockImplementation((input: RequestInfo | URL) => {
      const url = String(input);
      if (url === TAOS_AGENT_CONFIG_URL) {
        return configPromise;
      }
      return Promise.reject(new Error(`unexpected fetch ${url}`));
    });
    vi.stubGlobal("fetch", fetchMock);

    document.head.innerHTML = "";
    document.body.innerHTML = '<div id="root"></div>';
    document.title = "";
  });

  afterEach(() => {
    vi.unstubAllGlobals();
    vi.useRealTimers();
  });

  it("awaits device-class prime before routing links: window.open after boot routes through openUrlInShell on mobile", async () => {
    let booted = false;
    const bootPromise = import("../app-standalone-main").then((m) => m.booted).then(() => {
      booted = true;
    });

    await vi.waitFor(() => {
      const calls = fetchMock.mock.calls;
      expect(calls.some(call => call[0] === TAOS_AGENT_CONFIG_URL)).toBe(true);
    });

    expect(booted).toBe(false);

    configResolve!(jsonResponse({ device_class: "mobile" }));

    await bootPromise;

    window.open("https://example.com/", "_blank");

    expect(openUrlInShellMock).toHaveBeenCalledWith("https://example.com/");
  });

  it("renders even when the config fetch never resolves", async () => {
    vi.useFakeTimers();

    let booted = false;
    const bootPromise = import("../app-standalone-main").then((m) => m.booted).then(() => {
      booted = true;
    });

    expect(booted).toBe(false);

    await vi.advanceTimersByTimeAsync(2000);

    await bootPromise;

    expect(booted).toBe(true);
    expect(document.getElementById('root')).toHaveProperty('children');
    expect(document.getElementById('root')?.children?.length).toBeGreaterThan(0);
  });
});