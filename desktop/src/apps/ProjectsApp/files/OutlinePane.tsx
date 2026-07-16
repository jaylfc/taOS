interface Heading {
  id: string;
  text: string;
  level: number;
}

interface OutlinePaneProps {
  headings: Heading[];
  activeId: string | null;
  onSelect: (id: string) => void;
  onClose?: () => void;
}

export function OutlinePane({ headings, activeId, onSelect, onClose }: OutlinePaneProps) {
  if (headings.length === 0) return null;

  return (
    <nav className="outline-pane" aria-label="Document outline">
      <div className="outline-header">
        <span className="outline-title">On this page</span>
        {onClose && (
          <button type="button" className="outline-close" onClick={onClose} aria-label="Close outline">
            ×
          </button>
        )}
      </div>
      <ul className="outline-list">
        {headings.map((h) => (
          <li key={h.id}>
            <button
              type="button"
              className={
                "outline-item" + (activeId === h.id ? " outline-item-active" : "")
              }
              style={{ paddingLeft: `${(h.level - 1) * 12 + 8}px` }}
              onClick={() => onSelect(h.id)}
            >
              {h.text}
            </button>
          </li>
        ))}
      </ul>
    </nav>
  );
}

export type { Heading };
