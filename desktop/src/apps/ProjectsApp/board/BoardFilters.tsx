import { useState } from "react";
import styles from "./BoardToolbar.module.css";
import type { Filters } from "./types";

export interface BoardFiltersProps {
  value: Filters;
  onChange: (f: Filters) => void;
}

const PRIORITY_OPTIONS = [0, 1, 2, 3];

export function BoardFilters({ value, onChange }: BoardFiltersProps) {
  const [open, setOpen] = useState(false);
  const togglePriority = (p: number) => {
    const next = value.priorities.includes(p)
      ? value.priorities.filter(x => x !== p)
      : [...value.priorities, p];
    onChange({ ...value, priorities: next });
  };
  return (
    <div className={styles.filterWrap}>
      <button type="button" className={styles.pill} onClick={() => setOpen(o => !o)}>+ Filter</button>
      {open && (
        <div role="dialog" aria-label="Filters" className={styles.popover}>
          <label className={styles.popRow}>
            <input
              type="checkbox"
              checked={value.hideClosed}
              onChange={(e) => onChange({ ...value, hideClosed: e.target.checked })}
            />
            Hide closed
          </label>
          <label className={styles.popRow}>
            <input
              type="checkbox"
              checked={value.hasAttachments}
              onChange={(e) => onChange({ ...value, hasAttachments: e.target.checked })}
            />
            Has attachments
          </label>
          <div className={styles.chipRow}>
            {PRIORITY_OPTIONS.map(p => (
              <button
                key={p}
                type="button"
                aria-pressed={value.priorities.includes(p)}
                className={`${styles.chip} ${value.priorities.includes(p) ? styles.chipOn : ""}`}
                onClick={() => togglePriority(p)}
              >
                P{p}
              </button>
            ))}
          </div>
        </div>
      )}
    </div>
  );
}
