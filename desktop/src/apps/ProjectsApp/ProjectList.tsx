import { useState } from "react";
import type { Project } from "@/lib/projects";
import { CreateProjectDialog } from "./CreateProjectDialog";
import styles from "./ProjectsApp.module.css";

type Props = {
  projects: Project[];
  selectedId: string | null;
  onSelect: (id: string) => void;
  onCreated: () => void;
  onOpenInNewWindow?: (id: string) => void;
  collapsed?: boolean;
  onToggleCollapse?: () => void;
};

function mark(name: string): string {
  const words = name.trim().split(/\s+/).filter(Boolean);
  if (words.length === 0) return "?";
  if (words.length === 1) return words[0]!.slice(0, 2).toUpperCase();
  return (words[0]![0]! + words[1]![0]!).toUpperCase();
}

export function ProjectList({ projects, selectedId, onSelect, onCreated, onOpenInNewWindow, collapsed, onToggleCollapse }: Props) {
  const [dialogOpen, setDialogOpen] = useState(false);

  if (collapsed) {
    return (
      <aside className={styles.sidebarCollapsed} aria-label="Projects">
        <div className={styles.railTop}>
          <button
            type="button"
            aria-label="Expand projects sidebar"
            className={styles.collapseBtn}
            onClick={onToggleCollapse}
          >
            <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
              <path d="M9 6l6 6-6 6" />
            </svg>
          </button>
        </div>
        <ul className={styles.railList} aria-label="Projects">
          {projects.map((p) => (
            <li key={p.id}>
              <button
                type="button"
                title={p.name}
                aria-label={p.name}
                aria-pressed={p.id === selectedId}
                onClick={() => onSelect(p.id)}
                className={`${styles.railMark} ${p.id === selectedId ? styles.railMarkOn : ""}`}
              >
                {mark(p.name)}
              </button>
            </li>
          ))}
        </ul>
        <button
          type="button"
          aria-label="Create project"
          className={styles.collapseBtn}
          style={{ marginBottom: 8 }}
          onClick={() => setDialogOpen(true)}
        >
          <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.2">
            <path d="M12 5v14M5 12h14" />
          </svg>
        </button>
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

  return (
    <aside className={styles.sidebar}>
      <header className={styles.sbHead}>
        <h2>Projects</h2>
        <div style={{ display: "flex", alignItems: "center", gap: 6 }}>
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
          {onToggleCollapse && (
            <button
              type="button"
              aria-label="Collapse projects sidebar"
              className={styles.collapseBtn}
              onClick={onToggleCollapse}
            >
              <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
                <path d="M15 6l-6 6 6 6" />
              </svg>
            </button>
          )}
        </div>
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
            <li key={p.id} className={styles.pjRow}>
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
              {onOpenInNewWindow && (
                <button
                  type="button"
                  className={styles.pjNewWin}
                  title="Open in new window"
                  aria-label={`Open ${p.name} in a new window`}
                  onClick={() => onOpenInNewWindow(p.id)}
                >
                  <svg viewBox="0 0 24 24" width="14" height="14" fill="none" stroke="currentColor" strokeWidth="2" aria-hidden>
                    <path d="M7 17L17 7M9 7h8v8" />
                  </svg>
                </button>
              )}
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
