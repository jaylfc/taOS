import { useEffect, useRef, type ReactNode, type TouchEvent } from "react";
import { createPortal } from "react-dom";

export interface MobileTaskModalTask {
  id: string;
  title: string;
  description?: string | null;
  status: string;
  priority?: number | null;
  assignee_id?: string | null;
  labels?: string[];
  parent_task_id?: string | null;
}

interface Props {
  task: MobileTaskModalTask;
  onClose: () => void;
  onPrev: () => void;
  onNext: () => void;
  onChangeStatus: (next: string) => void;
  hasPrev: boolean;
  hasNext: boolean;
  heroSlot?: ReactNode;
  metadataSlot?: ReactNode;
  subtasksSlot?: ReactNode;
  relationshipsSlot?: ReactNode;
  activitySlot?: ReactNode;
}

const SWIPE_THRESHOLD_PX = 60;

function primaryActionFor(status: string): { label: string; next: string } {
  switch (status) {
    case "open":
      return { label: "Claim", next: "claimed" };
    case "claimed":
      return { label: "Close", next: "closed" };
    case "closed":
      return { label: "Reopen", next: "open" };
    default:
      return { label: "Update", next: status };
  }
}

/**
 * Full-screen mobile task modal shell.
 *
 * Top bar (prev / title / next / close), five collapsible sections
 * (Hero, Metadata, SubTasks, Relationships, Activity — Activity collapsed by
 * default), and a sticky bottom action bar whose label depends on task status.
 *
 * Each section accepts an optional slot to override the default content; the
 * defaults render directly from the `task` prop.
 */
