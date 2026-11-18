import { render, screen, fireEvent } from "@testing-library/react";
import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { CalendarApp } from "./CalendarApp";

const FIXED_DATE = new Date(2025, 5, 15); // June 15, 2025

function mockToday() {
  vi.useFakeTimers();
  vi.setSystemTime(FIXED_DATE);
}

describe("CalendarApp", () => {
  beforeEach(() => {
    mockToday();
  });

  afterEach(() => {
    vi.useRealTimers();
  });

  it("renders the current month and year in the header", () => {
    render(<CalendarApp windowId="w1" />);
    expect(screen.getByText("June 2025")).toBeTruthy();
  });

  it("renders all seven day-of-week column headers", () => {
    render(<CalendarApp windowId="w1" />);
    expect(screen.getByText("Mon")).toBeTruthy();
    expect(screen.getByText("Tue")).toBeTruthy();
    expect(screen.getByText("Wed")).toBeTruthy();
    expect(screen.getByText("Thu")).toBeTruthy();
    expect(screen.getByText("Fri")).toBeTruthy();
    expect(screen.getByText("Sat")).toBeTruthy();
    expect(screen.getByText("Sun")).toBeTruthy();
  });

  it("renders the correct number of days for the current month", () => {
    render(<CalendarApp windowId="w1" />);
    // June 2025 has 30 days; day "1" appears as both a trailing prev-month day
    // and the first day of June, so use getAllByText for that one.
    const days1 = screen.getAllByText("1");
    expect(days1.length).toBeGreaterThanOrEqual(1);
    const day30 = screen.getAllByText("30");
    expect(day30.length).toBeGreaterThanOrEqual(1);
    // There are 30 current-month cells plus leading/trailing fill cells
    const currentMonthCells = document.querySelectorAll(
      ".text-shell-text.hover\\:bg-shell-surface",
    );
    expect(currentMonthCells.length).toBe(30);
  });

  it("navigates to the next month when the right arrow is clicked", () => {
    render(<CalendarApp windowId="w1" />);
    const nextBtn = screen.getByRole("button", { name: /next month/i });
    fireEvent.click(nextBtn);
    expect(screen.getByText("July 2025")).toBeTruthy();
  });

  it("navigates to the previous month when the left arrow is clicked", () => {
    render(<CalendarApp windowId="w1" />);
    const prevBtn = screen.getByRole("button", { name: /previous month/i });
    fireEvent.click(prevBtn);
    expect(screen.getByText("May 2025")).toBeTruthy();
  });

  it("wraps from January to December of the previous year when going prev", () => {
    // Set "today" to January 2025
    vi.setSystemTime(new Date(2025, 0, 10));
    render(<CalendarApp windowId="w1" />);
    expect(screen.getByText("January 2025")).toBeTruthy();
    const prevBtn = screen.getByRole("button", { name: /previous month/i });
    fireEvent.click(prevBtn);
    expect(screen.getByText("December 2024")).toBeTruthy();
  });

  it("wraps from December to January of the next year when going next", () => {
    // Set "today" to December 2025
    vi.setSystemTime(new Date(2025, 11, 10));
    render(<CalendarApp windowId="w1" />);
    expect(screen.getByText("December 2025")).toBeTruthy();
    const nextBtn = screen.getByRole("button", { name: /next month/i });
    fireEvent.click(nextBtn);
    expect(screen.getByText("January 2026")).toBeTruthy();
  });

  it("highlights today's date with the accent class", () => {
    render(<CalendarApp windowId="w1" />);
    // June 15 is "today" per our fixed clock; the cell should have the accent bg
    const todayCell = screen.getByText("15")?.closest("button");
    expect(todayCell?.className).toContain("bg-accent");
  });

  it("returns to the current month when Today is clicked after navigating", () => {
    render(<CalendarApp windowId="w1" />);
    const nextBtn = screen.getByRole("button", { name: /next month/i });
    fireEvent.click(nextBtn);
    fireEvent.click(nextBtn);
    expect(screen.getByText("August 2025")).toBeTruthy();
    const todayBtn = screen.getByRole("button", { name: /today/i });
    fireEvent.click(todayBtn);
    expect(screen.getByText("June 2025")).toBeTruthy();
  });
});

describe("CalendarApp day selection", () => {
  it("marks the tapped day selected (aria-pressed + accent style) and shows the day panel", () => {
    render(<CalendarApp windowId="test" />);

    // The grid renders one button per cell; the today cell starts selected.
    const buttons = screen.getAllByTestId("calendar-day");
    expect(buttons.length).toBeGreaterThan(0);

    const todayButton = buttons.find((b) => b.getAttribute("aria-pressed") === "true");
    expect(todayButton).toBeInTheDocument();

    // Pick a different day and tap it.
    const other = buttons.find((b) => b.getAttribute("aria-pressed") !== "true")!;
    fireEvent.click(other);

    expect(other).toHaveAttribute("aria-pressed", "true");
    expect(todayButton).toHaveAttribute("aria-pressed", "false");

    // The day panel/agenda must show for the selected date, even with no
    // events — it must not vanish just because the day is empty.
    const panel = screen.getByRole("tabpanel", { name: /agenda for/i });
    expect(panel).toBeInTheDocument();
    expect(panel.textContent).toContain("No events.");
  });

  it("keeps the selected style on the button element itself", () => {
    render(<CalendarApp windowId="test" />);
    const buttons = screen.getAllByTestId("calendar-day");
    const selected = buttons.find((b) => b.getAttribute("aria-pressed") === "true")!;
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

  it("gives each cell a distinct accessible name (its own date)", () => {
    render(<CalendarApp windowId="test" />);
    const buttons = screen.getAllByTestId("calendar-day");
    // Find two different cells (today and another current-month cell)
    const todayButton = buttons.find((b) => b.getAttribute("aria-pressed") === "true");
    const otherButton = buttons.find(
      (b) => b.getAttribute("aria-pressed") !== "true" && b.textContent?.trim() !== todayButton?.textContent?.trim(),
    );
    expect(todayButton).toBeDefined();
    expect(otherButton).toBeDefined();
    const todayLabel = todayButton!.getAttribute("aria-label");
    const otherLabel = otherButton!.getAttribute("aria-label");
    expect(todayLabel).not.toBe(otherLabel);
    // Both should contain a month name, not the same heading
    expect(todayLabel).toMatch(/\w+ \d+, \d{4}/);
    expect(otherLabel).toMatch(/\w+ \d+, \d{4}/);
  });
});