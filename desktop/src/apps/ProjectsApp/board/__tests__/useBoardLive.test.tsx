import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { renderHook, act } from "@testing-library/react";
import { useBoardLive } from "../useBoardLive";
import { projectsApi } from "../../../../lib/projects";

beforeEach(() => {
  vi.restoreAllMocks();
});

describe("useBoardLive", () => {
  it("subscribes via projectsApi and forwards events", () => {
    let captured: ((e: any) => void) | null = null;
    const unsub = vi.fn();
    vi.spyOn(projectsApi, "subscribeEvents").mockImplementation((_pid, cb) => {
      captured = cb;
      return unsub;
    });
    const onEvent = vi.fn();
    const { unmount } = renderHook(() => useBoardLive("p1", onEvent));
    act(() => captured?.({ kind: "task.created", payload: { id: "t1" }, ts: 0 }));
    expect(onEvent).toHaveBeenCalledWith(expect.objectContaining({ kind: "task.created" }));
    unmount();
    expect(unsub).toHaveBeenCalled();
  });

  it("exposes connected status (true after subscribe)", () => {
    let onOpen: (() => void) | undefined;
    vi.spyOn(projectsApi, "subscribeEvents").mockImplementation((_pid, _cb, opts) => {
      onOpen = opts?.onOpen;
      return () => {};
    });
    const { result } = renderHook(() => useBoardLive("p1", () => {}));
    expect(result.current.connected).toBe(false);
    if (onOpen) {
      act(() => onOpen());
    }
    expect(result.current.connected).toBe(true);
  });

  it("sets connected=false when the SSE connection errors", async () => {
    let capturedOnError: (() => void) | undefined;
    vi.spyOn(projectsApi, "subscribeEvents").mockImplementation(
      (_url: string, _onEvent: (e: unknown) => void, opts?: { onError?: () => void }) => {
        capturedOnError = opts?.onError;
        return () => {};
      },
    );

    const { result } = renderHook(() => useBoardLive("p1", () => {}));

    await act(async () => {
      await Promise.resolve();
    });

    expect(capturedOnError).toBeDefined();

    if (capturedOnError) {
      await act(async () => {
        capturedOnError();
      });
      expect(result.current.connected).toBe(false);
    }
  });
});
