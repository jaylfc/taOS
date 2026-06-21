import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { renderHook, act } from "@testing-library/react";
import { useServerPreference } from "./use-server-preference";

describe("useServerPreference", () => {
  const originalFetch = globalThis.fetch;
  const originalLocalStorage = globalThis.localStorage;

  beforeEach(() => {
    vi.useFakeTimers();
    localStorage.clear();
  });

  afterEach(() => {
    vi.useRealTimers();
    globalThis.fetch = originalFetch;
    localStorage.clear();
  });

  function mockFetch(response: { ok: boolean; body?: unknown }) {
    globalThis.fetch = vi.fn().mockResolvedValue({
      ok: response.ok,
      json: async () => response.body,
    });
  }

  it("returns the default value when localStorage is empty and fetch has not completed", () => {
    mockFetch({ ok: false });

    const { result } = renderHook(() =>
      useServerPreference("theme", "dark"),
    );

    expect(result.current[0]).toBe("dark");
    expect(result.current[2].loaded).toBe(false);
  });

  it("returns the default value when the server responds with 404", async () => {
    mockFetch({ ok: false });

    const { result } = renderHook(() =>
      useServerPreference("theme", "dark"),
    );

    await act(async () => {
      await vi.runAllTimersAsync();
    });

    expect(result.current[0]).toBe("dark");
    expect(result.current[2].loaded).toBe(true);
  });

  it("hydrates from localStorage cache before fetch completes", () => {
    localStorage.setItem("taos-pref:theme", '"cached-value"');
    mockFetch({ ok: false });

    const { result } = renderHook(() =>
      useServerPreference("theme", "dark"),
    );

    expect(result.current[0]).toBe("cached-value");
  });

  it("overrides local cache with server response when server has data", async () => {
    localStorage.setItem("taos-pref:theme", '"cached-value"');
    mockFetch({ ok: true, body: { value: "server-value" } });

    const { result } = renderHook(() =>
      useServerPreference("theme", "dark"),
    );

    await act(async () => {
      await vi.runAllTimersAsync();
    });

    expect(result.current[0]).toBe("server-value");
    expect(result.current[2].loaded).toBe(true);
  });

  it("keeps current value when server returns an empty object", async () => {
    mockFetch({ ok: true, body: {} });

    const { result } = renderHook(() =>
      useServerPreference("theme", "dark"),
    );

    await act(async () => {
      await vi.runAllTimersAsync();
    });

    expect(result.current[0]).toBe("dark");
    expect(result.current[2].loaded).toBe(true);
  });

  it("sets loaded to true when fetch throws a network error", async () => {
    globalThis.fetch = vi.fn().mockRejectedValue(new Error("network"));

    const { result } = renderHook(() =>
      useServerPreference("theme", "dark"),
    );

    await act(async () => {
      await vi.runAllTimersAsync();
    });

    expect(result.current[0]).toBe("dark");
    expect(result.current[2].loaded).toBe(true);
  });

  it("updates value via setValue and writes to localStorage", () => {
    mockFetch({ ok: false });

    const { result } = renderHook(() =>
      useServerPreference("theme", "dark"),
    );

    act(() => {
      result.current[1]("light");
    });

    expect(result.current[0]).toBe("light");
    expect(localStorage.getItem("taos-pref:theme")).toBe('"light"');
  });

  it("updates value using a function updater", () => {
    mockFetch({ ok: false });

    const { result } = renderHook(() =>
      useServerPreference<string>("theme", "dark"),
    );

    act(() => {
      result.current[1]((prev) => (prev === "dark" ? "light" : "dark"));
    });

    expect(result.current[0]).toBe("light");
  });

  it("debounces server writes via PUT request", async () => {
    const fetchMock = vi.fn().mockResolvedValue({ ok: true, json: async () => ({}) });
    globalThis.fetch = fetchMock;

    const { result } = renderHook(() =>
      useServerPreference("theme", "dark"),
    );

    // Drain the initial fetch call
    await act(async () => {
      await vi.runAllTimersAsync();
    });
    const initialCallCount = fetchMock.mock.calls.length;

    act(() => {
      result.current[1]("light");
    });

    // Not yet debounced
    expect(fetchMock.mock.calls.length).toBe(initialCallCount);

    await act(async () => {
      vi.advanceTimersByTime(500);
    });

    // After debounce timer, a PUT should have been sent
    const putCalls = fetchMock.mock.calls.slice(initialCallCount);
    expect(putCalls.length).toBe(1);
    const [url, opts] = putCalls[0];
    expect(url).toContain("/api/preferences/theme");
    expect(opts.method).toBe("PUT");
    expect(JSON.parse(opts.body)).toEqual({ value: "light" });
  });

  it("uses extract and pack projectors", async () => {
    const fetchMock = vi.fn()
      .mockResolvedValueOnce({ ok: true, json: async () => ({ home: "London" }) })
      .mockResolvedValue({ ok: true, json: async () => ({}) });
    globalThis.fetch = fetchMock;

    const { result } = renderHook(() =>
      useServerPreference<{ home: string } | null>(
        "location",
        null,
        (blob) => ({ home: blob.home as string }),
        (val) => ({ home: val?.home ?? "" }),
      ),
    );

    await act(async () => {
      await vi.runAllTimersAsync();
    });

    expect(result.current[0]).toEqual({ home: "London" });

    act(() => {
      result.current[1]({ home: "Paris" });
    });

    await act(async () => {
      vi.advanceTimersByTime(500);
    });

    const putCalls = fetchMock.mock.calls.filter(
      ([, opts]: [string, RequestInit]) => opts.method === "PUT",
    );
    expect(putCalls.length).toBe(1);
    expect(JSON.parse(putCalls[0][1].body as string)).toEqual({ home: "Paris" });
  });

  it("only sends one PUT for rapid successive changes", async () => {
    const fetchMock = vi.fn().mockResolvedValue({ ok: true, json: async () => ({}) });
    globalThis.fetch = fetchMock;

    const { result } = renderHook(() =>
      useServerPreference("theme", "dark"),
    );

    await act(async () => {
      await vi.runAllTimersAsync();
    });
    const initialCallCount = fetchMock.mock.calls.length;

    act(() => {
      result.current[1]("a");
      result.current[1]("b");
      result.current[1]("c");
    });

    await act(async () => {
      vi.advanceTimersByTime(500);
    });

    const putCalls = fetchMock.mock.calls.slice(initialCallCount);
    expect(putCalls.length).toBe(1);
    expect(JSON.parse(putCalls[0][1].body as string)).toEqual({ value: "c" });
  });
});
