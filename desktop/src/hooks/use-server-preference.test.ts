import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { renderHook, act } from "@testing-library/react";
import { useServerPreference } from "./use-server-preference";

describe("useServerPreference", () => {
  const originalFetch = globalThis.fetch;
  const storage: Record<string, string> = {};

  beforeEach(() => {
    vi.stubGlobal("fetch", vi.fn());
    Object.defineProperty(window, "localStorage", {
      configurable: true,
      value: {
        getItem: (k: string) => storage[k] ?? null,
        setItem: (k: string, v: string) => { storage[k] = v; },
        removeItem: (k: string) => { delete storage[k]; },
        clear: () => { for (const k of Object.keys(storage)) delete storage[k]; },
        key: (i: number) => Object.keys(storage)[i] ?? null,
        get length() { return Object.keys(storage).length; },
      },
    });
  });

  afterEach(() => {
    vi.restoreAllMocks();
    for (const k of Object.keys(storage)) delete storage[k];
  });

  it("returns defaultValue and loaded=false before fetch resolves", () => {
    vi.mocked(fetch).mockReturnValue(new Promise(() => {}));
    const { result } = renderHook(() =>
      useServerPreference<string>("greeting", "hi"),
    );
    expect(result.current[0]).toBe("hi");
    expect(result.current[2].loaded).toBe(false);
  });

  it("hydrates from server when fetch returns a non-empty blob", async () => {
    vi.mocked(fetch).mockResolvedValue(
      new Response(JSON.stringify({ value: "from-server" }), {
        status: 200,
        headers: { "Content-Type": "application/json" },
      }),
    );
    const { result } = renderHook(() =>
      useServerPreference<string>("greeting", "default", (blob) => blob.value as string),
    );
    await act(async () => {});
    expect(result.current[0]).toBe("from-server");
    expect(result.current[2].loaded).toBe(true);
  });

  it("keeps defaultValue when fetch returns an empty object", async () => {
    vi.mocked(fetch).mockResolvedValue(
      new Response("{}", {
        status: 200,
        headers: { "Content-Type": "application/json" },
      }),
    );
    const { result } = renderHook(() =>
      useServerPreference<string>("greeting", "default"),
    );
    await act(async () => {});
    expect(result.current[0]).toBe("default");
    expect(result.current[2].loaded).toBe(true);
  });

  it("keeps defaultValue when fetch responds non-200", async () => {
    vi.mocked(fetch).mockResolvedValue(new Response("{}", { status: 500 }));
    const { result } = renderHook(() =>
      useServerPreference<string>("greeting", "default"),
    );
    await act(async () => {});
    expect(result.current[0]).toBe("default");
    expect(result.current[2].loaded).toBe(true);
  });

  it("keeps defaultValue when fetch throws", async () => {
    vi.mocked(fetch).mockRejectedValue(new Error("network"));
    const { result } = renderHook(() =>
      useServerPreference<string>("greeting", "default"),
    );
    await act(async () => {});
    expect(result.current[0]).toBe("default");
    expect(result.current[2].loaded).toBe(true);
  });

  it("setValue updates local state and localStorage immediately", async () => {
    vi.mocked(fetch).mockResolvedValue(
      new Response("{}", { status: 200, headers: { "Content-Type": "application/json" } }),
    );
    const { result } = renderHook(() =>
      useServerPreference<string>("greeting", "default"),
    );
    await act(async () => {});

    act(() => result.current[1]("new-value"));
    expect(result.current[0]).toBe("new-value");
    expect(JSON.parse(storage["taos-pref:greeting"])).toBe("new-value");
  });

  it("setValue uses functional updater", async () => {
    vi.mocked(fetch).mockResolvedValue(
      new Response("{}", { status: 200, headers: { "Content-Type": "application/json" } }),
    );
    const { result } = renderHook(() =>
      useServerPreference<number>("counter", 0),
    );
    await act(async () => {});

    act(() => result.current[1]((prev) => prev + 5));
    expect(result.current[0]).toBe(5);
  });

  it("setValue debounces the PUT to the server", async () => {
    vi.useFakeTimers();
    vi.mocked(fetch).mockResolvedValue(
      new Response("{}", { status: 200, headers: { "Content-Type": "application/json" } }),
    );
    const { result } = renderHook(() =>
      useServerPreference<string>("greeting", "default"),
    );
    await act(async () => {});

    act(() => result.current[1]("a"));
    act(() => result.current[1]("ab"));
    const putCalls = vi.mocked(fetch).mock.calls.filter((c) => (c[1] as RequestInit)?.method === "PUT");
    expect(putCalls.length).toBe(0);

    await act(async () => { vi.advanceTimersByTime(500); });
    const putCallsAfter = vi.mocked(fetch).mock.calls.filter((c) => (c[1] as RequestInit)?.method === "PUT");
    expect(putCallsAfter.length).toBe(1);
    expect(JSON.parse((putCallsAfter[0][1] as RequestInit).body as string)).toEqual({ value: "ab" });

    vi.useRealTimers();
  });

  it("reads initial value from localStorage when present", () => {
    storage["taos-pref:greeting"] = JSON.stringify("cached");
    vi.mocked(fetch).mockReturnValue(new Promise(() => {}));
    const { result } = renderHook(() =>
      useServerPreference<string>("greeting", "default"),
    );
    expect(result.current[0]).toBe("cached");
  });

  it("extract/pack projectors shape server payload", async () => {
    vi.mocked(fetch).mockResolvedValue(
      new Response(JSON.stringify({ home: "earth" }), {
        status: 200,
        headers: { "Content-Type": "application/json" },
      }),
    );
    const extract = (blob: Record<string, unknown>) => blob.home as string;
    const pack = (value: string) => ({ home: value });
    const { result } = renderHook(() =>
      useServerPreference<string>("weather", "default", extract, pack),
    );
    await act(async () => {});
    expect(result.current[0]).toBe("earth");
  });
});
