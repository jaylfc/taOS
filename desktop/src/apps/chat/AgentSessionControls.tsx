import { Plus } from "lucide-react";
import type { AgentConversation } from "./agent-sessions";
import { toMs } from "./time";

/**
 * Conversation-session controls for an agent DM (#2123): a selector
 * listing the user's previous conversations with the agent (most
 * recent first, titled from the first user message, with a timestamp),
 * the model the current session is bound to, and a New chat button
 * that starts a fresh conversation without discarding the old one.
 */
export function AgentSessionControls({
  slug,
  conversations,
  currentChannelId,
  onSelect,
  onNewChat,
  creating = false,
}: {
  slug: string;
  conversations: AgentConversation[];
  currentChannelId: string;
  onSelect: (channelId: string) => void;
  onNewChat: () => void;
  creating?: boolean;
}) {
  const current = conversations.find((c) => c.id === currentChannelId);
  // The open conversation may predate the fetched list (or the list
  // may still be loading); keep the selector anchored on it regardless.
  const options: AgentConversation[] = current
    ? conversations
    : [
        {
          id: currentChannelId,
          agent: slug,
          title: "Current conversation",
          created_at: null,
          last_message_at: null,
        },
        ...conversations,
      ];

  const stamp = (c: AgentConversation): string => {
    const ts = c.last_message_at ?? c.created_at;
    if (ts == null) return "";
    const ms = toMs(ts);
    if (Number.isNaN(ms)) return "";
    return new Date(ms).toLocaleString();
  };

  return (
    <div
      className="shrink-0 flex items-center gap-2 px-3 py-1.5 border-b border-shell-border bg-shell-surface/60"
      data-testid="agent-session-controls"
    >
      <select
        aria-label={`Conversations with ${slug}`}
        className="min-w-0 flex-1 sm:flex-none bg-shell-surface border border-shell-border rounded-md text-xs px-2 py-1 text-shell-text"
        value={currentChannelId}
        onChange={(e) => onSelect(e.target.value)}
      >
        {options.map((c) => {
          const when = stamp(c);
          return (
            <option key={c.id} value={c.id}>
              {when ? `${c.title} — ${when}` : c.title}
            </option>
          );
        })}
      </select>
      {current?.model && (
        <span
          className="hidden sm:inline text-[11px] text-shell-text-tertiary truncate"
          title={`Model this conversation is bound to: ${current.model}`}
        >
          Model: {current.model}
        </span>
      )}
      <button
        type="button"
        onClick={onNewChat}
        disabled={creating}
        className="ml-auto sm:ml-0 shrink-0 inline-flex items-center gap-1 rounded-md border border-shell-border bg-shell-surface px-2 py-1 text-xs text-shell-text hover:bg-shell-surface-hover disabled:opacity-50"
      >
        <Plus size={13} aria-hidden="true" />
        New chat
      </button>
    </div>
  );
}
