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

  const refresh = async () => {
    try {
      const list = await projectsApi.list("active");
      setProjects(list);
      setError(null);
      const stillExists = selectedId && list.some((p) => p.id === selectedId);
      if (!stillExists) setSelectedId(list.length > 0 ? list[0]!.id : null);
    } catch (e) {
      setError(String(e));
    }
  };

  useEffect(() => {
    refresh();
  }, []);

  const isMobile = useIsMobile();
  const selected = projects.find((p) => p.id === selectedId) ?? null;

  const list = (
    <ProjectList
      projects={projects}
      selectedId={selectedId}
      onSelect={setSelectedId}
      onCreated={refresh}
    />
  );

  const detail = (
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
        list={list}
        detail={detail}
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
        {list}
      </aside>
      <main className="flex-1 min-w-0">
        {detail}
      </main>
    </div>
  );
}
