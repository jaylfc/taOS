import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { render, screen } from "@testing-library/react";
import { BackendBanner } from "./BackendBanner";

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
      value: originalLocation,
      writable: true,
      configurable: true,
    });
  });

  it("renders an empty live region when status is 'up'", () => {
    vi.mocked(useBackendStatus).mockReturnValue({
      status: "up",
      currentVersion: "0.1.0",
      secondsReconnecting: 0,
    });
    const { container } = render(<BackendBanner />);
    const region = container.querySelector('[role="status"]');
    expect(region).not.toBeNull();
    expect(region?.textContent?.trim() ?? "").toBe("");
  });

  it("renders 'taOS is restarting' when reconnecting under 60s", () => {
    vi.mocked(useBackendStatus).mockReturnValue({
      status: "reconnecting",
      currentVersion: null,
      secondsReconnecting: 15,
    });
    render(<BackendBanner />);
    expect(screen.getByText(/taOS is restarting/i)).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /refresh/i })).toBeNull();
  });

  it("does not show the refresh button at exactly 59s", () => {
    vi.mocked(useBackendStatus).mockReturnValue({
      status: "reconnecting",
      currentVersion: null,
      secondsReconnecting: 59,
    });
    render(<BackendBanner />);
    expect(screen.getByText(/taOS is restarting/i)).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /refresh/i })).toBeNull();
  });

  it("renders 'taking longer than usual' with refresh button at 60s", () => {
    vi.mocked(useBackendStatus).mockReturnValue({
      status: "reconnecting",
      currentVersion: null,
      secondsReconnecting: 60,
    });
    render(<BackendBanner />);
    expect(
      screen.getByText(/taking longer than usual/i),
    ).toBeInTheDocument();
    expect(
      screen.getByRole("button", { name: /refresh/i }),
    ).toBeInTheDocument();
  });

  it("renders 'taking longer than usual' with refresh button after 60s", () => {
    vi.mocked(useBackendStatus).mockReturnValue({
      status: "reconnecting",
      currentVersion: null,
      secondsReconnecting: 120,
    });
    render(<BackendBanner />);
    expect(
      screen.getByText(/taking longer than usual/i),
    ).toBeInTheDocument();
    expect(
      screen.getByRole("button", { name: /refresh/i }),
    ).toBeInTheDocument();
  });

  it("refresh button calls window.location.reload", () => {
    vi.mocked(useBackendStatus).mockReturnValue({
      status: "reconnecting",
      currentVersion: null,
      secondsReconnecting: 65,
    });
    const reload = vi.fn();
    Object.defineProperty(window, "location", {
      value: { reload },
      writable: true,
      configurable: true,
    });
    render(<BackendBanner />);
    screen.getByRole("button", { name: /refresh/i }).click();
    expect(reload).toHaveBeenCalledTimes(1);
  });

  it("has aria-live='polite' on the status region", () => {
    vi.mocked(useBackendStatus).mockReturnValue({
      status: "up",
      currentVersion: null,
      secondsReconnecting: 0,
    });
    const { container } = render(<BackendBanner />);
    const region = container.querySelector('[role="status"]');
    expect(region).toHaveAttribute("aria-live", "polite");
  });

  it("shows the spinner icon when reconnecting", () => {
    vi.mocked(useBackendStatus).mockReturnValue({
      status: "reconnecting",
      currentVersion: null,
      secondsReconnecting: 10,
    });
    const { container } = render(<BackendBanner />);
    expect(container.querySelector(".animate-spin")).not.toBeNull();
  });

  it("does not show the spinner icon when status is 'up'", () => {
    vi.mocked(useBackendStatus).mockReturnValue({
      status: "up",
      currentVersion: "0.1.0",
      secondsReconnecting: 0,
    });
    const { container } = render(<BackendBanner />);
    expect(container.querySelector(".animate-spin")).toBeNull();
  });
});
