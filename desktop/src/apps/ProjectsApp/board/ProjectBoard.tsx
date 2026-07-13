import { useEffect, useMemo, useState } from "react";
import type { DragEvent } from "react";
import styles from "./ProjectBoard.module.css";
import { BoardToolbar } from "./BoardToolbar";
import { BoardColumn } from "./BoardColumn";
import { BoardLane } from "./BoardLane";
import { TaskCard } from "./TaskCard";
import { useBoardData } from "./useBoardData";
import { useBoardLive } from "./useBoardLive";
import { applyFilters } from "./boardFiltering";
import { groupByAssignee, groupByLabel, groupByParent, groupByPriority } from "./boardGrouping";
import { dndAction } from "./boardDnd";
import { EMPTY_FILTERS } from "./types";
import type { Filters, GroupBy, Task, ViewMode } from "./types";
import { projectsApi } from "../../../lib/projects";
import type { ProjectTask } from "../../../lib/projects";
import { useIsMobile } from "../../../hooks/use-is-mobile";
import { MobileBoardCarousel } from "../mobile/MobileBoardCarousel";
import type { BoardColumn as MobileBoardColumn, BoardTask } from "../mobile/MobileBoardCarousel";

export interface ProjectBoardProps {
  projectId: string;
  currentUserId: string;
  onOpenTask?: (id: string) => void;
  /** When set, the board is scoped to this element and the element filter bar
   *  is hidden (the element is the active filter). Used by element drill-in. */
  elementId?: string | null;
}

const PERSIST_KEY = (pid: string) => `taos.projects.${pid}.board`;
// "timeline" is reserved in the toolbar but not implemented; only persist
// modes that actually render to avoid `lanes!` blowing up on rehydrate.
const VALID_VIEWS: ViewMode[] = ["lanes", "kanban"];
const VALID_GROUPS: GroupBy[] = ["assignee", "parent", "label", "priority"];
const MOBILE_COLUMNS: ReadonlyArray<MobileBoardColumn> = [
  { id: "ready", label: "Ready" },
  { id: "claimed", label: "Claimed" },
  { id: "closed", label: "Closed" },
];
type ColStatus = "ready" | "claimed" | "closed";

