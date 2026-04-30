import { useState, useEffect } from "react";

export interface AgentShortcut {
  idx: number;
  label: string;
  icon: string;
  kind: "container-terminal" | "tui" | "dashboard";
  requires_capability: string;
  command?: string;
  port?: number;
  path?: string;
}

interface UseAgentShortcutsResult {
  shortcuts: AgentShortcut[];
  loading: boolean;
  error: string | null;
}

export function useAgentShortcuts(agentId: string): UseAgentShortcutsResult {
  const [shortcuts, setShortcuts] = useState<AgentShortcut[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    setError(null);

    fetch(`/api/agents/${agentId}/shortcuts`)
      .then(async (res) => {
        if (!res.ok) {
          throw new Error(`${res.status} ${res.statusText}`);
        }
        return res.json() as Promise<AgentShortcut[]>;
      })
      .then((data) => {
        if (!cancelled) {
          setShortcuts(data);
          setLoading(false);
        }
      })
      .catch((err: unknown) => {
        if (!cancelled) {
          setError(err instanceof Error ? err.message : String(err));
          setShortcuts([]);
          setLoading(false);
        }
      });

    return () => {
      cancelled = true;
    };
  }, [agentId]);

  return { shortcuts, loading, error };
}
