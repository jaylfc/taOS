import { describe, it, expect } from "vitest";
import { render, screen, fireEvent } from "@testing-library/react";
import { CalendarApp } from "./CalendarApp";

describe("CalendarApp day selection", () => {
  it("marks the tapped day selected (aria-selected + accent style) and shows the day panel", () => {
    render(<CalendarApp windowId="test" />);

    // The grid renders one button per cell; the today cell starts selected.
    const buttons = screen.getAllByRole("button").filter(
      (b) => b.getAttribute("aria-label") !== null && b.getAttribute("aria-selected") !== null,
    );
    expect(buttons.length).toBeGreaterThan(0);

    const todayButton = buttons.find((b) => b.getAttribute("aria-selected") === "true");
    expect(todayButton).toBeInTheDocument();

    // Pick a different day and tap it.
    const other = buttons.find((b) => b.getAttribute("aria-selected") !== "true")!;
    fireEvent.click(other);

    expect(other).toHaveAttribute("aria-selected", "true");
    expect(todayButton).toHaveAttribute("aria-selected", "false");

    // The day panel/agenda must show for the selected date, even with no
    // events — it must not vanish just because the day is empty.
    const panel = screen.getByRole("tabpanel", { name: /agenda for/i });
    expect(panel).toBeInTheDocument();
    expect(panel.textContent).toContain("No events.");
  });

  it("keeps the selected style on the button element itself", () => {
    render(<CalendarApp windowId="test" />);
    const buttons = screen.getAllByRole("button").filter(
      (b) => b.getAttribute("aria-selected") !== null,
    );
    const selected = buttons.find((b) => b.getAttribute("aria-selected") === "true")!;
    // The accent background marks the selected day visually.
    expect(selected.className).toContain("bg-accent");
  });
});