export function ProjectBoard({ projectId, currentUserId, onOpenTask, elementId }: ProjectBoardProps) {
  const [viewMode, setViewMode] = useState<ViewMode>("lanes");
  const [groupBy, setGroupBy] = useState<GroupBy>("assignee");
  const [filters, setFilters] = useState<Filters>({ ...EMPTY_FILTERS, elementId: elementId ?? null });
  const [justClaimed, setJustClaimed] = useState<string | null>(null);
  const [movePopover, setMovePopover] = useState<string | null>(null);
  const [announcement, setAnnouncement] = useState("");

  // Keep the board scoped to the drilled-in element when the prop changes.
  useEffect(() => {
    setFilters((f) => (f.elementId === (elementId ?? null) ? f : { ...f, elementId: elementId ?? null }));
  }, [elementId]);

  useEffect(() => {
    try {
      const raw = localStorage.getItem(PERSIST_KEY(projectId));
      if (raw) {
        const p = JSON.parse(raw) as { viewMode?: string; groupBy?: string };
        if (p.viewMode && VALID_VIEWS.includes(p.viewMode as ViewMode)) setViewMode(p.viewMode as ViewMode);
        if (p.groupBy && VALID_GROUPS.includes(p.groupBy as GroupBy)) setGroupBy(p.groupBy as GroupBy);
      }
    } catch { /* ignore */ }
  }, [projectId]);

  useEffect(() => {
    localStorage.setItem(PERSIST_KEY(projectId), JSON.stringify({ viewMode, groupBy }));
  }, [projectId, viewMode, groupBy]);

  const { tasks, elements, applyEvent, setTasks } = useBoardData(projectId);
  const { connected } = useBoardLive(projectId, (e) => {
    if (e.kind === "task.claimed") {
      const id = String((e.payload as { id?: string }).id ?? "");
      if (id) {
        setJustClaimed(id);
        setTimeout(() => setJustClaimed(null), 1500);
      }
    }
    applyEvent(e);
  });

  const filtered = useMemo(() => applyFilters(tasks, filters), [tasks, filters]);

  const isMobile = useIsMobile();

  const tasksByColumn = useMemo<Record<string, BoardTask[]>>(() => ({
    ready: filtered.filter((t) => t.status === "open"),
    claimed: filtered.filter((t) => t.status === "claimed"),
    closed: filtered.filter((t) => t.status === "closed"),
  }), [filtered]);

  const lanes = useMemo(() => {
    if (viewMode !== "lanes") return null;
    const fn = { assignee: groupByAssignee, parent: groupByParent, label: groupByLabel, priority: groupByPriority }[groupBy];
    return fn(filtered);
  }, [viewMode, groupBy, filtered]);

  const counts = useMemo(() => ({
    ready: filtered.filter(t => t.status === "open").length,
    claimed: filtered.filter(t => t.status === "claimed").length,
    closed: filtered.filter(t => t.status === "closed").length,
  }), [filtered]);

  const elementNameById = useMemo(
    () => Object.fromEntries(elements.map(e => [e.id, e.name])),
    [elements],
  );
  const showElementBadge = filters.elementId === null;

  const onCardDragStart = (e: DragEvent<HTMLButtonElement>, task: Task) => {
    e.dataTransfer.setData("text/plain", task.id);
  };

  const dispatchDnd = async (taskId: string, columnStatus: ColStatus, laneKey?: string) => {
    const task = tasks.find(t => t.id === taskId);
    if (!task) return;
    const result = dndAction({
      task,
      target: { columnStatus, laneKey, groupBy: viewMode === "lanes" ? groupBy : undefined },
      viewMode,
      currentUserId,
    });
    if ("blocked" in result) {
      window.alert(result.blocked);
      return;
    }
    const snapshot = tasks;
    for (const c of result.calls) {
      try {
        switch (c.kind) {
          case "claim": await projectsApi.tasks.claim(projectId, c.taskId, c.claimerId); break;
          case "release": await projectsApi.tasks.release(projectId, c.taskId, c.releaserId); break;
          case "close": await projectsApi.tasks.close(projectId, c.taskId, c.closedBy); break;
          case "update": await projectsApi.tasks.update(projectId, c.taskId, c.patch as Partial<ProjectTask>); break;
        }
      } catch (err) {
        console.error("DnD call failed", err);
        setTasks(snapshot);
        window.alert("Could not move card: reverted.");
        return;
      }
    }
    const moved = tasks.find(t => t.id === taskId);
    if (moved) setAnnouncement(`Moved ${moved.title} to ${columnStatus}`);
  };

  const renderCard = (t: Task, drag = true) => (
    <TaskCard
      key={t.id}
      task={t}
      onOpen={(id) => onOpenTask?.(id)}
      onMove={(id) => setMovePopover(id)}
      justClaimed={justClaimed === t.id}
      draggable={drag}
      elementName={showElementBadge && t.element_id ? (elementNameById[t.element_id] ?? null) : null}
      onDragStart={drag ? onCardDragStart : undefined}
    />
  );

  if (isMobile) {
    return (
      <div className={styles.frame}>
        <BoardToolbar
          viewMode={viewMode}
          groupBy={groupBy}
          filters={filters}
          elements={elements}
          live={connected}
          onChangeView={setViewMode}
          onChangeGroup={setGroupBy}
          onChangeFilters={setFilters}
          hideElementFilter={elementId != null}
        />
        <MobileBoardCarousel
          columns={MOBILE_COLUMNS}
          tasksByColumn={tasksByColumn}
          groupBy={groupBy}
          onOpenTask={(id) => onOpenTask?.(id)}
        />
      </div>
    );
  }

  return (
    <div className={styles.frame}>
      <div role="status" aria-live="polite" className={styles.sr}>{announcement}</div>
      <BoardToolbar
        viewMode={viewMode}
        groupBy={groupBy}
        filters={filters}
        elements={elements}
        live={connected}
        onChangeView={setViewMode}
        onChangeGroup={setGroupBy}
        onChangeFilters={setFilters}
        hideElementFilter={elementId != null}
      />
      {viewMode === "kanban" ? (
        <div className={styles.cols}>
          {(["ready", "claimed", "closed"] as ColStatus[]).map(s => {
            const dataStatus = s === "ready" ? "open" : s;
            const cards = filtered.filter(t => t.status === dataStatus);
            return (
              <BoardColumn key={s} status={s} count={cards.length} onDropTask={(id) => dispatchDnd(id, s)}>
                {cards.map(t => renderCard(t))}
              </BoardColumn>
            );
          })}
        </div>
      ) : (
        <div className={styles.lanes}>
          <div className={styles.colsHeader}>
            <span />
            <span>Ready · {counts.ready}</span>
            <span>Claimed · {counts.claimed}</span>
            <span>Closed · {counts.closed}</span>
          </div>
          {lanes!.map(lane => (
            <BoardLane
              key={lane.header.key}
              header={lane.header}
              cells={{
                ready: lane.cards.filter(t => t.status === "open").map(t => renderCard(t)),
                claimed: lane.cards.filter(t => t.status === "claimed").map(t => renderCard(t)),
                closed: lane.cards.filter(t => t.status === "closed").map(t => renderCard(t, false)),
              }}
              onDropTask={(id, status, laneKey) => dispatchDnd(id, status, laneKey)}
            />
          ))}
        </div>
      )}
      {movePopover && (
        <div role="dialog" aria-label="Move task" className={styles.movePop}>
          <p>Move to:</p>
          {(["ready", "claimed", "closed"] as ColStatus[]).map(s => (
            <button
              key={s}
              type="button"
              onClick={() => {
                const id = movePopover;
                setMovePopover(null);
                void dispatchDnd(id, s);
              }}
            >
              {s}
            </button>
          ))}
          <button type="button" onClick={() => setMovePopover(null)}>Cancel</button>
        </div>
      )}
    </div>
  );
}
