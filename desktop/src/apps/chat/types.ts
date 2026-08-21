/** Shared channel/message types used by MessagesApp sub-components. */

export interface Channel {
  id: string;
  name: string;
  type: "dm" | "topic" | "group";
  description?: string;
  topic?: string;
  members?: string[];
  created_at?: string;
  last_message_at?: string;
  lastPreview?: string;
  project_id?: string;
  settings?: {
    archived?: boolean;
    archived_at?: string;
    archived_agent_id?: string;
    archived_agent_slug?: string;
    muted?: string[];
    kind?: string;
  };
}

export interface LiveAgent {
  name: string;
  display_name?: string;
  emoji?: string;
  framework?: string;
  model?: string;
  status?: string;
}

export interface ArchivedAgentEntry {
  id: string;
  archived_slug: string;
  original?: {
    name?: string;
    display_name?: string;
  };
}

export interface ProjectGroup {
  id: string;
  name: string;
  channels: Channel[];
}

/**
 * Sidebar presence state for an agent DM channel, derived from the agent's
 * registry status plus real-time "thinking" events.
 * - "working" -- agent is actively generating (thinking / running a tool).
 * - "live"    -- agent is registered as running and available.
 * - "idle"    -- agent is paused, stopped, failed, or otherwise unavailable.
 */
export type AgentPresence = "live" | "working" | "idle";

export type WsStatus = "connecting" | "connected" | "disconnected";
