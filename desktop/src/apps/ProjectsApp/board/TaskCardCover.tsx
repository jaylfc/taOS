import styles from "./TaskCardCover.module.css";

export type CoverKind = "gradient" | "code" | "terminal" | "screenshot" | "none";

export interface TaskCardCoverProps {
  kind: CoverKind;
  data?: {
    snippet?: string;
    language?: string;
    lines?: string[];
    badge?: string;
  };
}

export function TaskCardCover({ kind, data }: TaskCardCoverProps) {
  if (kind === "none") return null;
  if (kind === "gradient") {
    return (
      <div data-testid="cover-gradient" className={styles.gradient}>
        {data?.badge && <span className={styles.badge}>{data.badge}</span>}
      </div>
    );
  }
  if (kind === "code") {
    return (
      <pre data-testid="cover-code" className={styles.code}>
        {data?.badge && <span className={styles.badge}>{data.badge}</span>}
        <code>{data?.snippet ?? ""}</code>
      </pre>
    );
  }
  if (kind === "terminal") {
    return (
      <div data-testid="cover-terminal" className={styles.terminal}>
        {data?.badge && <span className={styles.badge}>{data.badge}</span>}
        {(data?.lines ?? []).map((l, i) => <div key={i}>{l}</div>)}
        <span className={styles.cursor} aria-hidden />
      </div>
    );
  }
  return (
    <div data-testid="cover-screenshot" className={styles.screenshot}>
      {data?.badge && <span className={styles.badge}>{data.badge}</span>}
    </div>
  );
}

// Heuristic: derive cover kind from labels + priority + (future) attachments
export function inferCoverKind(task: { labels: string[]; priority: number }): CoverKind {
  const lbl = task.labels.find(l => l.startsWith("cover:"));
  if (lbl) {
    const k = lbl.slice("cover:".length) as CoverKind;
    if (["gradient", "code", "terminal", "screenshot", "none"].includes(k)) return k;
  }
  return "none";
}
