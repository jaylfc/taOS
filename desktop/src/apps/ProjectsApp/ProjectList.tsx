import { useState } from "react";
import type { Project } from "@/lib/projects";
import { CreateProjectDialog } from "./CreateProjectDialog";

type Props = {
  projects: Project[];
  selectedId: string | null;
  onSelect: (id: string) => void;
  onCreated: () => void;
};

export function ProjectList({ projects, selectedId, onSelect, onCreated }: Props) {
  const [dialogOpen, setDialogOpen] = useState(false);
  return (
    <>
      <header className="p-3 border-b border-zinc-800 flex items-center justify-between">
        <h2 className="font-medium">Projects</h2>
        <button
          type="button"
          aria-label="Create project"
          className="text-sm px-2 py-1 rounded bg-zinc-800 hover:bg-zinc-700"
          onClick={() => setDialogOpen(true)}
        >
          + New
        </button>
      </header>
      <ul className="flex-1 overflow-auto" aria-label="Projects">
        {projects.length === 0 ? (
          <li className="flex flex-col items-center justify-center h-full px-4 py-12 text-center gap-3">
            <p className="text-sm font-medium text-zinc-300">No projects yet</p>
            <p className="text-xs text-zinc-500">Organise your work and agent conversations into projects.</p>
            <button
              type="button"
              onClick={() => setDialogOpen(true)}
              className="mt-1 text-sm px-3 py-1.5 rounded bg-zinc-700 hover:bg-zinc-600 text-zinc-100"
            >
              Create your first project
            </button>
          </li>
        ) : (
          projects.map((p) => (
            <li key={p.id}>
              <button
                type="button"
                aria-pressed={p.id === selectedId}
                onClick={() => onSelect(p.id)}
                className={`w-full text-left px-3 py-2 hover:bg-zinc-800 ${
                  p.id === selectedId ? "bg-zinc-800" : ""
                }`}
              >
                <div className="font-medium">{p.name}</div>
                <div className="text-xs text-zinc-500">{p.slug}</div>
              </button>
            </li>
          ))
        )}
      </ul>
      {dialogOpen && (
        <CreateProjectDialog
          onClose={() => setDialogOpen(false)}
          onCreated={() => {
            setDialogOpen(false);
            onCreated();
          }}
        />
      )}
    </>
  );
}
