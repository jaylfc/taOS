import styles from "./ElementFilterBar.module.css";
import type { ProjectElement } from "../../../lib/projects";
import type { ElementFilter } from "./types";

export interface ElementFilterBarProps {
  elements: ProjectElement[];
  value: ElementFilter;
  onChange: (v: ElementFilter) => void;
}

export function ElementFilterBar({ elements, value, onChange }: ElementFilterBarProps) {
  if (elements.length === 0) return null;

  return (
    <div className={styles.row} role="group" aria-label="Filter by element">
      <button
        type="button"
        className={`${styles.chip} ${value === null ? styles.on : ""}`}
        aria-pressed={value === null}
        onClick={() => onChange(null)}
      >
        All
      </button>
      {elements.map(el => (
        <button
          key={el.id}
          type="button"
          className={`${styles.chip} ${value === el.id ? styles.on : ""}`}
          aria-pressed={value === el.id}
          onClick={() => onChange(el.id)}
        >
          {el.name}
        </button>
      ))}
      <button
        type="button"
        className={`${styles.chip} ${value === "none" ? styles.on : ""}`}
        aria-pressed={value === "none"}
        onClick={() => onChange("none")}
      >
        Project-level
      </button>
    </div>
  );
}
