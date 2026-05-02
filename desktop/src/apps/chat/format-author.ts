export interface AuthorContext {
  currentUserId: string | null;
  currentUserDisplayName: string | null;
}

export function displayAuthor(
  msg: { author_id: string; author_type?: "user" | "agent" | "system" },
  ctx: AuthorContext,
): string {
  if (msg.author_type === "system") return "system";
  if (msg.author_type === "user") {
    if (msg.author_id === ctx.currentUserId && ctx.currentUserDisplayName) {
      return ctx.currentUserDisplayName;
    }
    return msg.author_id; // TODO: multi-user lookup table
  }
  return msg.author_id; // agent slug
}
