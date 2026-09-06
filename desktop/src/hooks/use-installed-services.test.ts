import { renderHook, waitFor } from "@testing-library/react";
import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { useInstalledServices } from "./use-installed-services";
import { onAppEvent } from "@/lib/app-event-bus";

vi.mock("@/lib/app-event-bus", () => ({
  onAppEvent: vi.fn().mockReturnValue(() => {}),
  APP_INSTALLED: "app.installed",
}));

describe("useInstalledServices", () => {
  beforeEach(() => {
    vi.stubGlobal("fetch", vi.fn());
  });

  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("returns an empty array before the fetch resolves", () => {
    (fetch as ReturnType<typeof vi.fn>).mockReturnValue(new Promise(() => {}));
    const { result } = renderHook(() => useInstalledServices());
    expect(result.current).toEqual([]);
  });

  it("fetches /api/apps/installed on mount and populates services", async () => {
    const mockServices = [
      {
        app_id: "firefox",
        display_name: "Firefox",
        icon: null,
        url: "http://localhost:3000",
        category: "browser",
        backend: "docker",
        status: "running" as const,
      },
      {
        app_id: "code-server",
        display_name: "Code Server",
        icon: null,
        url: "http://localhost:8080",
        category: "development",
        backend: "docker",
        status: "stopped" as const,
      },
    ];

    (fetch as ReturnType<typeof vi.fn>).mockResolvedValueOnce({
      ok: true,
      json: async () => mockServices,
    });

    const { result } = renderHook(() => useInstalledServices());

    await waitFor(() => expect(result.current).toHaveLength(2));
    expect(fetch).toHaveBeenCalledWith("/api/apps/installed");
    expect(result.current).toEqual(mockServices);
  });

  it("returns an empty array when the response is not ok", async () => {
    (fetch as ReturnType<typeof vi.fn>).mockResolvedValueOnce({
      ok: false,
      status: 500,
    });

    const { result } = renderHook(() => useInstalledServices());

    await waitFor(() => expect(fetch).toHaveBeenCalled());
    expect(result.current).toEqual([]);
  });

  it("returns an empty array when fetch rejects", async () => {
    (fetch as ReturnType<typeof vi.fn>).mockRejectedValueOnce(
      new Error("Network error"),
    );

    const { result } = renderHook(() => useInstalledServices());

    await waitFor(() => expect(fetch).toHaveBeenCalled());
    expect(result.current).toEqual([]);
  });

  it("re-fetches when an app.installed event fires", async () => {
    const firstBatch = [
      {
        app_id: "firefox",
        display_name: "Firefox",
        icon: null,
        url: "http://localhost:3000",
        category: "browser",
        backend: "docker",
        status: "running" as const,
      },
    ];

    const secondBatch = [
      ...firstBatch,
      {
        app_id: "code-server",
        display_name: "Code Server",
        icon: null,
        url: "http://localhost:8080",
        category: "development",
        backend: "docker",
        status: "running" as const,
      },
    ];

    (fetch as ReturnType<typeof vi.fn>)
      .mockResolvedValueOnce({
        ok: true,
        json: async () => firstBatch,
      })
      .mockResolvedValueOnce({
        ok: true,
        json: async () => secondBatch,
      });

    let appInstalledCallback: (() => void) | undefined;
    (onAppEvent as ReturnType<typeof vi.fn>).mockImplementation(
      (_name: string, cb: () => void) => {
        appInstalledCallback = cb;
        return () => {};
      },
    );

    const { result } = renderHook(() => useInstalledServices());

    await waitFor(() => expect(result.current).toHaveLength(1));
    expect(fetch).toHaveBeenCalledTimes(1);

    appInstalledCallback!();

    await waitFor(() => expect(result.current).toHaveLength(2));
    expect(fetch).toHaveBeenCalledTimes(2);
  });
});
