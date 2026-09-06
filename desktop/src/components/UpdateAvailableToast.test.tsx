import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, act } from "@testing-library/react";
import { UpdateAvailableToast } from "./UpdateAvailableToast";

const mockAddNotification = vi.fn();
const mockCurrentVersion: { value: string | null } = { value: null };

vi.mock("@/contexts/BackendStatusContext", () => ({
  useBackendStatus: () => ({
    currentVersion: mockCurrentVersion.value,
  }),
}));

vi.mock("@/stores/notification-store", () => ({
  useNotificationStore: (selector: (state: { addNotification: typeof mockAddNotification }) => unknown) =>
    selector({ addNotification: mockAddNotification }),
}));

describe("UpdateAvailableToast", () => {
  beforeEach(() => {
    mockAddNotification.mockClear();
    mockCurrentVersion.value = null;
  });

  it("renders nothing", () => {
    const { container } = render(<UpdateAvailableToast buildVersion="1.0.0" />);
    expect(container.innerHTML).toBe("");
  });

  it("does not fire notification when currentVersion is null", () => {
    mockCurrentVersion.value = null;
    render(<UpdateAvailableToast buildVersion="1.0.0" />);
    expect(mockAddNotification).not.toHaveBeenCalled();
  });

  it("does not fire notification when versions match", () => {
    mockCurrentVersion.value = "1.0.0";
    render(<UpdateAvailableToast buildVersion="1.0.0" />);
    expect(mockAddNotification).not.toHaveBeenCalled();
  });

  it("does not fire notification for dev build version", () => {
    mockCurrentVersion.value = "1.0.1";
    render(<UpdateAvailableToast buildVersion="dev-abc123" />);
    expect(mockAddNotification).not.toHaveBeenCalled();
  });

  it("does not fire notification for 0.0.0 build version", () => {
    mockCurrentVersion.value = "1.0.1";
    render(<UpdateAvailableToast buildVersion="0.0.0+xyz" />);
    expect(mockAddNotification).not.toHaveBeenCalled();
  });

  it("fires notification when backend version differs from build version", () => {
    mockCurrentVersion.value = "1.0.1";
    render(<UpdateAvailableToast buildVersion="1.0.0" />);
    expect(mockAddNotification).toHaveBeenCalledTimes(1);
    expect(mockAddNotification).toHaveBeenCalledWith({
      source: "system",
      level: "info",
      title: "New taOS version available",
      body: "Reload to upgrade from 1.0.0 to 1.0.1.",
    });
  });

  it("strips build metadata from versions before comparing", () => {
    mockCurrentVersion.value = "1.0.0+a3bd632";
    render(<UpdateAvailableToast buildVersion="1.0.0" />);
    expect(mockAddNotification).not.toHaveBeenCalled();
  });

  it("does not re-fire notification for the same version on re-render", () => {
    mockCurrentVersion.value = "1.0.1";
    const { rerender } = render(<UpdateAvailableToast buildVersion="1.0.0" />);
    expect(mockAddNotification).toHaveBeenCalledTimes(1);
    rerender(<UpdateAvailableToast buildVersion="1.0.0" />);
    expect(mockAddNotification).toHaveBeenCalledTimes(1);
  });
});
