import type { ProjectMember } from "@/lib/projects";

export type PresenceFace = {
  id: string;
  initial: string;
  kind: "human" | "agent";
  title: string;
};

/**
 * Derives the project-header presence row from existing data (task #59 phase 1).
 *
 * This is static-but-real: it uses the project owner + the real member roster
 * (native/clone members are agents) rather than live multiplayer presence.
 * Live cursors / true real-time presence are #59 phase 2 (deferred).
 */
export function derivePresence(opts: {
  ownerInitial?: string;
  members: ProjectMember[];
  agentName: (memberId: string) => string;
  max?: number;
}): PresenceFace[] {
  const { ownerInitial, members, agentName, max = 5 } = opts;
  const faces: PresenceFace[] = [];

  if (ownerInitial) {
    faces.push({
      id: "owner",
      initial: ownerInitial.slice(0, 1).toUpperCase(),
      kind: "human",
      title: "You",
    });
  }

  for (const m of members) {
    const name = agentName(m.member_id);
    faces.push({
      id: m.member_id,
      initial: (name || "?").slice(0, 1).toUpperCase(),
      kind: m.member_kind === "native" || m.member_kind === "clone" ? "agent" : "human",
      title: name,
    });
  }

  return faces.slice(0, max);
}
