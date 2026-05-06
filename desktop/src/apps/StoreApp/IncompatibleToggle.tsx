// desktop/src/apps/StoreApp/IncompatibleToggle.tsx
import { useState, type ReactNode } from "react";
import { ChevronDown, ChevronUp } from "lucide-react";

interface Props {
  count: number;
  /** Render-prop for the dimmed grid of incompatible cards. */
  children: ReactNode;
}

export function IncompatibleToggle({ count, children }: Props) {
  const [open, setOpen] = useState(false);

  if (count === 0) return null;

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
