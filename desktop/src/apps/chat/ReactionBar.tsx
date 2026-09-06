export interface ReactionBarProps {
  /** Emoji → list of user ids who reacted. */
  reactions: Record<string, string[]>;
  /** The message these reactions belong to. */
  messageId: string;
  /** Current user id for the "mine" highlight. */
  currentUserId: string | null;
  /** Called when a reaction button is clicked. */
  onToggle: (messageId: string, emoji: string) => void;
}

/**
 * Renders the reaction buttons row for a single message.
 * Pure presentational — no picker, no portal.
 */
export function ReactionBar({
  reactions,
  messageId,
  currentUserId,
  onToggle,
}: ReactionBarProps) {
  return (
    <div className="flex flex-wrap gap-1 mt-1.5">
      {Object.entries(reactions).map(([emoji, users]) => {
        const mine = currentUserId != null && users.includes(currentUserId);
        return (
          <button
            key={emoji}
            onClick={() => onToggle(messageId, emoji)}
            aria-pressed={mine}
            className={`text-[12px] rounded-full px-2 py-0.5 flex items-center gap-1 border transition-colors ${
              mine
                ? "bg-accent-soft border-accent-line text-accent-strong"
                : "bg-shell-surface border-shell-border hover:bg-shell-surface-hover text-shell-text-secondary"
            }`}
          >
            <span>{emoji}</span>
            <span
              className={
                mine ? "text-accent-strong font-medium" : "text-shell-text-tertiary"
              }
            >
              {users.length}
            </span>
          </button>
        );
      })}
    </div>
  );
}
