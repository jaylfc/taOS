import { useEffect, useState } from "react";
import { projectsApi, type Project } from "@/lib/projects";
import { useIsMobile } from "../../hooks/use-is-mobile";
import { MobileSplitView } from "../../components/mobile/MobileSplitView";
import { ProjectList } from "./ProjectList";
import { ProjectWorkspace } from "./ProjectWorkspace";

export function ProjectsApp({ windowId: _windowId }: { windowId: string }) {
  const [projects, setProjects] = useState<Project[]>([]);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const isMobile = useIsMobile();

  const refresh = async () => {
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
  };

  useEffect(() => {
    refresh();
  }, []);

  const selected = projects.find((p) => p.id === selectedId) ?? null;

  const listPane = (
    <ProjectList
      projects={projects}
      selectedId={selectedId}
      onSelect={setSelectedId}
      onCreated={refresh}
    />
  );

  const detailPane = (
    <>
      {error && <div role="alert" className="p-3 text-red-400">{error}</div>}
      {selected ? (
        <ProjectWorkspace project={selected} onChanged={refresh} />
      ) : (
        <div className="p-6 text-zinc-500">Select or create a project.</div>
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

  // Desktop branch — byte-identical layout preserved
  return (
    <div className="flex h-full w-full">
      <aside className="w-72 border-r border-zinc-800 flex flex-col">
        {listPane}
      </aside>
      <main className="flex-1 min-w-0">
        {detailPane}
      </main>
    </div>
  );
}
