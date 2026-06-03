/**
 * Window-shaped loading placeholder shown while a lazy app chunk loads.
 *
 * Fills the window content region (the titlebar is painted by Window.tsx) with
 * a faux toolbar bar plus a few content blocks. Pure CSS shimmer via Tailwind's
 * `animate-pulse`, suppressed under `prefers-reduced-motion`.
 */
export function WindowSkeleton() {
  return (
    <div
      role="status"
      aria-label="Loading app"
      data-testid="window-skeleton"
      className="flex flex-col h-full w-full p-4 gap-4 animate-pulse motion-reduce:animate-none"
    >
      {/* Faux toolbar */}
      <div className="flex items-center gap-2 shrink-0">
        <div className="h-7 w-7 rounded-md bg-shell-surface-active" />
        <div className="h-7 w-7 rounded-md bg-shell-surface-active" />
        <div className="flex-1" />
        <div className="h-7 w-28 rounded-md bg-shell-surface" />
      </div>
      <div className="h-px w-full bg-shell-border" />

      {/* Faux content blocks */}
      <div className="flex flex-1 gap-4 min-h-0">
        {/* Sidebar */}
        <div className="hidden sm:flex flex-col gap-2 w-40 shrink-0">
          <div className="h-4 w-3/4 rounded bg-shell-surface" />
          <div className="h-4 w-1/2 rounded bg-shell-surface" />
          <div className="h-4 w-2/3 rounded bg-shell-surface" />
          <div className="h-4 w-1/2 rounded bg-shell-surface" />
        </div>
        {/* Main column */}
        <div className="flex flex-col gap-3 flex-1 min-w-0">
          <div className="h-5 w-1/3 rounded bg-shell-surface-active" />
          <div className="h-4 w-full rounded bg-shell-surface" />
          <div className="h-4 w-5/6 rounded bg-shell-surface" />
          <div className="h-4 w-4/6 rounded bg-shell-surface" />
          <div className="h-24 w-full rounded-lg bg-shell-surface mt-2" />
        </div>
      </div>
      <span className="sr-only">Loading…</span>
    </div>
  );
}
