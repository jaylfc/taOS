import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen } from "@testing-library/react";
import { AppShell } from "./AppShell";

vi.mock("@/contexts/BackendStatusContext", () => ({
  BackendStatusProvider: ({ children }: { children: React.ReactNode }) => (
    <>{children}</>
  ),
  useBackendStatus: vi.fn(),
}));

vi.mock("./BackendBanner", () => ({
  BackendBanner: () => <div data-testid="backend-banner" />,
}));

vi.mock("./UpdateAvailableToast", () => ({
  UpdateAvailableToast: ({ buildVersion }: { buildVersion: string }) => (
    <div data-testid="update-toast">{buildVersion}</div>
  ),
}));

vi.mock("./AppErrorBoundary", () => ({
  AppErrorBoundary: ({ children }: { children: React.ReactNode }) => (
    <div data-testid="error-boundary">{children}</div>
  ),
}));

vi.mock("@/lib/sw-register", () => ({
  registerServiceWorker: vi.fn().mockResolvedValue(undefined),
}));

describe("<AppShell />", () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it("renders children inside the error boundary", () => {
    render(
      <AppShell>
        <div data-testid="child-content">hello world</div>
      </AppShell>,
    );
    expect(screen.getByTestId("error-boundary")).toBeInTheDocument();
    expect(screen.getByTestId("child-content")).toBeInTheDocument();
    expect(screen.getByText("hello world")).toBeInTheDocument();
  });

  it("renders the BackendBanner and UpdateAvailableToast", () => {
    render(
      <AppShell>
        <span>app</span>
      </AppShell>,
    );
    expect(screen.getByTestId("backend-banner")).toBeInTheDocument();
    expect(screen.getByTestId("update-toast")).toBeInTheDocument();
  });

  it("passes the build version to UpdateAvailableToast", () => {
    render(
      <AppShell>
        <span>app</span>
      </AppShell>,
    );
    expect(screen.getByTestId("update-toast")).toHaveTextContent("dev");
  });

  it("renders multiple children", () => {
    render(
      <AppShell>
        <div data-testid="a">A</div>
        <div data-testid="b">B</div>
      </AppShell>,
    );
    expect(screen.getByTestId("a")).toBeInTheDocument();
    expect(screen.getByTestId("b")).toBeInTheDocument();
  });
});
