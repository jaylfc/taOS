import { useEffect, useRef, useState } from "react";
import { createPortal } from "react-dom";
import styles from "./TaskModal.module.css";
import { Hero } from "./modal/Hero";
import { MetadataPane } from "./modal/MetadataPane";
import { SubTasks } from "./modal/SubTasks";
import { Relationships } from "./modal/Relationships";
import { Activity } from "./modal/Activity";
import { projectsApi } from "../../../lib/projects";
import type { Task } from "./types";
import { useIsMobile } from "../../../hooks/use-is-mobile";
import { MobileTaskModal } from "../mobile/MobileTaskModal";

export interface TaskModalProps {
  projectId: string;
  taskId: string | null;
  currentUserId: string;
  onClose: () => void;
  onPrev?: () => void;
  onNext?: () => void;
}

export function TaskModal({ projectId, taskId, currentUserId, onClose, onPrev, onNext }: TaskModalProps) {
  const [allTasks, setAllTasks] = useState<Task[]>([]);
  const [task, setTask] = useState<Task | null>(null);
  const statusChangeInFlightRef = useRef(false);

  useEffect(() => {
    if (!taskId) { setTask(null); return; }
    let cancelled = false;
    (async () => {
      const all = (await projectsApi.tasks.list(projectId)) as unknown as Task[];
      if (cancelled) return;
      setAllTasks(all);
      setTask(all.find(t => t.id === taskId) ?? null);
    })();
    return () => { cancelled = true; };
  }, [projectId, taskId]);

  useEffect(() => {
    if (!taskId) return;
    const onKey = (e: KeyboardEvent) => {
      const el = e.target as HTMLElement | null;
      const tag = el?.tagName;
      const editable = tag === "INPUT" || tag === "TEXTAREA" || tag === "SELECT" || el?.isContentEditable === true;
      if (e.key === "Escape") onClose();
      else if (!editable && e.key === "ArrowDown") onNext?.();
      else if (!editable && e.key === "ArrowUp") onPrev?.();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [taskId, onClose, onNext, onPrev]);

  const isMobile = useIsMobile();

  const onChangeStatus = async (newStatus: string) => {
    if (!task) return;
    if (statusChangeInFlightRef.current) return;
    statusChangeInFlightRef.current = true;
    try {
      if (newStatus === "claimed") await projectsApi.tasks.claim(projectId, task.id, currentUserId);
      else if (newStatus === "closed") await projectsApi.tasks.close(projectId, task.id, currentUserId);
      else if (newStatus === "open") await projectsApi.tasks.release(projectId, task.id, currentUserId);
      else return;
      const all = (await projectsApi.tasks.list(projectId)) as unknown as Task[];
      setAllTasks(all);
      setTask(all.find((t) => t.id === task.id) ?? null);
    } catch (err) {
      console.error("Mobile status change failed", err);
      window.alert("Could not change status — please retry.");
    } finally {
      statusChangeInFlightRef.current = false;
    }
  };

  if (!taskId) return null;

  if (isMobile) {
    if (!task) {
      return createPortal(
        <div
          role="dialog"
          aria-modal="true"
          aria-label="Loading task"
          className="fixed inset-0 z-50 flex flex-col bg-zinc-950 text-zinc-400"
        >
          <div className="flex justify-end p-3" style={{ paddingTop: "calc(env(safe-area-inset-top, 0px) + 0.5rem)" }}>
            <button
              type="button"
              aria-label="Close"
              onClick={onClose}
              className="rounded-lg px-2 py-1 text-sm text-zinc-300"
            >
              ✕
            </button>
          </div>
          <div className="flex flex-1 items-center justify-center">Loading…</div>
        </div>,
        document.body,
      );
    }
    return (
      <MobileTaskModal
        task={task}
        onClose={onClose}
        onPrev={() => onPrev?.()}
        onNext={() => onNext?.()}
        hasPrev={!!onPrev}
        hasNext={!!onNext}
        onChangeStatus={onChangeStatus}
        heroSlot={<Hero task={task} />}
        metadataSlot={
          <MetadataPane
            projectId={projectId}
            task={task}
            onUpdated={(t) => {
              setTask(t);
              setAllTasks(prev => prev.map(x => x.id === t.id ? t : x));
            }}
          />
        }
        subtasksSlot={<SubTasks all={allTasks} parentId={task.id} />}
        relationshipsSlot={<Relationships projectId={projectId} taskId={task.id} />}
        activitySlot={<Activity projectId={projectId} taskId={task.id} currentUserId={currentUserId} />}
      />
    );
  }

  return createPortal(
    <div className={styles.scrim} role="dialog" aria-modal="true" aria-label={task?.title ?? "Task"}>
      <div className={styles.frame}>
        <header className={styles.bar}>
          <span className={styles.crumb}>Board / <b>{task?.id ?? taskId}</b></span>
          <span className={styles.grow} />
          <button type="button" onClick={onPrev} aria-label="Previous task">↑</button>
          <button type="button" onClick={onNext} aria-label="Next task">↓</button>
          <button type="button" onClick={onClose} aria-label="Close">✕</button>
        </header>
        {task && <Hero task={task} />}
        <div className={styles.body}>
          {task ? (
            <div className={styles.layout}>
              <main className={styles.main}>
                <h1 className={styles.title}>{task.title}</h1>
                {task.body && <p className={styles.bodyText}>{task.body}</p>}
                <SubTasks all={allTasks} parentId={task.id} />
                <Relationships projectId={projectId} taskId={task.id} />
                <Activity projectId={projectId} taskId={task.id} currentUserId={currentUserId} />
              </main>
              <MetadataPane
                projectId={projectId}
                task={task}
                onUpdated={(t) => {
                  setTask(t);
                  setAllTasks(prev => prev.map(x => x.id === t.id ? t : x));
                }}
              />
            </div>
          ) : <p className={styles.loading}>Loading…</p>}
        </div>
      </div>
    </div>,
    document.body,
  );
}
