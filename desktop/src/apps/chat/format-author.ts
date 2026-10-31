export interface AuthorContext {
  currentUserId: string | null;
  currentUserDisplayName: string | null;
}

type AuthorLike = { author_id: string; author_type?: "user" | "agent" | "system" };

// The single-user web client posts its own messages with the literal author_id "user".
// Stopgap until the backend stamps real author ids (card tsk-oxv4sh); then drop the "user" branch.
export function isOwnMessage(msg: AuthorLike, currentUserId: string | null | undefined): boolean {
  return (
    msg.author_type === "user" &&
    (msg.author_id === "user" || (currentUserId != null && msg.author_id === currentUserId))
  );
}

export function displayAuthor(
  msg: { author_id: string; author_type?: "user" | "agent" | "system" },
  ctx: AuthorContext,
): string {
  if (msg.author_type === "system") return "system";
  if (msg.author_type === "user") {
    if (isOwnMessage(msg, ctx.currentUserId)) {
      return ctx.currentUserDisplayName || "You";
    }
    return msg.author_id; // TODO: multi-user lookup table
  }
  return msg.author_id; // agent slug
}
