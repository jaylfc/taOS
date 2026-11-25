import { toMs } from "./time";

/**
 * Agent conversation sessions (#2123).
 *
 * A conversation session with an agent is a DM channel between the
 * user and that agent. The controller exposes them per agent:
 *
 *   GET  /api/chat/agents/{slug}/conversations  -> { conversations }
 *   POST /api/chat/agents/{slug}/conversations  -> { conversation }
 *
 * Starting a new chat is a pure controller operation — it never waits
 * on the agent — and previous sessions are kept, never discarded.
 */
export interface AgentConversation {
  id: string;
  agent: string;
  title: string;
  model?: string | null;
  created_at?: number | string | null;
  last_message_at?: number | string | null;
}

function activityKey(c: AgentConversation): number {
  const ts = c.last_message_at ?? c.created_at;
  if (ts == null) return 0;
  const ms = toMs(ts);
  return Number.isNaN(ms) ? 0 : ms;
}

/** Most recent activity first; ties fall back to creation order. */
export function sortConversations(
  list: AgentConversation[],
): AgentConversation[] {
  return [...list].sort((a, b) => activityKey(b) - activityKey(a));
}

export async function fetchConversations(
  slug: string,
): Promise<AgentConversation[]> {
  const res = await fetch(
    `/api/chat/agents/${encodeURIComponent(slug)}/conversations`,
  );
  if (!res.ok) throw new Error(`conversations fetch failed: ${res.status}`);
  const data = await res.json();
  return sortConversations(data.conversations ?? []);
}

export async function createConversation(
  slug: string,
  projectId?: string,
): Promise<AgentConversation> {
  const res = await fetch(
    `/api/chat/agents/${encodeURIComponent(slug)}/conversations`,
    {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      // A project-scoped window passes its project so the new session
      // is stored under that project and stays visible in its
      // project-filtered channel list.
      body: JSON.stringify(projectId ? { project_id: projectId } : {}),
    },
  );
  if (!res.ok) throw new Error(`conversation create failed: ${res.status}`);
  const data = await res.json();
  return data.conversation as AgentConversation;
}
