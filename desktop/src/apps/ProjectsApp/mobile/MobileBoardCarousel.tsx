import { useEffect, useRef, useState } from "react";

export interface BoardColumn {
  id: string;
  label: string;
}

export interface BoardTask {
  id: string;
  title: string;
  status: string;
  assignee?: string | null;
  parent_id?: string | null;
  labels?: string[];
  priority?: number | null;
}

export type GroupBy = "assignee" | "parent" | "label" | "priority" | null;

interface Props {
  columns: ReadonlyArray<BoardColumn>;
  tasksByColumn: Record<string, BoardTask[]>;
  groupBy: GroupBy;
  onOpenTask: (id: string) => void;
}

/**
 * Mobile board shell — sticky pill strip + horizontally paged columns.
 * When `groupBy` is set, each column renders collapsible swimlane sections.
 */
export function MobileBoardCarousel({ columns, tasksByColumn, groupBy, onOpenTask }: Props) {
  const [activeIdx, setActiveIdx] = useState(0);
  const scrollerRef = useRef<HTMLDivElement>(null);
  const paneRefs = useRef<Array<HTMLDivElement | null>>([]);

  useEffect(() => {
    const scroller = scrollerRef.current;
    if (!scroller) return;
    const onScroll = () => {
      const w = scroller.clientWidth;
      if (w === 0) return;
      const idx = Math.round(scroller.scrollLeft / w);
      setActiveIdx(idx);
    };
    scroller.addEventListener("scroll", onScroll, { passive: true });
    return () => scroller.removeEventListener("scroll", onScroll);
  }, []);

  const goToColumn = (idx: number) => {
    paneRefs.current[idx]?.scrollIntoView({
      behavior: "smooth",
      block: "nearest",
      inline: "start",
    });
    setActiveIdx(idx);
  };

  return (
    <div className="flex h-full flex-col">
      {/* Sticky column pill strip — role=tablist */}
      <div
        role="tablist"
        className="sticky top-0 z-10 flex gap-2 overflow-x-auto bg-zinc-900/95 px-3 py-2 backdrop-blur"
      >
        {columns.map((c, i) => {
          const count = (tasksByColumn[c.id] ?? []).length;
          const isActive = i === activeIdx;
          return (
            <button
              key={c.id}
              role="tab"
              aria-selected={isActive}
              onClick={() => goToColumn(i)}
              className={
                "shrink-0 rounded-full px-3 py-1.5 text-sm transition-colors " +
                (isActive
                  ? "bg-blue-600 text-white"
                  : "bg-zinc-800 text-zinc-300 hover:bg-zinc-700")
              }
            >
              {c.label} ({count})
            </button>
          );
        })}
      </div>

      {/* Scroll-snap paged columns */}
      <div
        ref={scrollerRef}
        data-testid="mobile-board-scroller"
        className="flex flex-1 overflow-x-auto overflow-y-hidden"
        style={{ scrollSnapType: "x mandatory", WebkitOverflowScrolling: "touch" }}
      >
        {columns.map((c, i) => {
          const tasks = tasksByColumn[c.id] ?? [];
          return (
            <div
              key={c.id}
              ref={(el) => {
                paneRefs.current[i] = el;
              }}
              className="h-full w-screen shrink-0 overflow-y-auto"
              style={{ scrollSnapAlign: "start" }}
            >
              {tasks.length === 0 ? (
                <EmptyColumn label={c.label} />
              ) : (
                <ColumnContent tasks={tasks} groupBy={groupBy} onOpenTask={onOpenTask} />
              )}
            </div>
          );
        })}
      </div>
    </div>
  );
}

function EmptyColumn({ label }: { label: string }) {
  return (
    <div className="flex h-full items-center justify-center p-6 text-center text-sm text-zinc-500">
      No {label.toLowerCase()} tasks
    </div>
  );
}

function ColumnContent({
  tasks,
  groupBy,
  onOpenTask,
}: {
  tasks: BoardTask[];
  groupBy: GroupBy;
  onOpenTask: (id: string) => void;
}) {
  const groups = groupTasks(tasks, groupBy);
  const [collapsed, setCollapsed] = useState<Record<string, boolean>>({});

  if (groups.length === 1 && groups[0]?.key === "_all") {
    return (
      <ul className="flex flex-col gap-2 p-3">
        {tasks.map((t) => (
          <TaskListItem key={t.id} task={t} onOpen={onOpenTask} />
        ))}
      </ul>
    );
  }

  return (
    <div className="flex flex-col">
      {groups.map((g) => {
        const isCollapsed = collapsed[g.key] ?? false;
        return (
          <section key={g.key}>
            <button
              type="button"
              onClick={() =>
                setCollapsed((c) => ({ ...c, [g.key]: !isCollapsed }))
              }
              className="sticky top-0 z-[1] flex w-full items-center justify-between bg-zinc-900/95 px-3 py-2 text-left text-sm font-medium text-zinc-200 backdrop-blur"
            >
              <span>
                {isCollapsed ? "▸" : "▾"} {g.label} ({g.tasks.length})
              </span>
            </button>
            {!isCollapsed && (
              <ul className="flex flex-col gap-2 p-3">
                {g.tasks.map((t) => (
                  <TaskListItem key={t.id} task={t} onOpen={onOpenTask} />
                ))}
              </ul>
            )}
          </section>
        );
      })}
    </div>
  );
}

function TaskListItem({
  task,
  onOpen,
}: {
  task: BoardTask;
  onOpen: (id: string) => void;
}) {
  return (
    <li>
      <button
        type="button"
        onClick={() => onOpen(task.id)}
        className="w-full rounded-lg border border-zinc-800 bg-zinc-900 p-3 text-left text-sm hover:bg-zinc-800"
      >
        <div className="truncate font-medium">{task.title}</div>
        {task.assignee && (
          <div className="mt-1 text-xs text-zinc-500">{task.assignee}</div>
        )}
      </button>
    </li>
  );
}

function groupTasks(
  tasks: BoardTask[],
  groupBy: GroupBy
): Array<{ key: string; label: string; tasks: BoardTask[] }> {
  if (!groupBy) return [{ key: "_all", label: "", tasks }];
  const buckets = new Map<string, { label: string; tasks: BoardTask[] }>();
  for (const t of tasks) {
    let key: string;
    let label: string;
    switch (groupBy) {
      case "assignee":
        key = t.assignee ?? "_unassigned";
        label = t.assignee ?? "Unassigned";
        break;
      case "parent":
        key = t.parent_id ?? "_root";
        label = t.parent_id ?? "(no parent)";
        break;
      case "label":
        key = t.labels?.[0] ?? "_unlabeled";
        label = t.labels?.[0] ?? "(no label)";
        break;
      case "priority":
        key = String(t.priority ?? "_none");
        label = t.priority != null ? `P${t.priority}` : "(no priority)";
        break;
    }
    if (!buckets.has(key)) buckets.set(key, { label, tasks: [] });
    buckets.get(key)!.tasks.push(t);
  }
  return Array.from(buckets.entries()).map(([key, { label, tasks }]) => ({
    key,
    label,
    tasks,
  }));
}
