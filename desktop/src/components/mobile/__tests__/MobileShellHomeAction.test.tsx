import { render, screen, act } from "@testing-library/react";
import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { BackendStatusProvider, __setBackendStatusSingletonForTest } from "@/contexts/BackendStatusContext";
import { BackendBanner } from "@/components/BackendBanner";
import { createBackendStatus } from "@/lib/backendStatus";

describe("Mobile shell with BackendBanner", () => {
  beforeEach(() => {
    vi.useFakeTimers();
  });
  afterEach(() => {
    vi.useRealTimers();
    vi.restoreAllMocks();
    __setBackendStatusSingletonForTest(null as any);
  });

  it("shows 'taOS is restarting...' banner when backend is reconnecting", async () => {
    const fetchMock = vi.fn().mockRejectedValue(new TypeError("network"));
    const testBs = createBackendStatus({
      healthUrl: "/api/health",
      fetchImpl: fetchMock,
    });
    __setBackendStatusSingletonForTest(testBs);

    render(
      <BackendStatusProvider>
        <BackendBanner />
      </BackendStatusProvider>
    );

    // Initially status is "up", banner should not be visible
    expect(screen.queryByText(/taOS is restarting/i)).not.toBeInTheDocument();

    // Advance to trigger first poll failure -> reconnecting
    await act(async () => {
      await vi.advanceTimersByTimeAsync(2000);
    });

    // Banner should now show "taOS is restarting..."
    expect(screen.getByText(/taOS is restarting/i)).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /refresh/i })).not.toBeInTheDocument();

    testBs.stop();
  });

  it("shows refresh button after 60s of reconnecting", async () => {
    const fetchMock = vi.fn().mockRejectedValue(new TypeError("network"));
    const testBs = createBackendStatus({
      healthUrl: "/api/health",
      fetchImpl: fetchMock,
    });
    __setBackendStatusSingletonForTest(testBs);

    render(
      <BackendStatusProvider>
        <BackendBanner />
      </BackendStatusProvider>
    );

    // Advance to trigger first poll failure -> reconnecting
    await act(async () => {
      await vi.advanceTimersByTimeAsync(2000);
    });

    expect(screen.getByText(/taOS is restarting/i)).toBeInTheDocument();

    // Advance past 60s threshold
    await act(async () => {
      await vi.advanceTimersByTimeAsync(60_000);
    });

    // Should show "taking longer than usual" with refresh button
    expect(screen.getByText(/taking longer than usual/i)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /refresh/i })).toBeInTheDocument();

    testBs.stop();
  });
});