import { useEffect, useState, useCallback } from "react";
import { projectsApi, type Project } from "@/lib/projects";
import { useProcessStore } from "@/stores/process-store";
import { getApp } from "@/registry/app-registry";
import { useIsMobile } from "../../hooks/use-is-mobile";
import { useRefreshOnFocus } from "@/hooks/use-refresh-on-focus";
import { MobileSplitView } from "../../components/mobile/MobileSplitView";
import { ProjectList } from "./ProjectList";
import { ProjectWorkspace } from "./ProjectWorkspace";

// `projectId` is a per-window prop: each Projects window can be pinned to a
// different project (opened via the project list's "open in new window"), so
// the user can view two projects side by side. Omitted for the first window.
// `tab` and `filePath` are deep-link launch props: a notification target can
// open a specific project on a specific tab (and, for the Files tab, a specific
// document). They ride the same prop-merge/launchNonce path as `projectId`.
export function ProjectsApp({
  windowId: _windowId,
  projectId,
  tab,
  filePath,
}: {
  windowId: string;
  projectId?: string;
  tab?: string;
  filePath?: string;
}) {
  const [projects, setProjects] = useState<Project[]>([]);
  const [selectedId, setSelectedId] = useState<string | null>(projectId ?? null);
  const openWindow = useProcessStore((s) => s.openWindow);

  const openInNewWindow = (id: string) => {
    const size = getApp("projects")?.defaultSize ?? { w: 1100, h: 720 };
    openWindow("projects", size, { projectId: id }, { forceNew: true });
  };
  const [error, setError] = useState<string | null>(null);
  const [sidebarCollapsed, setSidebarCollapsed] = useState(false);
  const isMobile = useIsMobile();

  const refresh = useCallback(async () => {
    try {
      const list = await projectsApi.list("active");
      setProjects(list);
      setError(null);
      // Mobile shows the project list as its own screen; auto-selecting
      // the first project would slide the user straight into the detail
      // view and skip the list. Desktop's split layout shows both at once,
      // so picking up the first project is a useful default there.
      const stillExists = selectedId && list.some((p) => p.id === selectedId);
      if (!stillExists && !isMobile) {
        setSelectedId(list.length > 0 ? list[0]!.id : null);
      } else if (!stillExists) {
        setSelectedId(null);
      }
    } catch (e) {
      setError(String(e));
    }
  }, [selectedId, isMobile]);

  // Mount only. `refresh` is derived from selectedId, so depending on its
  // identity re-lists every project on each selection; the focus hook keeps
  // its own latest-callback ref, so it still runs the current closure.
  useEffect(() => {
    void refresh();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  useRefreshOnFocus(refresh);

  const selected = projects.find((p) => p.id === selectedId) ?? null;

  const listPane = (
    <ProjectList
      projects={projects}
      selectedId={selectedId}
      onSelect={setSelectedId}
      onCreated={refresh}
    />
  );

  // Desktop sidebar can collapse to a narrow rail so the workspace/chat area
  // gets the room. Mobile uses its own split view, so collapse is desktop-only.
  const desktopListPane = (
    <ProjectList
      projects={projects}
      selectedId={selectedId}
      onSelect={setSelectedId}
      onCreated={refresh}
      onOpenInNewWindow={openInNewWindow}
      collapsed={sidebarCollapsed}
      onToggleCollapse={() => setSidebarCollapsed((v) => !v)}
    />
  );

  const detailPane = (
    <>
      {error && <div role="alert" className="p-3 text-red-400">{error}</div>}
      {selected ? (
        <ProjectWorkspace project={selected} onChanged={refresh} initialTab={tab} filePath={filePath} />
      ) : (
        <div className="p-6 text-shell-text-secondary">Select or create a project.</div>
      )}
    </>
  );

  if (isMobile) {
    return (
      <MobileSplitView
        list={listPane}
        detail={detailPane}
        selectedId={selectedId}
        onBack={() => setSelectedId(null)}
        listTitle="Projects"
      />
    );
  }

  // Desktop branch: project-list sidebar + main column. ProjectList renders
  // its own <aside> (the 248px sidebar), so this row just lays them out.
  return (
    <div className="flex h-full w-full bg-shell-bg text-shell-text">
      {desktopListPane}
      <main className="flex-1 min-w-0 flex flex-col min-h-0">
        {detailPane}
      </main>
    </div>
  );
}
