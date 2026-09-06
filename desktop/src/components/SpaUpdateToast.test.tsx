import { describe, it, expect, vi, beforeEach } from "vitest";
import { render } from "@testing-library/react";
import { SpaUpdateToast } from "./SpaUpdateToast";

const mockAddNotification = vi.fn();

vi.mock("@/hooks/use-spa-version-check", () => ({
  useSpaVersionCheck: vi.fn(),
}));

vi.mock("@/stores/notification-store", () => ({
  useNotificationStore: (selector: (state: { addNotification: typeof mockAddNotification }) => unknown) =>
    selector({ addNotification: mockAddNotification }),
}));

// Must import after mocks so the mocked module is used.
import { useSpaVersionCheck } from "@/hooks/use-spa-version-check";

describe("SpaUpdateToast", () => {
  beforeEach(() => {
    mockAddNotification.mockClear();
    vi.mocked(useSpaVersionCheck).mockReturnValue({
      hasNewBuild: false,
      deployedVersion: null,
    });
  });

  it("renders nothing", () => {
    const { container } = render(<SpaUpdateToast />);
    expect(container.innerHTML).toBe("");
  });

  it("does not fire when hasNewBuild is false", () => {
    render(<SpaUpdateToast />);
    expect(mockAddNotification).not.toHaveBeenCalled();
  });

  it("fires notification when a new build is available", () => {
    vi.mocked(useSpaVersionCheck).mockReturnValue({
      hasNewBuild: true,
      deployedVersion: "1.0.0+new.new.new",
    });

    render(<SpaUpdateToast />);
    expect(mockAddNotification).toHaveBeenCalledTimes(1);
    expect(mockAddNotification).toHaveBeenCalledWith({
      source: "system",
      level: "info",
      title: "New taOS build available",
      body: expect.stringContaining("Reload to update"),
    });
  });

  it("does not re-fire for the same deployed version on re-render", () => {
    vi.mocked(useSpaVersionCheck).mockReturnValue({
      hasNewBuild: true,
      deployedVersion: "1.0.0+new.new.new",
    });

    const { rerender } = render(<SpaUpdateToast />);
    expect(mockAddNotification).toHaveBeenCalledTimes(1);

    rerender(<SpaUpdateToast />);
    expect(mockAddNotification).toHaveBeenCalledTimes(1);
  });

  it("fires again when deployed version changes again", () => {
    vi.mocked(useSpaVersionCheck).mockReturnValue({
      hasNewBuild: true,
      deployedVersion: "1.0.0+first.first",
    });

    render(<SpaUpdateToast />);
    expect(mockAddNotification).toHaveBeenCalledTimes(1);

    // New deployed version.
    vi.mocked(useSpaVersionCheck).mockReturnValue({
      hasNewBuild: true,
      deployedVersion: "1.0.0+second.second",
    });

    render(<SpaUpdateToast />);
    expect(mockAddNotification).toHaveBeenCalledTimes(2);
  });
});
