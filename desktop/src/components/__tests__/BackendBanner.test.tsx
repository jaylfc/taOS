import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { render, screen } from "@testing-library/react";
import { BackendBanner } from "../BackendBanner";

vi.mock("@/contexts/BackendStatusContext", () => ({
  useBackendStatus: vi.fn(),
}));
import { useBackendStatus } from "@/contexts/BackendStatusContext";

describe("<BackendBanner />", () => {
  let originalLocation: Location;

  beforeEach(() => {
    vi.mocked(useBackendStatus).mockReset();
    originalLocation = window.location;
  });

  afterEach(() => {
    Object.defineProperty(window, "location", {
      value: originalLocation, writable: true, configurable: true,
    });
  });

  it("renders an empty live region when status is 'up' (so screen readers attach before content)", () => {
    vi.mocked(useBackendStatus).mockReturnValue({
      status: "up", currentVersion: "0.1.0", secondsReconnecting: 0,
    });
    const { container } = render(<BackendBanner />);
    // Live region wrapper is always present (a11y); empty when up.
    const region = container.querySelector('[role="status"]');
    expect(region).not.toBeNull();
    expect(region?.textContent?.trim() ?? "").toBe("");
  });

  it("renders 'taOS is restarting…' when reconnecting under 60s", () => {
    vi.mocked(useBackendStatus).mockReturnValue({
      status: "reconnecting", currentVersion: null, secondsReconnecting: 15,
    });
    render(<BackendBanner />);
    expect(screen.getByText(/taOS is restarting/i)).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /refresh/i })).toBeNull();
  });

  it("renders 'taking longer than usual' with refresh button after 60s", () => {
    vi.mocked(useBackendStatus).mockReturnValue({
      status: "reconnecting", currentVersion: null, secondsReconnecting: 65,
    });
    render(<BackendBanner />);
    expect(screen.getByText(/taking longer than usual/i)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /refresh/i })).toBeInTheDocument();
  });

  it("refresh button calls window.location.reload", () => {
    vi.mocked(useBackendStatus).mockReturnValue({
      status: "reconnecting", currentVersion: null, secondsReconnecting: 65,
    });
    const reload = vi.fn();
    Object.defineProperty(window, "location", {
      value: { reload }, writable: true, configurable: true,
    });
    render(<BackendBanner />);
    screen.getByRole("button", { name: /refresh/i }).click();
    expect(reload).toHaveBeenCalled();
  });
});
