import { Terminal, Wrench, ExternalLink, Stethoscope, Play } from "lucide-react";
import { useAgentShortcuts, type AgentShortcut } from "../hooks/use-agent-shortcuts";
import { Button } from "./ui";

const ICON_BY_HINT: Record<string, React.ComponentType<{ size?: number }>> = {
  terminal: Terminal,
  tui: Wrench,
  dashboard: ExternalLink,
  diagnostic: Stethoscope,
};

interface AgentShortcutRowProps {
  agentId: string;
  onLaunch: (agentId: string, shortcut: AgentShortcut) => void;
  btnCls?: string;
}

export function AgentShortcutRow({ agentId, onLaunch, btnCls = "h-8 w-8" }: AgentShortcutRowProps) {
  const { shortcuts, loading } = useAgentShortcuts(agentId);

  if (loading || shortcuts.length === 0) {
    return null;
  }

  return (
    <>
      {shortcuts.map((shortcut) => {
        const Icon = ICON_BY_HINT[shortcut.icon] ?? Play;
        return (
          <Button
            key={shortcut.idx}
            variant="ghost"
            size="icon"
            className={btnCls}
            aria-label={shortcut.label}
            title={shortcut.label}
            onClick={() => onLaunch(agentId, shortcut)}
          >
            <Icon size={15} aria-hidden="true" />
          </Button>
        );
      })}
    </>
  );
}
