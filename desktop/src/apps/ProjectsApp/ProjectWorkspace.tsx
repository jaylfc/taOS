import { useEffect, useMemo, useState } from "react";
import { projectsApi, type Project, type ProjectMember, type ProjectElement } from "@/lib/projects";
import { ProjectTaskList } from "./ProjectTaskList";
import { ProjectMembers } from "./ProjectMembers";
import { ProjectActivity } from "./ProjectActivity";
import { ProjectDecisions } from "./ProjectDecisions";
import { ProjectRoutines } from "./ProjectRoutines";
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
import { defaultTabForType } from "./elements/types";
import { ElementGrid } from "./elements/ElementGrid";
import { ElementCreateDialog } from "./elements/ElementCreateDialog";
import styles from "./ProjectsApp.module.css";

export type Tab = "workspace" | "board" | "canvas" | "tasks" | "files" | "messages" | "members" | "activity" | "decisions" | "routines";
const TABS: Tab[] = ["workspace", "board", "canvas", "tasks", "files", "messages", "members", "activity", "decisions", "routines"];

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

// The drilled-in element id rides the URL next to `task` so deep links and
// open-in-new-window work per element (per design doc slice 3).
function readElementParam(): string | null {
  if (typeof window === "undefined") return null;
  return new URLSearchParams(window.location.search).get("element");
}

