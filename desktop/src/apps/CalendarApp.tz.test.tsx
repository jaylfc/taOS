// @vitest-environment jsdom
import { render, screen, fireEvent } from "@testing-library/react";
import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { CalendarApp } from "./CalendarApp";

/**
 * Timezone-specific tests for CalendarApp date parsing.
 * These tests verify that YYYY-MM-DD date keys are parsed as LOCAL dates,
 * not UTC, preventing off-by-one-day bugs in negative-offset timezones.
 *
 * Run with:
 *   TZ=America/Los_Angeles npx vitest run src/apps/CalendarApp.tz.test.tsx
 *   TZ=Pacific/Kiritimati npx vitest run src/apps/CalendarApp.tz.test.tsx
 *
 * Before the fix (UTC parsing), the "round-trips other-month day keys" test
 * would fail in America/Los_Angeles (UTC-7/-8) because `new Date("2025-06-15")`
 * parses as midnight UTC, which is the previous evening in LA.
 */

const FIXED_DATE = new Date(2025, 5, 15); // June 15, 2025

function mockToday() {
  vi.useFakeTimers();
  vi.setSystemTime(FIXED_DATE);
}

describe("CalendarApp timezone handling", () => {
  beforeEach(() => {
    mockToday();
  });

  afterEach(() => {
    vi.useRealTimers();
  });

  it("parses selectedKey as local date in negative-offset timezone (America/Los_Angeles)", () => {
    // This test runs with TZ=America/Los_Angeles set in the environment.
    // The key "2025-06-15" must resolve to June 15 locally, not June 14.
    render(<CalendarApp windowId="tz-test-1" />);

    // Click the today cell (June 15)
    const todayButton = screen.getByRole("button", { name: /june 15, 2025/i });
    fireEvent.click(todayButton);

    // The day panel must show "June 15, 2025", not "June 14, 2025"
    const panel = screen.getByRole("tabpanel", { name: /agenda for/i });
    expect(panel.textContent).toContain("June 15, 2025");
    expect(panel.textContent).not.toContain("June 14, 2025");
  });

  it("parses selectedKey as local date in positive-offset timezone (Pacific/Kiritimati)", () => {
    // This test runs with TZ=Pacific/Kiritimati set in the environment (UTC+14).
    // The key "2025-06-15" must resolve to June 15 locally.
    render(<CalendarApp windowId="tz-test-2" />);

    const todayButton = screen.getByRole("button", { name: /june 15, 2025/i });
    fireEvent.click(todayButton);

    const panel = screen.getByRole("tabpanel", { name: /agenda for/i });
    expect(panel.textContent).toContain("June 15, 2025");
    expect(panel.textContent).not.toContain("June 16, 2025");
  });

  it("round-trips other-month day keys correctly in negative-offset timezone", () => {
    // This is the critical test that failed before the fix.
    // Trailing cells from the next month (July 2025) have single-digit day numbers.
    // The old non-padded key format ("2025-6-1") would parse incorrectly in some engines.
    render(<CalendarApp windowId="tz-test-3" />);

    const cells = screen.getAllByTestId("calendar-day");

    // Find a trailing cell from the next month (July) with a single-digit day
    const other = cells.find((b) => {
      const label = b.getAttribute("aria-label") ?? "";
      return (
        label.includes("(other month)") && /^\d$/.test(b.textContent?.trim() ?? "")
      );
    }) as HTMLElement | undefined;

    expect(other).toBeDefined();
    const day = other!.textContent!.trim();

    // Compute the expected heading for the next month
    const today = new Date();
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

  it("each day cell has its own distinct accessible name (aria-label)", () => {
    render(<CalendarApp windowId="tz-test-4" />);
    const buttons = screen.getAllByTestId("calendar-day");

    // Find two different current-month cells with different day numbers
    const cell1 = buttons.find(
      (b) => b.getAttribute("aria-pressed") === "true"
    );
    const cell2 = buttons.find(
      (b) =>
        b.getAttribute("aria-pressed") !== "true" &&
        b.textContent?.trim() !== cell1?.textContent?.trim() &&
        !b.getAttribute("aria-label")?.includes("(other month)")
    );

    expect(cell1).toBeDefined();
    expect(cell2).toBeDefined();

    const label1 = cell1!.getAttribute("aria-label");
    const label2 = cell2!.getAttribute("aria-label");

    // Each cell must have a different accessible name reflecting its own date
    expect(label1).not.toBe(label2);
    expect(label1).toMatch(/\w+ \d+, \d{4}/);
    expect(label2).toMatch(/\w+ \d+, \d{4}/);
  });
});