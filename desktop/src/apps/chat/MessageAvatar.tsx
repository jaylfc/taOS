import { resolveAgentEmoji } from "@/lib/agent-emoji";

/**
 * Slack-style avatar tile shown in the gutter on the first message of a group.
 *
 * - Agent (active): the agent emoji on a tile tinted with a stable per-agent
 *   colour derived from its slug (LiveAgent has no explicit colour field).
 * - Agent (dead / removed): a muted neutral tile.
 * - User: 1-2 character initials on a neutral surface tile.
 *
 * Structural colour uses shell tokens so it adapts to the active theme.
 */

/** Stable hue (0-359) hashed from an arbitrary string. */
function hueFromString(input: string): number {
  let hash = 0;
  for (let i = 0; i < input.length; i++) {
    hash = (hash * 31 + input.charCodeAt(i)) | 0;
  }
  return Math.abs(hash) % 360;
}

/** A low-alpha tint for an agent tile background, stable per slug. */
function agentTileBg(slug: string): string {
  return `hsl(${hueFromString(slug)} 58% 58% / 0.15)`;
}

/** Up to two initials from a display name or id. */
function initialsFor(name: string): string {
  const parts = name.trim().split(/[\s._-]+/).filter(Boolean);
  if (parts.length === 0) return "?";
  if (parts.length === 1) return (parts[0] ?? "").slice(0, 2).toUpperCase() || "?";
  return ((parts[0]?.[0] ?? "") + (parts[1]?.[0] ?? "")).toUpperCase() || "?";
}

export interface MessageAvatarProps {
  /** Avatar size in px (desktop ~38, mobile ~34). */
  size: number;
  /** Agent slug or user display name driving the colour / initials. */
  authorId: string;
  /** Display name used for user initials. */
  displayName: string;
  kind: "agent" | "user";
  /** Dead / archived / removed agents render a muted neutral tile. */
  dead?: boolean;
  /** Resolved agent emoji (already passed through resolveAgentEmoji upstream). */
  emoji?: string;
}

export function MessageAvatar({
  size,
  authorId,
  displayName,
  kind,
  dead = false,
  emoji,
}: MessageAvatarProps) {
  const dim = { width: size, height: size };
  const fontPx = Math.round(size * 0.46);

  if (kind === "agent") {
    if (dead) {
      return (
        <div
          aria-hidden="true"
          style={dim}
          className="rounded-[11px] flex items-center justify-center bg-shell-surface border border-shell-border text-shell-text-tertiary"
        >
          <span style={{ fontSize: fontPx, lineHeight: 1, opacity: 0.6 }}>
            {emoji ?? resolveAgentEmoji(undefined, undefined)}
          </span>
        </div>
      );
    }
    return (
      <div
        aria-hidden="true"
        style={{ ...dim, backgroundColor: agentTileBg(authorId) }}
        className="rounded-[11px] flex items-center justify-center border border-shell-border"
      >
        <span style={{ fontSize: fontPx, lineHeight: 1 }}>
          {emoji ?? resolveAgentEmoji(undefined, undefined)}
        </span>
      </div>
    );
  }

  return (
    <div
      aria-hidden="true"
      style={dim}
      className="rounded-[11px] flex items-center justify-center bg-shell-surface-active border border-shell-border text-shell-text font-semibold"
    >
      <span style={{ fontSize: Math.round(size * 0.38), lineHeight: 1 }}>
        {initialsFor(displayName || authorId)}
      </span>
    </div>
  );
}
