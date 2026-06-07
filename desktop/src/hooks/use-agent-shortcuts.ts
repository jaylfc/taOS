import { useState, useEffect, useRef } from "react";

/** Icon values emitted by the backend shortcuts API. */
export type ShortcutIcon = "terminal" | "tui" | "diagnostic" | "dashboard";

export interface AgentShortcut {
  idx: number;
  label: string;
  icon: ShortcutIcon;
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
  refetch: () => void;
}

export function useAgentShortcuts(agentId: string): UseAgentShortcutsResult {
  const [shortcuts, setShortcuts] = useState<AgentShortcut[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [tick, setTick] = useState(0);
  const abortRef = useRef<AbortController | null>(null);

  useEffect(() => {
    // Cancel any in-flight request before starting a new one.
    abortRef.current?.abort();
    const controller = new AbortController();
    abortRef.current = controller;

    setLoading(true);
    setError(null);

    fetch(`/api/agents/${agentId}/shortcuts`, { signal: controller.signal })
      .then(async (res) => {
        if (!res.ok) {
          throw new Error(`${res.status} ${res.statusText}`);
        }
        return res.json() as Promise<AgentShortcut[]>;
      })
      .then((data) => {
        setShortcuts(data);
        setLoading(false);
      })
      .catch((err: unknown) => {
        if ((err as { name?: string }).name === "AbortError") return;
        setError(err instanceof Error ? err.message : String(err));
        setShortcuts([]);
        setLoading(false);
      });

    return () => {
      controller.abort();
    };
  }, [agentId, tick]);

  return { shortcuts, loading, error, refetch: () => setTick((t) => t + 1) };
}