function setElementParam(elementId: string | null) {
  if (typeof window === "undefined") return;
  const url = new URL(window.location.href);
  if (elementId) url.searchParams.set("element", elementId);
  else url.searchParams.delete("element");
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
  const [elements, setElements] = useState<ProjectElement[]>([]);
  const [activeElementId, setActiveElementId] = useState<string | null>(() => readElementParam());
  const [createDialogOpen, setCreateDialogOpen] = useState(false);

  const handleCreateTask = async ({ title }: { title: string }) => {
    await projectsApi.tasks.create(project.id, { title });
    window.dispatchEvent(
      new CustomEvent("projects:tasks-refresh", { detail: { projectId: project.id } }),
    );
  };

  // Mobile pill order: surface Messages right after Workspace so it is reachable
  // without scrolling (on mobile Messages is its own full page, not a squeezed
  // pane inside Workspace).
  const mobileTabOrder: Tab[] = ["workspace", "messages", "board", "tasks", "canvas", "files", "members", "activity", "decisions", "routines"];
  const tabPills = mobileTabOrder.map((t) => ({
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

  // Elements drive the overview grid and the drill-in navigation. Failure here
  // must never break the rest of the workspace: fall back to an empty list.
  useEffect(() => {
    let cancelled = false;
    projectsApi.elements
      .list(project.id)
      .then((rows) => {
        if (cancelled) return;
        const items = Array.isArray(rows) ? rows : [];
        setElements(items);
        // Honour a deep-linked element param once we know the valid ids.
        const deep = readElementParam();
        if (deep && items.some((e) => e.id === deep)) {
          setActiveElementId(deep);
          const el = items.find((e) => e.id === deep);
          if (el) setTab(defaultTabForType(el.type));
        }
      })
      .catch(() => { if (!cancelled) setElements([]); });
    return () => { cancelled = true; };
  }, [project.id]);

  useEffect(() => {
    const onPop = () => {
      setOpenTaskId(readTaskParam());
      const el = readElementParam();
      setActiveElementId(el);
      if (el) {
        const found = elements.find((e) => e.id === el);
        if (found) setTab(defaultTabForType(found.type));
      }
    };
    window.addEventListener("popstate", onPop);
    return () => window.removeEventListener("popstate", onPop);
  }, [elements]);

  const agentName = useMemo(() => {
    const byId = new Map<string, AgentSummary>();
    for (const a of agents) byId.set(a.id, a);
    return (id: string) => {
      const a = byId.get(id);
      return a ? a.display_name || a.name : id;
    };
  }, [agents]);

  // Resolve a member/agent id to a friendly label for the element card owner
  // chip. Members are agents, so fall back to the agent roster, then the id.
  const memberLabel = useMemo(() => {
    const byId = new Map<string, string>();
    for (const m of members) byId.set(m.member_id, agentName(m.member_id));
    for (const a of agents) byId.set(a.id, a.display_name || a.name);
    return (id: string) => byId.get(id) ?? id;
  }, [members, agents, agentName]);

  const memberOptions = useMemo(
    () => members.map((m) => ({ id: m.member_id, label: memberLabel(m.member_id) })),
    [members, memberLabel],
  );

  const presence = useMemo(
    () => derivePresence({ ownerInitial: "Y", members, agentName }),
    [members, agentName],
  );

  const openTask = (id: string) => { setTaskParam(id); setOpenTaskId(id); };
  const closeTask = () => { setTaskParam(null); setOpenTaskId(null); };

  // Element drill-in: scope the board/canvas/files to the element and land on
  // its type's preferred tab. The id rides the URL for deep links.
  const openElement = (id: string) => {
    const el = elements.find((e) => e.id === id);
    if (!el) return;
    setElementParam(id);
    setActiveElementId(id);
    setTab(defaultTabForType(el.type));
  };
  const openProjectView = () => {
    setElementParam(null);
    setActiveElementId(null);
    setTab("workspace");
  };
  const refreshElements = () => {
    projectsApi.elements
      .list(project.id)
      .then((rows) => setElements(Array.isArray(rows) ? rows : []))
      .catch(() => {});
  };

  // Selecting the workspace tab returns to the element overview grid (clears
  // any drilled-in element). This is the breadcrumb's "project" home too.
  const selectTab = (t: Tab) => {
    if (t === "workspace") {
      setElementParam(null);
      setActiveElementId(null);
    }
    setTab(t);
  };

  // The element we are currently scoped to, validated against the live list so
  // a stale/removed id never silently scopes the board.
  const scopedElement =
    activeElementId && elements.some((e) => e.id === activeElementId)
      ? elements.find((e) => e.id === activeElementId)!
      : null;
  const scopedElementId = scopedElement ? scopedElement.id : null;

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
            onSelect={(id) => selectTab(id as Tab)}
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
                onClick={() => selectTab(t)}
                className={`${styles.tab} ${tab === t ? styles.tabOn : ""}`}
              >
                {t}
              </button>
            ))}
          </nav>
        )}
      </header>

      {scopedElement && (
        <nav className={styles.breadcrumb} aria-label="Breadcrumb">
          <button type="button" className={styles.crumb} onClick={openProjectView}>
            {project.name}
          </button>
          <span className={styles.crumbSep} aria-hidden>/</span>
          <span className={`${styles.crumb} ${styles.crumbOn}`} aria-current="page">
            {scopedElement.name}
          </span>
        </nav>
      )}

      <div
        className={tab === "workspace" ? styles.panel : styles.panelPad}
        role="tabpanel"
        id={`workspace-tabpanel-${tab}`}
        aria-labelledby={`workspace-tab-${tab}`}
      >
        {tab === "workspace" && (
          elements.length > 0 ? (
            <ElementGrid
              project={project}
              elements={elements}
              assigneeName={memberLabel}
              onOpenElement={openElement}
              onAddElement={() => setCreateDialogOpen(true)}
              onOpenProject={openProjectView}
            />
          ) : (
            <ProjectWorkspacePane project={project} />
          )
        )}
        {tab === "board" && (
          <>
            {!authResolved ? (
              <div className="text-sm text-shell-text-secondary">Loading board…</div>
            ) : currentUserId ? (
              <ProjectBoard
                projectId={project.id}
                currentUserId={currentUserId}
                elementId={scopedElementId}
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
        {tab === "canvas" && (
          <CanvasView
            projectId={project.id}
            projectSlug={project.slug}
            elementId={scopedElementId}
          />
        )}
        {tab === "tasks" && <ProjectTaskList projectId={project.id} />}
        {tab === "files" && (
          <FilesApp
            key={scopedElement ? `project-files-${project.id}-${scopedElement.slug}` : `project-files-${project.id}`}
            windowId={`project-files-${project.id}`}
            rootPath={`project:${project.slug}`}
            path={scopedElement?.slug}
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
        {tab === "decisions" && <ProjectDecisions projectId={project.id} />}
        {tab === "routines" && <ProjectRoutines project={project} />}
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

      {createDialogOpen && (
        <ElementCreateDialog
          projectId={project.id}
          memberOptions={memberOptions}
          onClose={() => setCreateDialogOpen(false)}
          onCreated={() => {
            setCreateDialogOpen(false);
            refreshElements();
          }}
        />
      )}
    </div>
  );
}
