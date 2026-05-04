import { describe, expect, it, beforeEach, vi } from "vitest";
import { render, screen, fireEvent } from "@testing-library/react";
import { Chrome } from "./Chrome";
import { useBrowserStore } from "@/stores/browser-store";

const TEST_WINDOW_ID = "win-test";

beforeEach(() => {
  // Reset store + create a fresh window with a tab that has history
  useBrowserStore.setState({ windows: {} });
  useBrowserStore.getState().createWindow(TEST_WINDOW_ID, "personal");
  const tabId = useBrowserStore.getState().getWindow(TEST_WINDOW_ID)!.tabs[0].id;
  useBrowserStore.getState().navigateTab(TEST_WINDOW_ID, tabId, "https://a.test/");
  useBrowserStore.getState().navigateTab(TEST_WINDOW_ID, tabId, "https://b.test/");
});

describe("Chrome — nav buttons", () => {
  it("renders back / forward / refresh buttons", () => {
    render(<Chrome windowId={TEST_WINDOW_ID} />);
    expect(screen.getByRole("button", { name: /back/i })).toBeTruthy();
    expect(screen.getByRole("button", { name: /forward/i })).toBeTruthy();
    expect(screen.getByRole("button", { name: /refresh|reload/i })).toBeTruthy();
  });

  it("back button calls browserStore.goBack", () => {
    const goBackSpy = vi.spyOn(useBrowserStore.getState(), "goBack");
    render(<Chrome windowId={TEST_WINDOW_ID} />);
    fireEvent.click(screen.getByRole("button", { name: /back/i }));
    expect(goBackSpy).toHaveBeenCalledWith(
      TEST_WINDOW_ID,
      expect.any(String),
    );
  });

  it("forward button calls browserStore.goForward", () => {
    const goForwardSpy = vi.spyOn(useBrowserStore.getState(), "goForward");
    // Step back so forward is enabled
    const tabId = useBrowserStore.getState().getWindow(TEST_WINDOW_ID)!.activeTabId;
    useBrowserStore.getState().goBack(TEST_WINDOW_ID, tabId);

    render(<Chrome windowId={TEST_WINDOW_ID} />);
    fireEvent.click(screen.getByRole("button", { name: /forward/i }));
    expect(goForwardSpy).toHaveBeenCalledWith(
      TEST_WINDOW_ID,
      expect.any(String),
    );
  });

  it("back button is disabled when there's no history to go back to", () => {
    // Reset to a window with a single tab and no nav
    useBrowserStore.setState({ windows: {} });
    useBrowserStore.getState().createWindow(TEST_WINDOW_ID, "personal");
    render(<Chrome windowId={TEST_WINDOW_ID} />);
    const backBtn = screen.getByRole("button", { name: /back/i }) as HTMLButtonElement;
    expect(backBtn.disabled).toBe(true);
  });

  it("forward button is disabled when at the head of history", () => {
    render(<Chrome windowId={TEST_WINDOW_ID} />);
    // The setup navigated twice forward; forward button should be disabled
    const fwdBtn = screen.getByRole("button", { name: /forward/i }) as HTMLButtonElement;
    expect(fwdBtn.disabled).toBe(true);
  });
});

describe("Chrome — profile chip", () => {
  it("renders profile chip with profile id (display-only for PR 4)", () => {
    render(<Chrome windowId={TEST_WINDOW_ID} />);
    // Match aria-label or visible text
    const chip = screen.getByLabelText(/profile|personal/i);
    expect(chip).toBeTruthy();
    expect(chip.textContent?.toLowerCase()).toContain("personal");
  });
});

describe("Chrome — graceful handling of missing window", () => {
  it("renders nothing when the windowId doesn't exist in the store", () => {
    const { container } = render(<Chrome windowId="missing-id" />);
    // Either renders an empty container or a clear placeholder — assert
    // that no nav buttons appear since there's no tab to act on.
    expect(container.querySelector("button")).toBeNull();
  });
});
