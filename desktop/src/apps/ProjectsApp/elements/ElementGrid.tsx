import type { Project, ProjectElement } from "../../../lib/projects";
import { ElementCard } from "./ElementCard";
import styles from "./Elements.module.css";

export interface ElementGridProps {
  project: Project;
  elements: ProjectElement[];
  /** Resolve a member/agent id to a display name, or null if unknown. */
  assigneeName: (id: string) => string | null;
  onOpenElement: (id: string) => void;
  onAddElement: () => void;
  onOpenProject: () => void;
}

export function ElementGrid({
  project,
  elements,
  assigneeName,
  onOpenElement,
  onAddElement,
  onOpenProject,
}: ElementGridProps) {
  return (
    <div className={styles.gridWrap}>
      <div className={styles.gridHead}>
        <h2 className={styles.gridTitle}>Elements</h2>
        <button type="button" className={styles.addBtn} onClick={onAddElement}>
          + Add element
        </button>
      </div>
      <div className={styles.grid} role="list">
        <div role="listitem">
          <button
            type="button"
            className={`${styles.card} ${styles.projectCard}`}
            onClick={onOpenProject}
            aria-label="Open project-level view"
          >
            <span className={styles.cardIcon} aria-hidden>🗂️</span>
            <span className={styles.cardBody}>
              <span className={styles.cardName}>{project.name}</span>
              <span className={styles.cardType}>Project</span>
              <span className={styles.cardActivity}>All project-level items</span>
            </span>
          </button>
        </div>

        {elements.map((el) => (
          <div role="listitem" key={el.id}>
            <ElementCard
              element={el}
              assigneeName={assigneeName(el.assignee_id ?? "")}
              onOpen={onOpenElement}
            />
          </div>
        ))}

        <div role="listitem">
          <button
            type="button"
            className={`${styles.card} ${styles.addTile}`}
            onClick={onAddElement}
            aria-label="Add element"
          >
            <span className={styles.cardIcon} aria-hidden>+</span>
            <span className={styles.cardBody}>
              <span className={styles.cardName}>Add element</span>
              <span className={styles.cardType}>Create a nested element</span>
            </span>
          </button>
        </div>
      </div>
    </div>
  );
}
