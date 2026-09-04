import type { DragEvent, ReactNode } from "react";
import styles from "./BoardLane.module.css";
import type { LaneHeader } from "./types";

type CellStatus = "ready" | "claimed" | "closed" | "quarantined";

export interface BoardLaneProps {
  header: LaneHeader;
  cells: { ready: ReactNode; claimed: ReactNode; closed: ReactNode; quarantined: ReactNode };
  onDropTask: (taskId: string, status: CellStatus, laneKey: string) => void;
}

export function BoardLane({ header, cells, onDropTask }: BoardLaneProps) {
  const onCellDrop = (status: CellStatus) => (e: DragEvent<HTMLDivElement>) => {
    e.preventDefault();
    const id = e.dataTransfer.getData("text/plain");
    if (id) onDropTask(id, status, header.key);
  };
  const dragOver = (e: DragEvent<HTMLDivElement>) => e.preventDefault();
  const initial = header.title.charAt(0).toUpperCase() || "?";
  return (
    <div className={styles.row}>
      <div className={styles.head}>
        <div className={styles.av} aria-hidden>{initial}</div>
        <div>
          <div className={styles.name}>{header.title}</div>
          {header.subtitle && <div className={styles.sub}>{header.subtitle}</div>}
        </div>
      </div>
      <div data-testid="lane-cell-ready" className={styles.cell} onDragOver={dragOver} onDrop={onCellDrop("ready")}>{cells.ready}</div>
      <div data-testid="lane-cell-claimed" className={styles.cell} onDragOver={dragOver} onDrop={onCellDrop("claimed")}>{cells.claimed}</div>
      <div data-testid="lane-cell-closed" className={styles.cell} onDragOver={dragOver} onDrop={onCellDrop("closed")}>{cells.closed}</div>
      <div data-testid="lane-cell-quarantined" className={styles.cell}>{cells.quarantined}</div>
    </div>
  );
}
