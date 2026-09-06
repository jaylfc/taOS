import { useState, useEffect, useCallback } from "react";
import { CalendarDays, List, ChevronLeft, ChevronRight, Search, Clock, Tag } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Card, CardContent } from "@/components/ui/card";
import { fetchCatalogDate, fetchCatalogSearch } from "@/lib/memory";
import { SessionDetail } from "./SessionDetail";
import type { Session } from "./SessionDetail";

/* ------------------------------------------------------------------ */
/*  Helpers                                                            */
/* ------------------------------------------------------------------ */

function isoDate(d: Date): string {
  return d.toISOString().slice(0, 10);
}

function daysInMonth(year: number, month: number): number {
  return new Date(year, month + 1, 0).getDate();
}

function firstDayOfMonth(year: number, month: number): number {
  return new Date(year, month, 1).getDay();
}

const MONTH_NAMES = [
  'January', 'February', 'March', 'April', 'May', 'June',
  'July', 'August', 'September', 'October', 'November', 'December',
];

const DAY_LABELS = ['Su', 'Mo', 'Tu', 'We', 'Th', 'Fr', 'Sa'];

/* ------------------------------------------------------------------ */
/*  CalendarView                                                       */
/* ------------------------------------------------------------------ */

interface CalendarViewProps {
  onSelectDate: (date: string) => void;
  selectedDate: string | null;
}

function CalendarView({ onSelectDate, selectedDate }: CalendarViewProps) {
  const today = new Date();
  const [year, setYear] = useState(today.getFullYear());
  const [month, setMonth] = useState(today.getMonth());

  const days = daysInMonth(year, month);
  const firstDay = firstDayOfMonth(year, month);
  const cells: (number | null)[] = [];
  for (let i = 0; i < firstDay; i++) cells.push(null);
  for (let d = 1; d <= days; d++) cells.push(d);

  const prevMonth = () => {
    if (month === 0) { setYear((y) => y - 1); setMonth(11); }
    else setMonth((m) => m - 1);
  };
  const nextMonth = () => {
    if (month === 11) { setYear((y) => y + 1); setMonth(0); }
    else setMonth((m) => m + 1);
  };

  return (
    <div className="flex flex-col gap-3 select-none" aria-label="Calendar date picker">
      {/* Header */}
      <div className="flex items-center justify-between">
        <Button
          variant="ghost"
          size="icon"
          onClick={prevMonth}
          aria-label="Previous month"
          className="h-7 w-7"
        >
          <ChevronLeft size={14} aria-hidden="true" />
        </Button>
        <span className="text-sm font-medium text-shell-text" aria-live="polite">
          {MONTH_NAMES[month]} {year}
        </span>
        <Button
          variant="ghost"
          size="icon"
          onClick={nextMonth}
          aria-label="Next month"
          className="h-7 w-7"
        >
          <ChevronRight size={14} aria-hidden="true" />
        </Button>
      </div>

      {/* Day labels */}
      <div className="grid grid-cols-7 gap-0.5" role="row" aria-label="Days of week">
        {DAY_LABELS.map((d) => (
          <div key={d} className="text-[10px] text-center text-shell-text-tertiary py-0.5 font-medium" aria-hidden="true">
            {d}
          </div>
        ))}
      </div>

      {/* Day cells */}
      <div className="grid grid-cols-7 gap-0.5" role="grid" aria-label="Calendar days">
        {cells.map((day, i) => {
          if (!day) return <div key={`empty-${i}`} role="gridcell" aria-hidden="true" />;

          const dateStr = `${year}-${String(month + 1).padStart(2, '0')}-${String(day).padStart(2, '0')}`;
          const isToday = dateStr === isoDate(today);
          const isSelected = dateStr === selectedDate;

          return (
            <button
              key={dateStr}
              type="button"
              role="gridcell"
              aria-label={`${day} ${MONTH_NAMES[month]} ${year}${isToday ? ', today' : ''}${isSelected ? ', selected' : ''}`}
              aria-selected={isSelected}
              onClick={() => onSelectDate(dateStr)}
              className={`
                h-8 w-full rounded-md text-xs font-medium transition-colors focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-accent
                ${isSelected
                  ? 'bg-accent text-white'
                  : isToday
                  ? 'bg-accent/20 text-accent border border-accent/30'
                  : 'text-shell-text hover:bg-white/[0.06]'}
              `}
            >
              {day}
            </button>
          );
        })}
      </div>
    </div>
  );
}

