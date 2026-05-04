import { describe, expect, it, beforeEach, vi, afterEach } from "vitest";
import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { ProfileSwitcher } from "./ProfileSwitcher";
import { useBrowserStore } from "@/stores/browser-store";

const TEST_WINDOW_ID = "win-test";
const originalFetch = global.fetch;

beforeEach(() => {
  useBrowserStore.setState({ windows: {} });
  useBrowserStore.getState().createWindow(TEST_WINDOW_ID, "personal");
  global.fetch = vi.fn().mockResolvedValue({
    ok: true,
    status: 200,
    json: async () => ({
      profiles: [
        { profile_id: "personal", name: "Personal", color: "#6c8df0", created_at: 0 },
        { profile_id: "work", name: "Work", color: "#f5b86b", created_at: 0 },
      ],
    }),
  });
});

afterEach(() => {
  global.fetch = originalFetch;
  vi.restoreAllMocks();
});

describe("ProfileSwitcher", () => {
  it("renders profiles after loading", async () => {
    render(
      <ProfileSwitcher windowId={TEST_WINDOW_ID} onClose={() => {}} />,
    );
    await waitFor(() => {
      const items = screen.getAllByRole("menuitem");
      // 2 profiles + "New profile" button = 3 menuitems minimum
      expect(items.length).toBeGreaterThanOrEqual(3);
    });
  });

  it("marks the active profile with aria-current", async () => {
    render(
      <ProfileSwitcher windowId={TEST_WINDOW_ID} onClose={() => {}} />,
    );
    await waitFor(() => {
      const items = screen.getAllByRole("menuitem");
      const active = items.find(
        (i) => i.getAttribute("aria-current") === "true",
      );
      expect(active?.textContent?.toLowerCase()).toContain("personal");
    });
  });

  it("clicking a different profile calls switchProfile + onClose", async () => {
    const switchSpy = vi.spyOn(useBrowserStore.getState(), "switchProfile");
    const onClose = vi.fn();
    render(
      <ProfileSwitcher windowId={TEST_WINDOW_ID} onClose={onClose} />,
    );
    await waitFor(() => screen.getAllByRole("menuitem"));

    const items = screen.getAllByRole("menuitem");
    const workBtn = items.find((i) =>
      i.textContent?.toLowerCase().includes("work"),
    );
    fireEvent.click(workBtn!);
    expect(switchSpy).toHaveBeenCalledWith(TEST_WINDOW_ID, "work");
    expect(onClose).toHaveBeenCalled();
  });

  it("clicking the active profile does NOT call switchProfile but does close", async () => {
    const switchSpy = vi.spyOn(useBrowserStore.getState(), "switchProfile");
    const onClose = vi.fn();
    render(
      <ProfileSwitcher windowId={TEST_WINDOW_ID} onClose={onClose} />,
    );
    await waitFor(() => screen.getAllByRole("menuitem"));

    const items = screen.getAllByRole("menuitem");
    const personalBtn = items.find((i) =>
      i.getAttribute("aria-current") === "true",
    );
    fireEvent.click(personalBtn!);
    expect(switchSpy).not.toHaveBeenCalled();
    expect(onClose).toHaveBeenCalled();
  });

  it("renders Manage footer when onManage prop provided", async () => {
    const onManage = vi.fn();
    render(
      <ProfileSwitcher
        windowId={TEST_WINDOW_ID}
        onClose={() => {}}
        onManage={onManage}
      />,
    );
    await waitFor(() => screen.getAllByRole("menuitem"));
    const manageBtn = screen.getByText(/manage profiles/i).closest("button");
    expect(manageBtn).toBeTruthy();
  });

  it("does NOT render Manage footer when onManage prop omitted", async () => {
    render(
      <ProfileSwitcher windowId={TEST_WINDOW_ID} onClose={() => {}} />,
    );
    await waitFor(() => screen.getAllByRole("menuitem"));
    expect(screen.queryByText(/manage profiles/i)).toBeNull();
  });
});
