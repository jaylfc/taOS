import type { ReactNode } from "react";
import styles from "./BoardColumn.module.css";

export interface BoardColumnProps {
  status: "ready" | "claimed" | "closed" | "quarantined";
  count: number;
  showAllClosed?: boolean;
  onShowAllClosed?: () => void;
  onDropTask?: (taskId: string) => void;
  children: ReactNode;
}

const NAME = { ready: "Ready", claimed: "Claimed", closed: "Closed · 7d", quarantined: "Quarantined" } as const;

export function BoardColumn(p: BoardColumnProps) {
  const onDropTask = p.onDropTask;
  return (
    <section
      role="region"
      aria-label={NAME[p.status]}
      className={`${styles.col} ${styles[p.status]}`}
      onDragOver={onDropTask ? (e) => { e.preventDefault(); } : undefined}
      onDrop={onDropTask ? (e) => {
        e.preventDefault();
        const id = e.dataTransfer.getData("text/plain");
        if (id) onDropTask(id);
      } : undefined}
    >
      <header className={styles.head}>
        <span className={styles.name}>{NAME[p.status]}</span>
        <span className={styles.count}>{p.count}</span>
      </header>
      <div className={styles.body}>{p.children}</div>
      {p.status === "closed" && p.onShowAllClosed && (
        <button type="button" className={styles.showMore} onClick={p.onShowAllClosed}>
          {p.showAllClosed ? "Show recent only" : "Show all closed"}
        </button>
      )}
    </section>
  );
}
