import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { renderHook, waitFor } from "@testing-library/react";
import { useInstalledOptionalApps } from "./use-installed-optional-apps";
import { onAppEvent, emitAppEvent, APP_OPTIONAL_CHANGED } from "@/lib/app-event-bus";

describe("useInstalledOptionalApps", () => {
  beforeEach(() => {
    vi.stubGlobal("fetch", vi.fn());
  });

  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("returns an empty set before the fetch resolves", () => {
    let resolveJson: (value: { installed: string[] }) => void;
    (fetch as ReturnType<typeof vi.fn>).mockReturnValueOnce(
      new Promise(() => { /* pending */ }),
    );

    const { result } = renderHook(() => useInstalledOptionalApps());
    expect(result.current).toBeInstanceOf(Set);
    expect(result.current.size).toBe(0);
  });

  it("fetches installed optional apps and returns them as a Set", async () => {
    (fetch as ReturnType<typeof vi.fn>).mockResolvedValueOnce({
      ok: true,
      json: async () => ({ installed: ["reddit", "youtube"] }),
    });

    const { result } = renderHook(() => useInstalledOptionalApps());

    await waitFor(() => expect(result.current.size).toBe(2));
    expect(result.current.has("reddit")).toBe(true);
    expect(result.current.has("youtube")).toBe(true);
    expect(fetch).toHaveBeenCalledWith(
      "/api/apps/optional/installed",
      expect.objectContaining({ headers: { Accept: "application/json" } }),
    );
  });

  it("returns an empty set when the response is not ok", async () => {
    (fetch as ReturnType<typeof vi.fn>).mockResolvedValueOnce({
      ok: false,
      status: 500,
    });

    const { result } = renderHook(() => useInstalledOptionalApps());

    await waitFor(() => {
      expect(fetch).toHaveBeenCalled();
    });
    expect(result.current).toBeInstanceOf(Set);
    expect(result.current.size).toBe(0);
  });

  it("returns an empty set when fetch rejects", async () => {
    (fetch as ReturnType<typeof vi.fn>).mockRejectedValueOnce(
      new Error("network failure"),
    );

    const { result } = renderHook(() => useInstalledOptionalApps());

    await waitFor(() => {
      expect(fetch).toHaveBeenCalled();
    });
    expect(result.current).toBeInstanceOf(Set);
    expect(result.current.size).toBe(0);
  });

  it("re-fetches when APP_OPTIONAL_CHANGED event fires", async () => {
    (fetch as ReturnType<typeof vi.fn>).mockResolvedValueOnce({
      ok: true,
      json: async () => ({ installed: ["reddit"] }),
    });

    const { result } = renderHook(() => useInstalledOptionalApps());

    await waitFor(() => expect(result.current.has("reddit")).toBe(true));

    (fetch as ReturnType<typeof vi.fn>).mockResolvedValueOnce({
      ok: true,
      json: async () => ({ installed: ["reddit", "github", "x"] }),
    });

    emitAppEvent(APP_OPTIONAL_CHANGED, "github");

    await waitFor(() => expect(result.current.size).toBe(3));
    expect(result.current.has("github")).toBe(true);
    expect(result.current.has("x")).toBe(true);
  });

  it("returns an empty set when the response has no installed field", async () => {
    (fetch as ReturnType<typeof vi.fn>).mockResolvedValueOnce({
      ok: true,
      json: async () => ({}),
    });

    const { result } = renderHook(() => useInstalledOptionalApps());

    await waitFor(() => {
      expect(fetch).toHaveBeenCalled();
    });
    expect(result.current).toBeInstanceOf(Set);
    expect(result.current.size).toBe(0);
  });
});