export function MobileTaskModal({
  task,
  onClose,
  onPrev,
  onNext,
  onChangeStatus,
  hasPrev,
  hasNext,
  heroSlot,
  metadataSlot,
  subtasksSlot,
  relationshipsSlot,
  activitySlot,
}: Props) {
  const action = primaryActionFor(task.status);
  const closeBtnRef = useRef<HTMLButtonElement>(null);
  const touchStartRef = useRef<{ x: number; y: number } | null>(null);

  const onTouchStart = (e: TouchEvent) => {
    if (e.touches.length !== 1) return;
    const t = e.touches[0];
    if (!t) return;
    touchStartRef.current = { x: t.clientX, y: t.clientY };
  };
  const onTouchEnd = (e: TouchEvent) => {
    const start = touchStartRef.current;
    touchStartRef.current = null;
    if (!start) return;
    const t = e.changedTouches[0];
    if (!t) return;
    const dx = t.clientX - start.x;
    const dy = Math.abs(t.clientY - start.y);
    if (Math.abs(dx) < SWIPE_THRESHOLD_PX || dy > Math.abs(dx)) return;
    if (dx < 0 && hasNext) onNext();
    if (dx > 0 && hasPrev) onPrev();
  };

  useEffect(() => {
    const t = window.setTimeout(() => closeBtnRef.current?.focus(), 50);
    return () => window.clearTimeout(t);
  }, []);

  return createPortal(
    <div
      role="dialog"
      aria-modal="true"
      aria-label="Task details"
      data-testid="mobile-task-modal"
      className="fixed inset-0 z-50 flex flex-col bg-zinc-950 text-zinc-200"
      onTouchStart={onTouchStart}
      onTouchEnd={onTouchEnd}
    >
      {/* Top bar */}
      <div
        className="flex items-center gap-2 border-b border-zinc-800 bg-zinc-900 px-3 py-2"
        style={{ paddingTop: "calc(env(safe-area-inset-top, 0px) + 0.5rem)" }}
      >
        <button
          type="button"
          aria-label="Previous task"
          onClick={onPrev}
          disabled={!hasPrev}
          className="rounded-lg px-2 py-1 text-sm text-zinc-300 disabled:opacity-30"
        >
          ‹
        </button>
        <div className="flex-1 truncate text-center text-sm font-medium text-zinc-200">
          {task.title}
        </div>
        <button
          type="button"
          aria-label="Next task"
          onClick={onNext}
          disabled={!hasNext}
          className="rounded-lg px-2 py-1 text-sm text-zinc-300 disabled:opacity-30"
        >
          ›
        </button>
        <button
          ref={closeBtnRef}
          type="button"
          aria-label="Close modal"
          onClick={onClose}
          className="rounded-lg px-2 py-1 text-sm text-zinc-300"
        >
          ✕
        </button>
      </div>

      {/* Scrollable body */}
      <div className="flex-1 overflow-y-auto">
        <Section label="Hero" defaultOpen>
          {heroSlot ?? (
            <div className="space-y-2">
              <div className="text-base font-semibold text-zinc-200">
                {task.title}
              </div>
              <div className="flex items-center gap-2 text-xs">
                <span className="rounded-full bg-zinc-800 px-2 py-0.5 text-zinc-200">
                  {task.status}
                </span>
                {task.priority != null && (
                  <span className="text-zinc-500">P{task.priority}</span>
                )}
              </div>
              {task.description ? (
                <p className="text-sm text-zinc-300">{task.description}</p>
              ) : (
                <Empty>No description</Empty>
              )}
            </div>
          )}
        </Section>

        <Section label="Metadata" defaultOpen>
          {metadataSlot ?? (
            <dl className="grid grid-cols-[auto_1fr] gap-x-3 gap-y-2 text-sm">
              <dt className="text-zinc-500">Assignee</dt>
              <dd className="text-zinc-300">
                {task.assignee_id ?? <Empty>Unassigned</Empty>}
              </dd>
              <dt className="text-zinc-500">Priority</dt>
              <dd className="text-zinc-300">
                {task.priority != null ? `P${task.priority}` : <Empty>—</Empty>}
              </dd>
              <dt className="text-zinc-500">Labels</dt>
              <dd className="text-zinc-300">
                {task.labels && task.labels.length > 0 ? (
                  task.labels.join(", ")
                ) : (
                  <Empty>None</Empty>
                )}
              </dd>
              <dt className="text-zinc-500">Parent</dt>
              <dd className="text-zinc-300">
                {task.parent_task_id ?? <Empty>None</Empty>}
              </dd>
            </dl>
          )}
        </Section>

        <Section label="SubTasks" defaultOpen>
          {subtasksSlot ?? <Empty>No subtasks</Empty>}
        </Section>

        <Section label="Relationships" defaultOpen>
          {relationshipsSlot ?? <Empty>No relationships</Empty>}
        </Section>

        <Section label="Activity" defaultOpen={false}>
          {activitySlot ?? <Empty>No activity yet</Empty>}
        </Section>
      </div>

      {/* Sticky bottom action bar */}
      <div
        className="border-t border-zinc-800 bg-zinc-900 px-3 py-2"
        style={{ paddingBottom: "calc(env(safe-area-inset-bottom, 0px) + 0.5rem)" }}
      >
        <button
          type="button"
          onClick={() => onChangeStatus(action.next)}
          className="w-full rounded-lg bg-blue-600 px-4 py-3 text-sm font-medium text-white"
        >
          {action.label}
        </button>
      </div>
    </div>,
    document.body,
  );
}

function Section({
  label,
  defaultOpen,
  children,
}: {
  label: string;
  defaultOpen: boolean;
  children: ReactNode;
}) {
  return (
    <section aria-label={label} className="border-b border-zinc-800">
      <details open={defaultOpen}>
        <summary className="cursor-pointer list-none px-3 py-3 text-sm font-medium text-zinc-300">
          {label}
        </summary>
        <div className="px-3 pb-3">{children}</div>
      </details>
    </section>
  );
}

function Empty({ children }: { children: ReactNode }) {
  return <span className="text-zinc-500">{children}</span>;
}
