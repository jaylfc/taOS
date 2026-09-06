import { TaskCardCover, inferCoverKind } from "../TaskCardCover";
import type { Task } from "../types";

export function Hero({ task }: { task: Task }) {
  const kind = inferCoverKind(task);
  if (kind === "none") return null;
  return (
    <div style={{ height: 160, position: "relative", overflow: "hidden" }}>
      <TaskCardCover kind={kind} />
    </div>
  );
}
