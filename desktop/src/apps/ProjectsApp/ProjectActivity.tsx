import { useEffect, useMemo, useState } from "react";
import { projectsApi, type ProjectActivity } from "@/lib/projects";

interface AgentSummary {
  id: string;
  name: string;
  display_name?: string;
  emoji?: string;
}

// Fields inside activity payloads that hold an agent or user id we can swap
// for a friendly name. Keeps the substitution narrow — we don't want to scan
// arbitrary string values.
const ID_PAYLOAD_FIELDS = ["member_id", "actor_id", "user_id", "agent_id"] as const;

function formatPayload(payload: Record<string, unknown>, byId: Map<string, AgentSummary>): string {
  const decorated: Record<string, unknown> = {};
  for (const [k, v] of Object.entries(payload)) {
    if (typeof v === "string" && (ID_PAYLOAD_FIELDS as readonly string[]).includes(k)) {
      const agent = byId.get(v);
      decorated[k] = agent ? agent.display_name || agent.name : v;
    } else {
      decorated[k] = v;
    }
  }
  return JSON.stringify(decorated);
}

function formatActor(actorId: string, byId: Map<string, AgentSummary>): string {
  if (!actorId || actorId === "system") return actorId || "system";
  const agent = byId.get(actorId);
  return agent ? agent.display_name || agent.name : actorId;
}

export function ProjectActivity({ projectId }: { projectId: string }) {
  const [items, setItems] = useState<ProjectActivity[]>([]);
  const [agents, setAgents] = useState<AgentSummary[]>([]);

  useEffect(() => {
    let cancelled = false;
    projectsApi
      .activity(projectId)
      .then((rows) => {
        if (!cancelled) setItems(rows);
      })
      .catch(() => {
        if (!cancelled) setItems([]);
      });
    return () => {
      cancelled = true;
    };
  }, [projectId]);

  // Fetch the agent roster once so payload member_id values render as names.
  useEffect(() => {
    let cancelled = false;
    fetch("/api/agents")
      .then((r) => (r.ok ? r.json() : []))
      .then((rows) => {
        if (!cancelled && Array.isArray(rows)) setAgents(rows);
      })
      .catch(() => {});
    return () => {
      cancelled = true;
    };
  }, []);

  const byId = useMemo(() => {
    const m = new Map<string, AgentSummary>();
    for (const a of agents) m.set(a.id, a);
    return m;
  }, [agents]);

  return (
    <ul className="space-y-1" aria-label="Activity">
      {items.map((a) => (
        <li
          key={a.id}
          className="bg-zinc-900 px-3 py-2 rounded text-sm flex flex-col gap-1 md:flex-row-reverse md:items-baseline md:justify-end md:gap-2"
        >
          <div className="md:contents">
            <span className="font-medium">{a.kind}</span>
            <span className="text-xs text-zinc-400 ml-2">{formatActor(a.actor_id, byId)}</span>
            {a.payload && Object.keys(a.payload).length > 0 && (
              <span className="ml-2 text-zinc-500 text-xs">{formatPayload(a.payload, byId)}</span>
            )}
          </div>
          <span className="text-zinc-500 text-xs md:text-sm md:tabular-nums">
            {new Date(a.created_at * 1000).toLocaleString()}
          </span>
        </li>
      ))}
      {items.length === 0 && <li className="text-sm text-zinc-500">No activity.</li>}
    </ul>
  );
}
