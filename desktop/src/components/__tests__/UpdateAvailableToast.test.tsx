import { describe, it, expect, vi, beforeEach } from "vitest";
import { render } from "@testing-library/react";
import { UpdateAvailableToast } from "../UpdateAvailableToast";

vi.mock("@/contexts/BackendStatusContext", () => ({
  useBackendStatus: vi.fn(),
}));
vi.mock("@/stores/notification-store", () => ({
  useNotificationStore: vi.fn(),
}));

import { useBackendStatus } from "@/contexts/BackendStatusContext";
import { useNotificationStore } from "@/stores/notification-store";

const BUILD_VERSION = "0.1.0";

describe("<UpdateAvailableToast />", () => {
  let addNotification: ReturnType<typeof vi.fn>;
  beforeEach(() => {
    addNotification = vi.fn().mockReturnValue("id-1");
    vi.mocked(useNotificationStore).mockImplementation((selector?: any) => {
      const state = { addNotification };
      return selector ? selector(state) : state;
    });
    vi.mocked(useBackendStatus).mockReset();
  });

  it("does nothing when current version equals build version", () => {
    vi.mocked(useBackendStatus).mockReturnValue({
      status: "up", currentVersion: BUILD_VERSION, secondsReconnecting: 0,
    });
    render(<UpdateAvailableToast buildVersion={BUILD_VERSION} />);
    expect(addNotification).not.toHaveBeenCalled();
  });

  it("does nothing when build version is a dev marker", () => {
    vi.mocked(useBackendStatus).mockReturnValue({
      status: "up", currentVersion: "9.9.9", secondsReconnecting: 0,
    });
    render(<UpdateAvailableToast buildVersion="dev" />);
    expect(addNotification).not.toHaveBeenCalled();
    render(<UpdateAvailableToast buildVersion="0.0.0-local" />);
    expect(addNotification).not.toHaveBeenCalled();
  });

  it("does nothing when current version is unknown", () => {
    vi.mocked(useBackendStatus).mockReturnValue({
      status: "up", currentVersion: null, secondsReconnecting: 0,
    });
    render(<UpdateAvailableToast buildVersion={BUILD_VERSION} />);
    expect(addNotification).not.toHaveBeenCalled();
  });

  it("emits a single notification when versions differ", () => {
    vi.mocked(useBackendStatus).mockReturnValue({
      status: "up", currentVersion: "0.2.0", secondsReconnecting: 0,
    });
    const { rerender } = render(<UpdateAvailableToast buildVersion={BUILD_VERSION} />);
    rerender(<UpdateAvailableToast buildVersion={BUILD_VERSION} />);
    expect(addNotification).toHaveBeenCalledTimes(1);
    const call = addNotification.mock.calls[0][0];
    expect(call.title).toMatch(/new taOS version/i);
    // The notification store has no actions[] — reload instruction is in the body.
    expect(call.body).toMatch(/reload/i);
  });

  it("ignores semver build metadata when comparing", () => {
    // Bundle version carries +sha for SW cache busting; backend version
    // header is the raw 0.1.0. These should be treated as equal.
    vi.mocked(useBackendStatus).mockReturnValue({
      status: "up", currentVersion: "0.1.0", secondsReconnecting: 0,
    });
    render(<UpdateAvailableToast buildVersion="0.1.0+a3bd632" />);
    expect(addNotification).not.toHaveBeenCalled();
  });
});
