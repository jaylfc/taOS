import { useState } from "react";
import { ChevronLeft, ChevronRight } from "lucide-react";

const DAYS = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"];
const MONTHS = [
  "January",
  "February",
  "March",
  "April",
  "May",
  "June",
  "July",
  "August",
  "September",
  "October",
  "November",
  "December",
];

function getDaysInMonth(year: number, month: number) {
  return new Date(year, month + 1, 0).getDate();
}

/** 0 = Mon … 6 = Sun */
function getFirstDayOfWeek(year: number, month: number) {
  const day = new Date(year, month, 1).getDay();
  return day === 0 ? 6 : day - 1;
}

/** YYYY-MM-DD — ISO 8601 local date (no timezone). Zero-padded so that
 *  parseDateKey can reconstruct the exact local day without UTC drift. */
function formatDateKey(d: Date): string {
  const y = d.getFullYear();
  const m = String(d.getMonth() + 1).padStart(2, "0");
  const day = String(d.getDate()).padStart(2, "0");
  return `${y}-${m}-${day}`;
}

/** Parse a YYYY-MM-DD key as a LOCAL date (not UTC). */
function parseDateKey(key: string): Date {
  const parts = key.split("-");
  const y = Number(parts[0]);
  const m = Number(parts[1]);
  const d = Number(parts[2]);
  return new Date(y, m - 1, d);
}

export function CalendarApp({ windowId: _windowId }: { windowId: string }) {
  const today = new Date();
  const [viewYear, setViewYear] = useState(today.getFullYear());
  const [viewMonth, setViewMonth] = useState(today.getMonth());
  const [selectedKey, setSelectedKey] = useState(formatDateKey(today));

  const daysInMonth = getDaysInMonth(viewYear, viewMonth);
  const startOffset = getFirstDayOfWeek(viewYear, viewMonth);

  const prevDays = getDaysInMonth(
    viewMonth === 0 ? viewYear - 1 : viewYear,
    viewMonth === 0 ? 11 : viewMonth - 1,
  );

  const cells: { day: number; current: boolean; isToday: boolean; key: string }[] = [];

  // Leading days from previous month
  for (let i = startOffset - 1; i >= 0; i--) {
    const d = new Date(
      viewMonth === 0 ? viewYear - 1 : viewYear,
      viewMonth === 0 ? 11 : viewMonth - 1,
      prevDays - i,
    );
    cells.push({ day: prevDays - i, current: false, isToday: false, key: formatDateKey(d) });
  }

  // Current month days
  for (let d = 1; d <= daysInMonth; d++) {
    const date = new Date(viewYear, viewMonth, d);
    const isToday =
      d === today.getDate() &&
      viewMonth === today.getMonth() &&
      viewYear === today.getFullYear();
    cells.push({ day: d, current: true, isToday, key: formatDateKey(date) });
  }

  // Trailing days to fill last row
  const remaining = 7 - (cells.length % 7);
  if (remaining < 7) {
    for (let d = 1; d <= remaining; d++) {
      const date = new Date(viewYear, viewMonth, daysInMonth + d);
      cells.push({ day: d, current: false, isToday: false, key: formatDateKey(date) });
    }
  }

  function prevMonth() {
    if (viewMonth === 0) {
      setViewMonth(11);
      setViewYear((y) => y - 1);
    } else {
      setViewMonth((m) => m - 1);
    }
  }

  function nextMonth() {
    if (viewMonth === 11) {
      setViewMonth(0);
      setViewYear((y) => y + 1);
    } else {
      setViewMonth((m) => m + 1);
    }
  }

  function goToday() {
    setViewYear(today.getFullYear());
    setViewMonth(today.getMonth());
    setSelectedKey(formatDateKey(today));
  }

  function onDaySelect(key: string) {
    setSelectedKey(key);
  }

  const selectedDate = parseDateKey(selectedKey);
  const heading = `${MONTHS[selectedDate.getMonth()]} ${selectedDate.getDate()}, ${selectedDate.getFullYear()}`;

  return (
    <div className="flex flex-col h-full bg-shell-bg-deep text-shell-text select-none p-4 gap-4">
      {/* Header */}
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-3">
          <button
            onClick={prevMonth}
            className="p-1.5 rounded-lg hover:bg-shell-surface transition-colors"
            aria-label="Previous month"
          >
            <ChevronLeft size={18} />
          </button>
          <h2 className="text-lg font-semibold w-48 text-center">
            {MONTHS[viewMonth]} {viewYear}
          </h2>
          <button
            onClick={nextMonth}
            className="p-1.5 rounded-lg hover:bg-shell-surface transition-colors"
            aria-label="Next month"
          >
            <ChevronRight size={18} />
          </button>
        </div>
        <button
          onClick={goToday}
          className="px-3 py-1.5 rounded-lg bg-shell-surface text-sm text-shell-text-secondary hover:bg-shell-surface-hover transition-colors"
        >
          Today
        </button>
      </div>

      {/* Day-of-week header */}
      <div role="row" className="grid grid-cols-7 text-center text-xs text-shell-text-tertiary font-medium">
        {DAYS.map((d) => (
          <div key={d} role="columnheader" className="py-2">
            {d}
          </div>
        ))}
      </div>

      {/* Day grid */}
      <div role="grid" className="grid grid-cols-7 flex-1 gap-px">
        {cells.map((cell) => {
          const selected = cell.key === selectedKey;
          const cellDate = parseDateKey(cell.key);
          const cellHeading = `${MONTHS[cellDate.getMonth()]} ${cellDate.getDate()}, ${cellDate.getFullYear()}`;
          return (
            <div
              key={cell.key}
              role="gridcell"
              className={`flex items-start justify-center pt-2 rounded-lg text-sm transition-colors ${
                cell.current
                  ? "text-shell-text hover:bg-shell-surface"
                  : "text-shell-text-tertiary"
              }`}
            >
              <button
                type="button"
                onClick={() => onDaySelect(cell.key)}
                aria-pressed={selected}
                data-testid="calendar-day"
                data-date-key={cell.key}
                aria-label={`${cellHeading}${cell.current ? "" : " (other month)"}${cell.isToday ? ", today" : ""}`}
                className={`relative h-8 w-8 flex items-center justify-center rounded-full text-sm font-medium transition-colors ${
                  selected
                    ? "bg-accent text-white"
                    : cell.isToday
                      ? "bg-accent/20 text-accent"
                      : cell.current
                        ? "text-shell-text hover:bg-white/[0.06]"
                        : "text-shell-text-tertiary"
                }`}
              >
                {cell.day}
              </button>
            </div>
          );
        })}
      </div>

      {/* Day panel / agenda — shows for the selected date, even with no events */}
      <div
        id="calendar-day-panel"
        role="tabpanel"
        aria-label={`Agenda for ${heading}`}
        className="shrink-0 border-t border-white/5 pt-3"
      >
        <h3 className="text-sm font-semibold mb-2">{heading}</h3>
        <p className="text-xs text-shell-text-tertiary">No events.</p>
      </div>
    </div>
  );
}