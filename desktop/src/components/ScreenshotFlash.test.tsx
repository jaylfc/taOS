import { describe, it, expect, vi } from "vitest";
import { render, fireEvent, waitFor } from "@testing-library/react";
import { ScreenshotFlash } from "./ScreenshotFlash";

describe("ScreenshotFlash", () => {
  it("renders nothing before the flash event fires", () => {
    const { container } = render(<ScreenshotFlash />);
    expect(container.innerHTML).toBe("");
  });

  it("shows the flash overlay with the correct attributes when the event fires", async () => {
    const { container } = render(<ScreenshotFlash />);
    fireEvent(window, new CustomEvent("taos:screenshot-flash"));
    await waitFor(() => {
      const overlay = container.querySelector("[data-screenshot-exclude]");
      expect(overlay).not.toBeNull();
    });
    const overlay = container.querySelector("[data-screenshot-exclude]");
    expect(overlay).toHaveAttribute("aria-hidden", "true");
  });

  it("hides the overlay after the animation timeout", async () => {
    const { container } = render(<ScreenshotFlash />);
    fireEvent(window, new CustomEvent("taos:screenshot-flash"));
    await waitFor(() => {
      expect(container.querySelector("[data-screenshot-exclude]")).not.toBeNull();
    });
    await waitFor(() => {
      expect(container.innerHTML).toBe("");
    }, { timeout: 2000 });
  });
});
