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

export function CalendarApp({ windowId: _windowId }: { windowId: string }) {
  const today = new Date();
  const [viewYear, setViewYear] = useState(today.getFullYear());
  const [viewMonth, setViewMonth] = useState(today.getMonth());

  const daysInMonth = getDaysInMonth(viewYear, viewMonth);
  const startOffset = getFirstDayOfWeek(viewYear, viewMonth);

  const prevDays = getDaysInMonth(
    viewMonth === 0 ? viewYear - 1 : viewYear,
    viewMonth === 0 ? 11 : viewMonth - 1
  );

  const cells: { day: number; current: boolean; isToday: boolean }[] = [];

  // Leading days from previous month
  for (let i = startOffset - 1; i >= 0; i--) {
    cells.push({ day: prevDays - i, current: false, isToday: false });
  }

  // Current month days
  for (let d = 1; d <= daysInMonth; d++) {
    const isToday =
      d === today.getDate() &&
      viewMonth === today.getMonth() &&
      viewYear === today.getFullYear();
    cells.push({ day: d, current: true, isToday });
  }

  // Trailing days to fill last row
  const remaining = 7 - (cells.length % 7);
  if (remaining < 7) {
    for (let d = 1; d <= remaining; d++) {
      cells.push({ day: d, current: false, isToday: false });
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
  }

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
      <div className="grid grid-cols-7 text-center text-xs text-shell-text-tertiary font-medium">
        {DAYS.map((d) => (
          <div key={d} className="py-2">
            {d}
          </div>
        ))}
      </div>

      {/* Day grid */}
      <div className="grid grid-cols-7 flex-1 gap-px">
        {cells.map((cell, i) => (
          <div
            key={i}
            className={`flex items-start justify-center pt-2 rounded-lg text-sm transition-colors ${
              cell.current
                ? "text-shell-text hover:bg-shell-surface"
                : "text-shell-text-tertiary"
            }`}
          >
            <span
              className={`w-8 h-8 flex items-center justify-center rounded-full ${
                cell.isToday
                  ? "bg-accent text-white font-semibold"
                  : ""
              }`}
            >
              {cell.day}
            </span>
          </div>
        ))}
      </div>
    </div>
  );
}
