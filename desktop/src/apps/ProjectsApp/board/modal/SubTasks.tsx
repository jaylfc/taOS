import type { Task } from "../types";

export function SubTasks({ all, parentId }: { all: Task[]; parentId: string }) {
  const children = all.filter(t => t.parent_task_id === parentId);
  if (children.length === 0) return null;
  const done = children.filter(c => c.status === "closed").length;
  return (
    <section className="board-section">
      <h3>Sub-tasks · {done} / {children.length}</h3>
      <ul>
        {children.map(c => (
          <li key={c.id}>
            <input type="checkbox" checked={c.status === "closed"} readOnly aria-label={c.title} />
            <span>{c.title}</span>
          </li>
        ))}
      </ul>
    </section>
  );
}
