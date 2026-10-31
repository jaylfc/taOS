export interface ReactionBarProps {
  /** Emoji → list of user ids who reacted. */
  reactions: Record<string, string[]>;
  /** The message these reactions belong to. */
  messageId: string;
  /** Current user id for the "mine" highlight. */
  currentUserId: string | null;
  /** Called when a reaction button is clicked. */
  onToggle: (messageId: string, emoji: string) => void;
  /**
   * Mobile bubble variant: a compact chip row overlapping the bottom edge of
   * the bubble it is rendered inside (the parent must be `relative`).
   */
  compact?: boolean;
  /** Which bottom corner the compact row hangs from. Default "right". */
  side?: "left" | "right";
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
  compact = false,
  side = "right",
}: ReactionBarProps) {
  return (
    <div
      className={
        compact
          ? `absolute -bottom-3 z-10 flex gap-1 ${side === "left" ? "left-2" : "right-2"}`
          : "flex flex-wrap gap-1 mt-1.5"
      }
    >
      {Object.entries(reactions).map(([emoji, users]) => {
        const mine = currentUserId != null && users.includes(currentUserId);
        return (
          <button
            key={emoji}
            onClick={() => onToggle(messageId, emoji)}
            aria-pressed={mine}
            className={`text-[12px] rounded-full px-2 py-0.5 flex items-center gap-1 border transition-colors ${
              compact ? "shadow-sm leading-4 " : ""
            }${
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
