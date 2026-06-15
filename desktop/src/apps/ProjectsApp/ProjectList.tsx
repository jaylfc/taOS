import { useState } from "react";
import type { Project } from "@/lib/projects";
import { CreateProjectDialog } from "./CreateProjectDialog";
import styles from "./ProjectsApp.module.css";

type Props = {
  projects: Project[];
  selectedId: string | null;
  onSelect: (id: string) => void;
  onCreated: () => void;
};

function mark(name: string): string {
  const words = name.trim().split(/\s+/).filter(Boolean);
  if (words.length === 0) return "?";
  if (words.length === 1) return words[0]!.slice(0, 2).toUpperCase();
  return (words[0]![0]! + words[1]![0]!).toUpperCase();
}

export function ProjectList({ projects, selectedId, onSelect, onCreated }: Props) {
  const [dialogOpen, setDialogOpen] = useState(false);
  return (
    <aside className={styles.sidebar}>
      <header className={styles.sbHead}>
        <h2>Projects</h2>
        <button
          type="button"
          aria-label="Create project"
          className={styles.newBtn}
          onClick={() => setDialogOpen(true)}
        >
          <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.2">
            <path d="M12 5v14M5 12h14" />
          </svg>
          New
        </button>
      </header>
      <ul className={styles.sbList} aria-label="Projects">
        {projects.length === 0 ? (
          <li className={styles.sbEmpty}>
            <p className={styles.sbEmptyTitle}>No projects yet</p>
            <p className={styles.sbEmptySub}>
              Organise your work and agent conversations into projects.
            </p>
            <button
              type="button"
              onClick={() => setDialogOpen(true)}
              className={styles.newBtn}
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
                className={`${styles.pj} ${p.id === selectedId ? styles.pjOn : ""}`}
              >
                <span className={styles.pjMark} aria-hidden>{mark(p.name)}</span>
                <span className={styles.pjBody}>
                  <span className={styles.pjName}>{p.name}</span>
                  <span className={styles.pjMeta}>{p.slug}</span>
                </span>
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
    </aside>
  );
}