/* ------------------------------------------------------------------ */
/*  SessionCard                                                        */
/* ------------------------------------------------------------------ */

interface SessionCardProps {
  session: Session;
  onClick: () => void;
}

function SessionCard({ session, onClick }: SessionCardProps) {
  return (
    <Card
      className="cursor-pointer hover:bg-white/[0.04] transition-colors"
      onClick={onClick}
      role="button"
      tabIndex={0}
      onKeyDown={(e) => { if (e.key === 'Enter' || e.key === ' ') onClick(); }}
      aria-label={`Session: ${session.topic}`}
    >
      <CardContent className="p-3.5 flex flex-col gap-1.5">
        <div className="flex items-start justify-between gap-2">
          <h3 className="text-sm font-medium text-shell-text leading-snug flex-1 min-w-0 line-clamp-2">
            {session.topic}
          </h3>
          {session.category && (
            <span className="shrink-0 px-1.5 py-0.5 rounded-full bg-accent/15 text-accent text-[10px] border border-accent/20 flex items-center gap-1">
              <Tag size={9} aria-hidden="true" />
              {session.category}
            </span>
          )}
        </div>
        {session.description && (
          <p className="text-xs text-shell-text-secondary leading-relaxed line-clamp-2">
            {session.description}
          </p>
        )}
        <div className="flex items-center gap-2 text-[10px] text-shell-text-tertiary">
          <span>{session.date}</span>
          {session.start_time && (
            <span className="flex items-center gap-0.5">
              <Clock size={10} aria-hidden="true" />
              {session.start_time}
              {session.end_time ? ` – ${session.end_time}` : ''}
            </span>
          )}
          {Array.isArray(session.sub_sessions) && session.sub_sessions.length > 0 && (
            <span className="ml-auto">{session.sub_sessions.length} sub-sessions</span>
          )}
        </div>
      </CardContent>
    </Card>
  );
}

/* ------------------------------------------------------------------ */
/*  SessionBrowser                                                     */
/* ------------------------------------------------------------------ */

type ViewMode = 'calendar' | 'feed';

