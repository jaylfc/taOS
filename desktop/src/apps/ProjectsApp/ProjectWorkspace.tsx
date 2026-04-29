import { useEffect, useState } from "react";
import type { Project } from "@/lib/projects";
import { ProjectTaskList } from "./ProjectTaskList";
import { ProjectMembers } from "./ProjectMembers";
import { ProjectActivity } from "./ProjectActivity";
import { ProjectBoard } from "./board/ProjectBoard";
import { TaskModal } from "./board/TaskModal";
import { FilesApp } from "@/apps/FilesApp";
import { MessagesApp } from "@/apps/MessagesApp";
import { CanvasView } from "./canvas/CanvasView";
import { useIsMobile } from "../../hooks/use-is-mobile";
import { WorkspaceTabPills } from "../../components/mobile/WorkspaceTabPills";

type Tab = "board" | "canvas" | "tasks" | "files" | "messages" | "members" | "activity";
const TABS: Tab[] = ["board", "canvas", "tasks", "files", "messages", "members", "activity"];

function readTaskParam(): string | null {
  if (typeof window === "undefined") return null;
  return new URLSearchParams(window.location.search).get("task");
}

function setTaskParam(taskId: string | null) {
  if (typeof window === "undefined") return;
  const url = new URL(window.location.href);
  if (taskId) url.searchParams.set("task", taskId);
  else url.searchParams.delete("task");
  window.history.pushState({}, "", url);
}

export function ProjectWorkspace({ project, onChanged }: { project: Project; onChanged: () => void }) {
  const isMobile = useIsMobile();
  const [tab, setTab] = useState<Tab>("tasks");
  const [currentUserId, setCurrentUserId] = useState<string | null>(null);
  const [authResolved, setAuthResolved] = useState(false);
  const [openTaskId, setOpenTaskId] = useState<string | null>(() => readTaskParam());

  const tabPills = TABS.map((t) => ({
    id: t,
    label: t.charAt(0).toUpperCase() + t.slice(1),
  }));

  useEffect(() => {
    let cancelled = false;
    fetch("/api/auth/me")
      .then((r) => (r.ok ? r.json() : null))
      .then((u) => { if (!cancelled) { if (u?.user?.id) setCurrentUserId(u.user.id); setAuthResolved(true); } })
      .catch(() => { if (!cancelled) setAuthResolved(true); });
    return () => { cancelled = true; };
  }, []);

  useEffect(() => {
    const onPop = () => setOpenTaskId(readTaskParam());
    window.addEventListener("popstate", onPop);
    return () => window.removeEventListener("popstate", onPop);
  }, []);

  const openTask = (id: string) => { setTaskParam(id); setOpenTaskId(id); };
  const closeTask = () => { setTaskParam(null); setOpenTaskId(null); };

  return (
    <div className="flex flex-col h-full">
      <header className="px-4 py-3 border-b border-zinc-800">
        <h1 className="text-lg font-semibold">{project.name}</h1>
        <p className="text-xs text-zinc-500">{project.description}</p>
      </header>
      {isMobile ? (
        <WorkspaceTabPills
          tabs={tabPills}
          active={tab}
          onSelect={(id) => setTab(id as Tab)}
        />
      ) : (
        <nav className="flex border-b border-zinc-800 px-2" role="tablist">
          {TABS.map((t) => (
            <button
              key={t}
              type="button"
              role="tab"
              aria-selected={tab === t}
              onClick={() => setTab(t)}
              className={`px-3 py-2 text-sm capitalize ${
                tab === t ? "border-b-2 border-blue-400" : "text-zinc-400"
              }`}
            >
              {t}
            </button>
          ))}
        </nav>
      )}
      <div className="flex-1 min-h-0 overflow-auto p-4">
        {tab === "board" && (
          <>
            {!authResolved ? (
              <div className="text-sm text-zinc-500">Loading board…</div>
            ) : currentUserId ? (
              <ProjectBoard
                projectId={project.id}
                currentUserId={currentUserId}
                onOpenTask={openTask}
              />
            ) : (
              <div className="text-sm text-zinc-500">Sign in required to view the board.</div>
            )}
            {currentUserId && (
              <TaskModal
                projectId={project.id}
                taskId={openTaskId}
                currentUserId={currentUserId}
                onClose={closeTask}
              />
            )}
          </>
        )}
        {tab === "canvas" && <CanvasView projectId={project.id} projectSlug={project.slug} />}
        {tab === "tasks" && <ProjectTaskList projectId={project.id} />}
        {tab === "files" && (
          <FilesApp
            key={project.id}
            windowId={`project-files-${project.id}`}
            rootPath={`project:${project.slug}`}
          />
        )}
        {tab === "messages" && (
          <MessagesApp
            key={project.id}
            windowId={`project-messages-${project.id}`}
            scope={{ projectId: project.id }}
          />
        )}
        {tab === "members" && <ProjectMembers project={project} onChanged={onChanged} />}
        {tab === "activity" && <ProjectActivity projectId={project.id} />}
      </div>
    </div>
  );
}
