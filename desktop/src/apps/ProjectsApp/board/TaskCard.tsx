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
  onDragStart?: (e: DragEvent<HTMLDivElement>, t: Task) => void;
  isLead?: boolean;
  onUnquarantine?: (id: string) => void;
}

export function TaskCard({ task, onOpen, onMove, justClaimed, draggable, elementName, onDragStart, isLead, onUnquarantine }: TaskCardProps) {
  const cover = inferCoverKind(task);
  const pri = task.priority === 0 ? "p0" : task.priority === 1 ? "p1" : "p2";
  const onKeyDown = (e: KeyboardEvent<HTMLDivElement>) => {
    if (e.key === "Enter" || e.key === " ") {
      e.preventDefault();
      onOpen(task.id);
    } else if ((e.key === "m" || e.key === "M") && onMove) {
      e.preventDefault();
      onMove(task.id);
    }
  };
  const quarantined = task.status === "quarantined";
  return (
    <div
      data-testid="task-card"
      role="button"
      tabIndex={0}
      className={`${styles.card} ${styles[pri as keyof typeof styles] ?? ""} ${justClaimed ? styles.justClaimed : ""} ${quarantined ? styles.quarantined : ""}`}
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
        {quarantined && (
          <div className={styles.quarantineBadge} role="status" aria-label={`Quarantined: ${task.strike_count ?? 0} strikes`}>
            <span className={styles.quarantineIcon} aria-hidden>⚠</span>
            <span className={styles.quarantineText}>
              {task.strike_count ?? 0} strikes
              {task.latest_strike?.step && <span className={styles.quarantineReason}> — {task.latest_strike.step}</span>}
            </span>
            {isLead && onUnquarantine && (
              <button
                type="button"
                className={styles.unquarantineBtn}
                onClick={(e) => {
                  e.stopPropagation();
                  onUnquarantine(task.id);
                }}
                onKeyDown={(e) => {
                  if (e.key === "Enter" || e.key === " ") {
                    e.stopPropagation();
                    e.preventDefault();
                    onUnquarantine(task.id);
                  }
                }}
                aria-label={`Unquarantine task ${task.id}`}
              >
                Unquarantine
              </button>
            )}
          </div>
        )}
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
    </div>
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
