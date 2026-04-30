import { useState, useRef, useEffect } from "react";
import { createPortal } from "react-dom";
import styles from "./BoardToolbar.module.css";
import type { ViewMode, GroupBy, Filters } from "./types";
import { BoardFilters } from "./BoardFilters";
import { useIsMobile } from "../../../hooks/use-is-mobile";

export interface BoardToolbarProps {
  viewMode: ViewMode;
  groupBy: GroupBy;
  filters: Filters;
  live: boolean;
  onChangeView: (m: ViewMode) => void;
  onChangeGroup: (g: GroupBy) => void;
  onChangeFilters: (f: Filters) => void;
  onAddTask?: () => void;
}

const VIEWS: ViewMode[] = ["lanes", "kanban", "timeline"];
const VIEW_LABEL: Record<ViewMode, string> = {
  lanes: "▦ Lanes",
  kanban: "▤ Kanban",
  timeline: "⇆ Timeline",
};

export function BoardToolbar(p: BoardToolbarProps) {
  const isMobile = useIsMobile();
  const [sheetOpen, setSheetOpen] = useState(false);
  const groupSelectRef = useRef<HTMLSelectElement>(null);

  useEffect(() => {
    if (!sheetOpen) return;
    const t = window.setTimeout(() => groupSelectRef.current?.focus(), 50);
    return () => window.clearTimeout(t);
  }, [sheetOpen]);

  if (isMobile) {
    return (
      <>
        <div className="flex items-center justify-between border-b border-zinc-800 px-3 py-2">
          <button
            type="button"
            onClick={() => setSheetOpen(true)}
            className="rounded-full bg-zinc-800 px-3 py-1.5 text-sm text-zinc-200"
          >
            Filter / Group ▾
          </button>
        </div>
        {sheetOpen && createPortal(
          <div
            role="dialog"
            aria-modal="true"
            aria-label="Filter and group"
            className="fixed inset-0 z-40 flex items-end"
          >
            <button
              type="button"
              aria-label="Dismiss"
              className="absolute inset-0 bg-black/60"
              onClick={() => setSheetOpen(false)}
            />
            <div
              className="relative z-10 w-full rounded-t-2xl bg-zinc-900 p-4 text-zinc-100"
              style={{ paddingBottom: "calc(env(safe-area-inset-bottom, 0px) + 1rem)" }}
            >
              <div className="mx-auto mb-3 h-1 w-10 rounded-full bg-zinc-700" />
              <h2 className="mb-3 text-base font-semibold">Filter & group</h2>

              {/* On mobile we always show group-by; the carousel applies it regardless of viewMode. */}
              <label className="mb-3 flex items-center gap-2 text-sm">
                <span className="text-zinc-300">Group by</span>
                <select
                  ref={groupSelectRef}
                  value={p.groupBy}
                  onChange={(e) => p.onChangeGroup(e.target.value as GroupBy)}
                  className="flex-1 rounded bg-zinc-800 px-2 py-1 text-sm text-zinc-100"
                >
                  <option value="assignee">Assignee</option>
                  <option value="parent">Parent</option>
                  <option value="label">Label</option>
                  <option value="priority">Priority</option>
                </select>
              </label>

              <input
                type="search"
                className="mb-3 w-full rounded bg-zinc-800 px-3 py-2 text-sm text-zinc-100 placeholder:text-zinc-500"
                placeholder="Search tasks…"
                value={p.filters.search}
                onChange={(e) => p.onChangeFilters({ ...p.filters, search: e.target.value })}
                aria-label="Search tasks"
              />

              <BoardFilters value={p.filters} onChange={p.onChangeFilters} />

              <button
                type="button"
                onClick={() => setSheetOpen(false)}
                className="mt-4 w-full rounded-lg bg-blue-600 px-4 py-2 text-sm font-medium text-white"
              >
                Done
              </button>
            </div>
          </div>,
          document.body,
        )}
      </>
    );
  }

  return (
    <div className={styles.bar}>
      <div className={styles.crumb}>Board</div>
      <div className={styles.grow} />
      <div className={styles.seg} role="tablist" aria-label="Board view">
        {VIEWS.map(m => (
          <button
            key={m}
            type="button"
            role="tab"
            aria-selected={p.viewMode === m}
            disabled={m === "timeline"}
            onClick={() => p.onChangeView(m)}
            className={p.viewMode === m ? styles.on : ""}
          >
            {VIEW_LABEL[m]}
          </button>
        ))}
      </div>
      {p.viewMode === "lanes" && (
        <label className={styles.pill} aria-label="Group by">
          Group:
          <select
            value={p.groupBy}
            onChange={(e) => p.onChangeGroup(e.target.value as GroupBy)}
          >
            <option value="assignee">Assignee</option>
            <option value="parent">Parent</option>
            <option value="label">Label</option>
            <option value="priority">Priority</option>
          </select>
        </label>
      )}
      <input
        className={styles.search}
        placeholder="Search tasks…"
        value={p.filters.search}
        onChange={(e) => p.onChangeFilters({ ...p.filters, search: e.target.value })}
        aria-label="Search tasks"
      />
      <BoardFilters value={p.filters} onChange={p.onChangeFilters} />
      <span className={`${styles.pill} ${p.live ? styles.live : styles.dead}`}>● Live</span>
      {p.onAddTask && (
        <button type="button" className={styles.add} onClick={p.onAddTask}>＋ Task</button>
      )}
    </div>
  );
}
