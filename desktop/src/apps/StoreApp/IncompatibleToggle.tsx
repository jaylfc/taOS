// desktop/src/apps/StoreApp/IncompatibleToggle.tsx
import { useEffect, useState, type ReactNode } from "react";
import { ChevronDown, ChevronUp, AlertCircle } from "lucide-react";

interface Props {
  count: number;
  /** Number of compatible items above this toggle. When 0, the toggle
   *  promotes itself to a prominent empty-state CTA so users on
   *  low-spec hardware know they CAN show the catalog anyway. Filed
   *  as #355 — without this the user thought there was no filter at
   *  all because their compatible-cards area was empty.
   */
  compatibleCount?: number;
  /** Render-prop for the dimmed grid of incompatible cards. */
  children: ReactNode;
}

export function IncompatibleToggle({ count, compatibleCount = 1, children }: Props) {
  const [open, setOpen] = useState(compatibleCount === 0);
  // compatibleCount can transition to 0 after mount when the user changes
  // the device filter; without this the section stays collapsed and the
  // empty-state CTA doesn't surface the dimmed list. Only auto-opens on
  // the 0 transition — preserves user's choice in the other direction.
  useEffect(() => {
    if (compatibleCount === 0) setOpen(true);
  }, [compatibleCount]);

  if (count === 0) return null;

  // Empty-state variant: nothing in the section is compatible.
  // Promote the toggle to a yellow-card CTA + open-by-default so the
  // user actually sees the dimmed list rather than an empty pane with
  // a chevron at the bottom they didn't notice.
  if (compatibleCount === 0) {
    return (
      <div className="mt-2 flex flex-col gap-3">
        <div className="flex items-start gap-3 rounded-lg border border-amber-500/30 bg-amber-500/[0.06] px-4 py-3">
          <AlertCircle size={16} className="text-amber-400 mt-0.5 shrink-0" aria-hidden="true" />
          <div className="flex-1">
            <div className="text-sm font-medium text-amber-100">
              Nothing in this section matches the selected devices.
            </div>
            <div className="text-xs text-amber-200/70 mt-0.5">
              Showing the {count} item{count === 1 ? "" : "s"} that won't run on this hardware below — pick a different device or accept the warning to install anyway.
            </div>
          </div>
          <button
            type="button"
            onClick={() => setOpen((v) => !v)}
            className="shrink-0 inline-flex items-center gap-1 rounded-md border border-amber-500/40 bg-amber-500/15 px-3 py-1 text-xs font-medium text-amber-100 hover:bg-amber-500/25 transition-colors"
            aria-expanded={open}
          >
            {open ? <ChevronUp size={12} /> : <ChevronDown size={12} />}
            {open ? "Hide" : "Show"} all
          </button>
        </div>
        {open && <div className="opacity-60">{children}</div>}
      </div>
    );
  }

  // Default: subtle bottom toggle when there ARE compatible items above.
  return (
    <div className="mt-6 border-t border-shell-border pt-4">
      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        className="text-xs text-shell-text-tertiary hover:text-shell-text-primary inline-flex items-center gap-1"
        aria-expanded={open}
      >
        {open ? <ChevronUp size={12} /> : <ChevronDown size={12} />}
        {open ? "Hide" : "Show"} {count} model{count === 1 ? "" : "s"} that
        won't run on the selected devices
      </button>
      {open && <div className="mt-3 opacity-50">{children}</div>}
    </div>
  );
}
