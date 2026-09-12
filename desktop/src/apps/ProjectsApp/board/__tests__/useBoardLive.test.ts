import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { renderHook, act } from "@testing-library/react";

vi.mock("@/lib/projects", () => ({
  projectsApi: {
    subscribeEvents: vi.fn(),
  },
}));

import { projectsApi } from "@/lib/projects";
import { useBoardLive } from "../useBoardLive";

describe("useBoardLive", () => {
  let unsubscribe: ReturnType<typeof vi.fn>;

  beforeEach(() => {
    unsubscribe = vi.fn();
    (projectsApi.subscribeEvents as ReturnType<typeof vi.fn>).mockReturnValue(unsubscribe);
  });

  afterEach(() => {
    vi.clearAllMocks();
  });

  it("sets connected=false when the SSE connection errors", async () => {
    let capturedOnError: (() => void) | undefined;
    (projectsApi.subscribeEvents as ReturnType<typeof vi.fn>).mockImplementation(
      (_url: string, _onEvent: (e: unknown) => void, opts?: { onError?: () => void }) => {
        capturedOnError = opts?.onError;
        return unsubscribe;
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
