import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen } from "@testing-library/react";
import { AppShell } from "./AppShell";
import { registerServiceWorker } from "@/lib/sw-register";

const mockAddNotification = vi.fn();

const mockStatus = { value: "up" as const };
const mockSeconds = { value: 0 };

vi.mock("@/lib/taos-fetch", () => {
  class BackendUnavailableError extends Error {
    constructor(message?: string) {
      super(message ?? "Backend is unavailable");
      this.name = "BackendUnavailableError";
    }
  }
  return { BackendUnavailableError };
});

vi.mock("@/contexts/BackendStatusContext", () => ({
  useBackendStatus: () => ({
    status: mockStatus.value,
    currentVersion: null,
    secondsReconnecting: mockSeconds.value,
  }),
  BackendStatusProvider: ({ children }: { children: React.ReactNode }) => <>{children}</>,
}));

vi.mock("@/stores/notification-store", () => ({
  useNotificationStore: (selector: (state: { addNotification: typeof mockAddNotification }) => unknown) =>
    selector({ addNotification: mockAddNotification }),
}));

vi.mock("@/lib/sw-register", () => ({
  registerServiceWorker: vi.fn().mockResolvedValue(undefined),
}));

vi.mock("@/components/BackendBanner", () => ({
  BackendBanner: () => {
    if (mockStatus.value === "up") return null;
    const takingLong = mockSeconds.value >= 60;
    return (
      <div role="status" aria-live="polite">
        <div data-testid="backend-banner">
          {takingLong ? "taOS is taking longer than usual." : "taOS is restarting\u2026"}
        </div>
      </div>
    );
  },
}));

vi.mock("@/components/UpdateAvailableToast", () => ({
  UpdateAvailableToast: () => null,
}));

vi.mock("@/components/AppErrorBoundary", () => ({
  AppErrorBoundary: ({ children }: { children: React.ReactNode }) => <>{children}</>,
}));

describe("AppShell", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mockStatus.value = "up";
    mockSeconds.value = 0;
  });

  it("renders children inside the shell", () => {
    render(
      <AppShell>
        <div data-testid="child-content">hello world</div>
      </AppShell>,
    );
    expect(screen.getByTestId("child-content")).toBeInTheDocument();
    expect(screen.getByText("hello world")).toBeInTheDocument();
  });

  it("does not show BackendBanner when status is up", () => {
    mockStatus.value = "up";
    render(
      <AppShell>
        <span>app content</span>
      </AppShell>,
    );
    expect(screen.queryByTestId("backend-banner")).not.toBeInTheDocument();
    expect(screen.getByText("app content")).toBeInTheDocument();
  });

  it("shows restarting banner when status is reconnecting under 60s", () => {
    mockStatus.value = "reconnecting";
    mockSeconds.value = 30;
    render(
      <AppShell>
        <span>app content</span>
      </AppShell>,
    );
    expect(screen.getByTestId("backend-banner")).toBeInTheDocument();
    expect(screen.getByText(/restarting/)).toBeInTheDocument();
    expect(screen.queryByText(/taking longer/)).not.toBeInTheDocument();
  });

  it("shows taking-longer banner when reconnecting 60s or more", () => {
    mockStatus.value = "reconnecting";
    mockSeconds.value = 60;
    render(
      <AppShell>
        <span>app content</span>
      </AppShell>,
    );
    expect(screen.getByTestId("backend-banner")).toBeInTheDocument();
    expect(screen.getByText("taOS is taking longer than usual.")).toBeInTheDocument();
    expect(screen.queryByText(/restarting/)).not.toBeInTheDocument();
  });

  it("registers the service worker on mount", () => {
    render(
      <AppShell>
        <span>content</span>
      </AppShell>,
    );
    expect(registerServiceWorker).toHaveBeenCalledTimes(1);
  });
});
