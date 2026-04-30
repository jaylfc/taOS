import { useAgentShortcuts, type AgentShortcut } from "../hooks/use-agent-shortcuts";
import { useIsMobile } from "../hooks/use-is-mobile";

const ICON_GLYPHS: Record<string, string> = {
  terminal: "⌨",
  tui: "▣",
  dashboard: "⊞",
  diagnostic: "⚕",
};

interface AgentShortcutRowProps {
  agentId: string;
  onLaunch: (agentId: string, shortcut: AgentShortcut) => void;
}

export function AgentShortcutRow({ agentId, onLaunch }: AgentShortcutRowProps) {
  const { shortcuts, loading } = useAgentShortcuts(agentId);
  const isMobile = useIsMobile();

  if (loading || shortcuts.length === 0) {
    return null;
  }

  return (
    <div className="agent-shortcut-row" role="group" aria-label="Agent shortcuts">
      {shortcuts.map((shortcut) => {
        const glyph = ICON_GLYPHS[shortcut.icon] ?? "▶";
        return (
          <button
            key={shortcut.idx}
            type="button"
            aria-label={shortcut.label}
            title={shortcut.label}
            className="agent-shortcut-btn"
            onClick={() => onLaunch(agentId, shortcut)}
          >
            <span className="agent-shortcut-icon" aria-hidden="true">{glyph}</span>
            {!isMobile && (
              <span className="agent-shortcut-label" data-label="true">{shortcut.label}</span>
            )}
          </button>
        );
      })}
    </div>
  );
}
