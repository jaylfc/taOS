import { describe, it, expect } from "vitest";
import { render, screen, fireEvent } from "@testing-library/react";
import { CalendarApp } from "./CalendarApp";

describe("CalendarApp day selection", () => {
  it("marks the tapped day selected (aria-selected + accent style) and shows the day panel", () => {
    render(<CalendarApp windowId="test" />);

    // The grid renders one button per cell; the today cell starts selected.
    const buttons = screen.getAllByTestId("calendar-day");
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
    const buttons = screen.getAllByTestId("calendar-day");
    const selected = buttons.find((b) => b.getAttribute("aria-selected") === "true")!;
    // The accent background marks the selected day visually.
    expect(selected.className).toContain("bg-accent");
  });

  it("round-trips other-month day keys so the panel shows the clicked day", () => {
    render(<CalendarApp windowId="test" />);
    const today = new Date();
    const cells = screen.getAllByTestId("calendar-day");

    // Trailing cells of the next month carry single-digit day numbers —
    // exactly the case the old non-padded key ("2025-5-15") parsed wrong in
    // some engines. Find the first such cell.
    const other = cells.find((b) => {
      const label = b.getAttribute("aria-label") ?? "";
      return (
        label.includes("(other month)") && /^\d$/.test(b.textContent?.trim() ?? "")
      );
    }) as HTMLElement | undefined;
    expect(other).toBeDefined();
    const day = other!.textContent!.trim();

    // The clicked cell belongs to the next month; compute the heading the
    // panel should name so the assertion is independent of today's date.
    const nextMonth = (today.getMonth() + 1) % 12;
    const nextYear = today.getMonth() === 11 ? today.getFullYear() + 1 : today.getFullYear();
    const expected = new Date(nextYear, nextMonth, Number(day)).toLocaleDateString(
      "en-US",
      { year: "numeric", month: "long", day: "numeric" },
    );

    fireEvent.click(other!);

    const panel = screen.getByRole("tabpanel", { name: /agenda for/i });
    expect(panel.textContent).toContain(expected);
  });
});