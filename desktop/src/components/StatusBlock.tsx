import { cn } from "@/lib/utils";
import type { StatusContentBlock, QuestionContentBlock } from "@/apps/MessagesApp";

export type StatusLikeBlock = StatusContentBlock | QuestionContentBlock;

/**
 * Single muted line for a `{kind: "status"}` content block.
 *
 * The `{kind: "question"}` variant is rendered through here too: it gains an
 * accent left border and a "reply below" hint, staying passive (the operator
 * answers with an ordinary chat message, no Decisions dependency).
 */
export function StatusBlock({ block }: { block: StatusLikeBlock }) {
  const isQuestion = block.kind === "question";

  return (
    <div
      data-status-block="true"
      data-variant={isQuestion ? "question" : "status"}
      className={cn(
        "text-[13px] text-shell-text-tertiary py-1.5",
        isQuestion && "border-l-2 border-accent-line pl-2",
      )}
    >
      <span className="block">{block.text}</span>
      {isQuestion && (
        <span className="block text-[11px] text-shell-text-tertiary mt-0.5">
          reply below
        </span>
      )}
    </div>
  );
}

export default StatusBlock;
