import { describe, expect, it, beforeEach, vi } from "vitest";
import { render, screen, fireEvent } from "@testing-library/react";
import { WindowChooser } from "./WindowChooser";
import { useBrowserStore } from "@/stores/browser-store";

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
});
