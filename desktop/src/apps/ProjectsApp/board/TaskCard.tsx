import type { DragEvent, KeyboardEvent } from "react";
import styles from "./TaskCard.module.css";
import { TaskCardCover, inferCoverKind } from "./TaskCardCover";
import type { Task } from "./types";

export interface TaskCardProps {
  task: Task;
  onOpen: (id: string) => void;
  onMove?: (id: string) => void;
  justClaimed?: boolean;
  draggable?: boolean;
  elementName?: string | null;
  onDragStart?: (e: DragEvent<HTMLButtonElement>, t: Task) => void;
}

export function TaskCard({ task, onOpen, onMove, justClaimed, draggable, elementName, onDragStart }: TaskCardProps) {
  const cover = inferCoverKind(task);
  const pri = task.priority === 0 ? "p0" : task.priority === 1 ? "p1" : "p2";
  const onKeyDown = (e: KeyboardEvent<HTMLButtonElement>) => {
    if (e.key === "Enter") {
      e.preventDefault();
      onOpen(task.id);
    } else if ((e.key === "m" || e.key === "M") && onMove) {
      e.preventDefault();
      onMove(task.id);
    }
  };
  return (
    <button
      data-testid="task-card"
      type="button"
      className={`${styles.card} ${styles[pri as keyof typeof styles] ?? ""} ${justClaimed ? styles.justClaimed : ""}`}
      onClick={() => onOpen(task.id)}
      onKeyDown={onKeyDown}
      draggable={draggable}
      onDragStart={(e) => onDragStart?.(e, task)}
      aria-label={task.title}
    >
      <span className={styles.priEdge} aria-hidden />
      <TaskCardCover kind={cover} />
      <div className={styles.body}>
        <div className={styles.idRow}>
          <span>{task.id}</span>
          {task.parent_task_id && <span className={styles.parent}>↳</span>}
        </div>
        <div className={styles.title}>{task.title}</div>
        {elementName && <span className={styles.elBadge}>{elementName}</span>}
        {task.labels.length > 0 && (
          <div className={styles.labels}>
            {task.labels.filter(l => !l.startsWith("cover:")).map(l => (
              <span key={l} className={`${styles.lbl} ${(styles as Record<string, string | undefined>)[`lbl_${l}`] ?? ""}`}>{l}</span>
            ))}
          </div>
        )}
        <div className={styles.foot}>
          {task.claimed_by && <span>{task.claimed_by}</span>}
          <span className={styles.grow} />
          <span>{relativeTime(task.updated_at)}</span>
        </div>
      </div>
    </button>
  );
}

function relativeTime(iso: string): string {
  const d = new Date(iso).getTime();
  const diff = Date.now() - d;
  const min = Math.round(diff / 60_000);
  if (min < 1) return "now";
  if (min < 60) return `${min}m`;
  const hrs = Math.round(min / 60);
  if (hrs < 24) return `${hrs}h`;
  return `${Math.round(hrs / 24)}d`;
}