export function SessionBrowser() {
  const [viewMode, setViewMode] = useState<ViewMode>('calendar');
  const [selectedDate, setSelectedDate] = useState<string | null>(null);
  const [sessions, setSessions] = useState<Session[]>([]);
  const [loadingSessions, setLoadingSessions] = useState(false);
  const [selectedSession, setSelectedSession] = useState<Session | null>(null);
  const [search, setSearch] = useState('');
  const [searchResults, setSearchResults] = useState<Session[] | null>(null);
  const [searching, setSearching] = useState(false);

  const loadDate = useCallback(async (date: string) => {
    setLoadingSessions(true);
    setSessions([]);
    setSearchResults(null);
    const data = await fetchCatalogDate(date);
    setSessions(Array.isArray(data) ? data : []);
    setLoadingSessions(false);
  }, []);

  const handleSelectDate = (date: string) => {
    setSelectedDate(date);
    setSelectedSession(null);
    loadDate(date);
  };

  // Load today on mount for feed view
  useEffect(() => {
    if (viewMode === 'feed' && !selectedDate) {
      const today = isoDate(new Date());
      setSelectedDate(today);
      loadDate(today);
    }
  }, [viewMode, selectedDate, loadDate]);

  const handleSearch = async () => {
    if (!search.trim()) { setSearchResults(null); return; }
    setSearching(true);
    const results = await fetchCatalogSearch(search.trim());
    setSearchResults(Array.isArray(results) ? results : []);
    setSearching(false);
  };

  const displaySessions = searchResults ?? sessions;

  if (selectedSession) {
    return (
      <SessionDetail
        session={selectedSession}
        onBack={() => setSelectedSession(null)}
      />
    );
  }

  return (
    <div className="flex flex-col gap-4 h-full overflow-hidden">
      {/* Toolbar */}
      <div className="flex items-center gap-2 px-4 pt-4 flex-wrap">
        {/* View toggle */}
        <div className="flex items-center rounded-lg bg-white/[0.04] p-0.5 gap-0.5" role="group" aria-label="View mode">
          <Button
            variant={viewMode === 'calendar' ? 'secondary' : 'ghost'}
            size="sm"
            onClick={() => setViewMode('calendar')}
            aria-pressed={viewMode === 'calendar'}
            className="h-7 px-2.5 gap-1.5 text-xs"
          >
            <CalendarDays size={13} aria-hidden="true" />
            Calendar
          </Button>
          <Button
            variant={viewMode === 'feed' ? 'secondary' : 'ghost'}
            size="sm"
            onClick={() => setViewMode('feed')}
            aria-pressed={viewMode === 'feed'}
            className="h-7 px-2.5 gap-1.5 text-xs"
          >
            <List size={13} aria-hidden="true" />
            Feed
          </Button>
        </div>

        {/* Search */}
        <div className="flex items-center gap-1.5 flex-1 min-w-0">
          <div className="relative flex-1 min-w-0">
            <Search size={13} className="absolute left-2.5 top-1/2 -translate-y-1/2 text-shell-text-tertiary pointer-events-none z-10" aria-hidden="true" />
            <Input
              type="search"
              value={search}
              onChange={(e) => {
                setSearch(e.target.value);
                if (!e.target.value.trim()) setSearchResults(null);
              }}
              onKeyDown={(e) => { if (e.key === 'Enter') handleSearch(); }}
              placeholder="Search sessions..."
              aria-label="Search sessions"
              className="pl-8 h-8 text-xs"
            />
          </div>
          <Button
            variant="outline"
            size="sm"
            onClick={handleSearch}
            disabled={searching}
            aria-label="Search"
            className="h-8 px-2.5 text-xs shrink-0"
          >
            {searching ? 'Searching…' : 'Search'}
          </Button>
          {searchResults !== null && (
            <Button
              variant="ghost"
              size="sm"
              onClick={() => { setSearchResults(null); setSearch(''); }}
              aria-label="Clear search"
              className="h-8 px-2 text-xs shrink-0"
            >
              Clear
            </Button>
          )}
        </div>
      </div>

      <div className="flex flex-1 min-h-0 overflow-hidden gap-4 px-4 pb-4">
        {/* Calendar panel */}
        {viewMode === 'calendar' && (
          <div className="w-56 shrink-0 flex flex-col gap-3">
            <CalendarView onSelectDate={handleSelectDate} selectedDate={selectedDate} />
            {selectedDate && (
              <p className="text-xs text-shell-text-tertiary text-center">
                {selectedDate}
              </p>
            )}
          </div>
        )}

        {/* Sessions list */}
        <div className="flex-1 overflow-auto flex flex-col gap-2.5" aria-label="Sessions list" aria-live="polite">
          {searchResults !== null && (
            <p className="text-xs text-shell-text-tertiary">
              {searchResults.length} result{searchResults.length !== 1 ? 's' : ''} for "{search}"
            </p>
          )}

          {loadingSessions || searching ? (
            <div className="flex items-center justify-center py-12 text-shell-text-tertiary text-sm">
              Loading sessions…
            </div>
          ) : !selectedDate && viewMode === 'calendar' && searchResults === null ? (
            <div className="flex items-center justify-center py-12 text-shell-text-tertiary text-sm">
              Select a date to browse sessions
            </div>
          ) : displaySessions.length === 0 ? (
            <div className="flex items-center justify-center py-12 text-shell-text-tertiary text-sm">
              No sessions found
            </div>
          ) : (
            displaySessions.map((s) => (
              <SessionCard
                key={s.id}
                session={s}
                onClick={() => setSelectedSession(s)}
              />
            ))
          )}
        </div>
      </div>
    </div>
  );
}
