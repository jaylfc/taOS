import type { ProjectElement } from "../../../lib/projects";
import { elementType } from "./types";
import styles from "./Elements.module.css";

function timeAgo(ts: number): string {
  if (!ts) return "recently";
  const diff = Date.now() / 1000 - ts;
  if (diff < 60) return "just now";
  if (diff < 3600) return `${Math.floor(diff / 60)}m ago`;
  if (diff < 86400) return `${Math.floor(diff / 3600)}h ago`;
  return `${Math.floor(diff / 86400)}d ago`;
}

export interface ElementCardProps {
  element: ProjectElement;
  assigneeName?: string | null;
  onOpen: (id: string) => void;
}

export function ElementCard({ element, assigneeName, onOpen }: ElementCardProps) {
  const def = elementType(element.type);
  return (
    <button
      type="button"
      className={styles.card}
      onClick={() => onOpen(element.id)}
      aria-label={`Open element ${element.name}`}
    >
      <span className={styles.cardIcon} aria-hidden>{def.icon}</span>
      <span className={styles.cardBody}>
        <span className={styles.cardName}>{element.name}</span>
        <span className={styles.cardType}>{def.label}</span>
        {element.assignee_id && assigneeName ? (
          <span className={styles.cardAssignee}>Owner: {assigneeName}</span>
        ) : null}
        <span className={styles.cardMeta}>
          {element.open_tasks ?? 0} open · {element.total_tasks ?? 0} total
        </span>
        <span className={styles.cardActivity}>Updated {timeAgo(element.updated_at)}</span>
      </span>
    </button>
  );
}
