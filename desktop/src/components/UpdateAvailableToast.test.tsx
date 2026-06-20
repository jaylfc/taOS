import { describe, it, expect, vi, beforeEach } from "vitest";
import { render } from "@testing-library/react";
import { UpdateAvailableToast } from "./UpdateAvailableToast";

vi.mock("@/contexts/BackendStatusContext", () => ({
  useBackendStatus: vi.fn(),
}));
vi.mock("@/stores/notification-store", () => ({
  useNotificationStore: vi.fn(),
}));

import { useBackendStatus } from "@/contexts/BackendStatusContext";
import { useNotificationStore } from "@/stores/notification-store";

describe("UpdateAvailableToast", () => {
  let addNotification: ReturnType<typeof vi.fn>;

  beforeEach(() => {
    addNotification = vi.fn().mockReturnValue("notif-1");
    vi.mocked(useNotificationStore).mockImplementation((selector?: any) => {
      const state = { addNotification };
      return selector ? selector(state) : state;
    });
    vi.mocked(useBackendStatus).mockReset();
  });

  it("does not fire when versions match", () => {
    vi.mocked(useBackendStatus).mockReturnValue({
      status: "up",
      currentVersion: "0.1.0",
      secondsReconnecting: 0,
    });
    render(<UpdateAvailableToast buildVersion="0.1.0" />);
    expect(addNotification).not.toHaveBeenCalled();
  });

  it("fires a notification when backend version is newer", () => {
    vi.mocked(useBackendStatus).mockReturnValue({
      status: "up",
      currentVersion: "0.2.0",
      secondsReconnecting: 0,
    });
    render(<UpdateAvailableToast buildVersion="0.1.0" />);
    expect(addNotification).toHaveBeenCalledTimes(1);
    const payload = addNotification.mock.calls[0][0];
    expect(payload.title).toMatch(/new taOS version/i);
    expect(payload.level).toBe("info");
    expect(payload.body).toContain("0.2.0");
  });

  it("does nothing in dev builds", () => {
    vi.mocked(useBackendStatus).mockReturnValue({
      status: "up",
      currentVersion: "9.9.9",
      secondsReconnecting: 0,
    });
    render(<UpdateAvailableToast buildVersion="dev" />);
    expect(addNotification).not.toHaveBeenCalled();
  });
});
