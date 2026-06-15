import { useEffect, useMemo, useState } from "react";
import { projectsApi, type Project, type ProjectMember } from "@/lib/projects";
import { ProjectTaskList } from "./ProjectTaskList";
import { ProjectMembers } from "./ProjectMembers";
import { ProjectActivity } from "./ProjectActivity";
import { ProjectBoard } from "./board/ProjectBoard";
import { TaskModal } from "./board/TaskModal";
import { FilesApp } from "@/apps/FilesApp";
import { MessagesApp } from "@/apps/MessagesApp";
import { CanvasView } from "./canvas/CanvasView";
import { ProjectWorkspacePane } from "./ProjectWorkspacePane";
import { derivePresence } from "./presence";
import { useIsMobile } from "../../hooks/use-is-mobile";
import { WorkspaceTabPills } from "../../components/mobile/WorkspaceTabPills";
import { ProjectFab } from "./mobile/ProjectFab";
import { TaskCreateSheet } from "./mobile/TaskCreateSheet";
import styles from "./ProjectsApp.module.css";

type Tab = "workspace" | "board" | "canvas" | "tasks" | "files" | "messages" | "members" | "activity";
const TABS: Tab[] = ["workspace", "board", "canvas", "tasks", "files", "messages", "members", "activity"];

interface AgentSummary {
  id: string;
  name: string;
  display_name?: string;
}

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
  const [tab, setTab] = useState<Tab>("workspace");
  const [currentUserId, setCurrentUserId] = useState<string | null>(null);
  const [authResolved, setAuthResolved] = useState(false);
  const [openTaskId, setOpenTaskId] = useState<string | null>(() => readTaskParam());
  const [createSheetOpen, setCreateSheetOpen] = useState(false);
  const [members, setMembers] = useState<ProjectMember[]>([]);
  const [agents, setAgents] = useState<AgentSummary[]>([]);

  const handleCreateTask = async ({ title }: { title: string }) => {
    await projectsApi.tasks.create(project.id, { title });
    window.dispatchEvent(
      new CustomEvent("projects:tasks-refresh", { detail: { projectId: project.id } }),
    );
  };

  const tabPills = TABS.map((t) => ({
    id: t,
    label: t.charAt(0).toUpperCase() + t.slice(1),
  }));

  useEffect(() => {
    let cancelled = false;
    fetch("/auth/me")
      .then((r) => (r.ok ? r.json() : null))
      .then((u) => { if (!cancelled) { if (u?.user?.id) setCurrentUserId(u.user.id); setAuthResolved(true); } })
      .catch(() => { if (!cancelled) setAuthResolved(true); });
    return () => { cancelled = true; };
  }, []);

  // Members + agent roster drive the header presence row (static-but-real:
  // derived from the existing member data, not live multiplayer presence).
  useEffect(() => {
    let cancelled = false;
    projectsApi.members
      .list(project.id)
      .then((rows) => { if (!cancelled) setMembers(Array.isArray(rows) ? rows : []); })
      .catch(() => { if (!cancelled) setMembers([]); });
    return () => { cancelled = true; };
  }, [project.id]);

  useEffect(() => {
    let cancelled = false;
    fetch("/api/agents")
      .then((r) => (r.ok ? r.json() : []))
      .then((rows) => { if (!cancelled && Array.isArray(rows)) setAgents(rows); })
      .catch(() => {});
    return () => { cancelled = true; };
  }, []);

  useEffect(() => {
    const onPop = () => setOpenTaskId(readTaskParam());
    window.addEventListener("popstate", onPop);
    return () => window.removeEventListener("popstate", onPop);
  }, []);

  const agentName = useMemo(() => {
    const byId = new Map<string, AgentSummary>();
    for (const a of agents) byId.set(a.id, a);
    return (id: string) => {
      const a = byId.get(id);
      return a ? a.display_name || a.name : id;
    };
  }, [agents]);

  const presence = useMemo(
    () => derivePresence({ ownerInitial: "Y", members, agentName }),
    [members, agentName],
  );

  const openTask = (id: string) => { setTaskParam(id); setOpenTaskId(id); };
  const closeTask = () => { setTaskParam(null); setOpenTaskId(null); };

  return (
    <div className="flex flex-col h-full min-h-0">
      <header className={styles.header}>
        <div className={styles.titleRow}>
          <h1 title={project.name}>{project.name}</h1>
          {!isMobile && presence.length > 0 && (
            <div className={styles.presence}>
              <div className={styles.stack}>
                {presence.map((f) => (
                  <span
                    key={f.id}
                    className={`${styles.av} ${f.kind === "agent" ? styles.avAgent : styles.avHuman}`}
                    title={f.title}
                  >
                    {f.initial}
                    <span className={styles.avRing} aria-hidden />
                  </span>
                ))}
              </div>
              <span className={styles.presenceLbl}>
                {presence.length} {presence.length === 1 ? "here" : "here now"}
              </span>
            </div>
          )}
        </div>
        {project.description && (
          <p className={styles.desc} title={project.description}>{project.description}</p>
        )}
        {isMobile ? (
          <WorkspaceTabPills
            tabs={tabPills}
            active={tab}
            onSelect={(id) => setTab(id as Tab)}
          />
        ) : (
          <nav className={styles.tabs} role="tablist">
            {TABS.map((t) => (
              <button
                key={t}
                type="button"
                role="tab"
                id={`workspace-tab-${t}`}
                aria-selected={tab === t}
                aria-controls={`workspace-tabpanel-${t}`}
                onClick={() => setTab(t)}
                className={`${styles.tab} ${tab === t ? styles.tabOn : ""}`}
              >
                {t}
              </button>
            ))}
          </nav>
        )}
      </header>

      <div
        className={tab === "workspace" ? styles.panel : styles.panelPad}
        role="tabpanel"
        id={`workspace-tabpanel-${tab}`}
        aria-labelledby={`workspace-tab-${tab}`}
      >
        {tab === "workspace" && <ProjectWorkspacePane project={project} />}
        {tab === "board" && (
          <>
            {!authResolved ? (
              <div className="text-sm text-shell-text-secondary">Loading board…</div>
            ) : currentUserId ? (
              <ProjectBoard
                projectId={project.id}
                currentUserId={currentUserId}
                onOpenTask={openTask}
              />
            ) : (
              <div className="text-sm text-shell-text-secondary">Sign in required to view the board.</div>
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

      {isMobile && (tab === "tasks" || tab === "board") && (
        <>
          <ProjectFab onClick={() => setCreateSheetOpen(true)} />
          <TaskCreateSheet
            open={createSheetOpen}
            onClose={() => setCreateSheetOpen(false)}
            onSubmit={handleCreateTask}
          />
        </>
      )}
    </div>
  );
}
