import { describe, expect, it, beforeEach, vi } from "vitest";
import { render, screen, fireEvent } from "@testing-library/react";
import { WindowChooser } from "./WindowChooser";
import { useBrowserStore } from "@/stores/browser-store";
import { useProcessStore } from "@/stores/process-store";

beforeEach(() => {
  useBrowserStore.setState({ windows: {} });
});

describe("WindowChooser", () => {
  it("renders all browser windows + New window button", () => {
    useBrowserStore.getState().createWindow("win-a", "personal");
    useBrowserStore.getState().createWindow("win-b", "work");

    render(
      <WindowChooser
        currentWindowId="win-a"
        onSelect={() => {}}
        onClose={() => {}}
      />,
    );

    expect(screen.getAllByRole("option").length).toBe(2);
    expect(screen.getByText(/new window/i)).toBeTruthy();
  });

  it("marks the current window with aria-selected", () => {
    useBrowserStore.getState().createWindow("win-a", "personal");
    useBrowserStore.getState().createWindow("win-b", "work");

    render(
      <WindowChooser
        currentWindowId="win-a"
        onSelect={() => {}}
        onClose={() => {}}
      />,
    );

    const opts = screen.getAllByRole("option");
    const current = opts.find((o) => o.getAttribute("aria-selected") === "true");
    expect(current?.textContent).toContain("personal");
  });

  it("clicking a window calls onSelect with its id and onClose", () => {
    useBrowserStore.getState().createWindow("win-a", "personal");
    useBrowserStore.getState().createWindow("win-b", "work");

    const onSelect = vi.fn();
    const onClose = vi.fn();
    render(
      <WindowChooser
        currentWindowId="win-a"
        onSelect={onSelect}
        onClose={onClose}
      />,
    );

    const opts = screen.getAllByRole("option");
    const winB = opts.find((o) => o.textContent?.includes("work"));
    fireEvent.click(winB!);
    expect(onSelect).toHaveBeenCalledWith("win-b");
    expect(onClose).toHaveBeenCalled();
  });

  it("close button calls onClose", () => {
    useBrowserStore.getState().createWindow("win-a", "personal");
    const onClose = vi.fn();
    render(
      <WindowChooser
        currentWindowId="win-a"
        onSelect={() => {}}
        onClose={onClose}
      />,
    );
    fireEvent.click(screen.getByLabelText("Close windows list"));
    expect(onClose).toHaveBeenCalled();
  });

  it("New window opens the browser IN-PAGE, so onSelect gets a real window id", () => {
    // On a taOSmobile handset a plain openWindow goes to the shell and returns
    // "", and onSelect("") would point the chooser at no window at all.
    const openWindow = vi.fn(() => "new-win-id");
    const real = useProcessStore.getState().openWindow;
    useProcessStore.setState({ openWindow });
    try {
      useBrowserStore.getState().createWindow("win-a", "personal");
      render(<WindowChooser currentWindowId="win-a" onSelect={() => {}} onClose={() => {}} />);
      fireEvent.click(screen.getByText(/new window/i));
      expect(openWindow).toHaveBeenCalledTimes(1);
      expect(openWindow.mock.calls[0][0]).toBe("browser");
      expect(openWindow.mock.calls[0][3]).toMatchObject({ inPage: true });
    } finally {
      useProcessStore.setState({ openWindow: real });
    }
  });
});

