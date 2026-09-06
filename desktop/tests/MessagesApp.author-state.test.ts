import { describe, it, expect } from "vitest";
import { resolveAuthorDisplayState } from "../src/apps/MessagesApp";

const LIVE_AGENTS = [
  { name: "alpha-agent", display_name: "Alpha" },
  { name: "beta-agent" },
];

const ARCHIVED_AGENTS = [
  {
    id: "arch-1",
    archived_slug: "gamma-agent-20240101T120000",
    original: { name: "gamma-agent", display_name: "Gamma" },
  },
  {
    id: "arch-2",
    archived_slug: "delta-agent",
    original: { name: "delta-agent" },
  },
];

describe("resolveAuthorDisplayState", () => {
  it("returns active for user messages regardless of agent lists", () => {
    expect(resolveAuthorDisplayState("anyone", "user", [], [])).toBe("active");
    expect(resolveAuthorDisplayState("anyone", "user", LIVE_AGENTS, ARCHIVED_AGENTS)).toBe("active");
  });

  it("returns active for agent messages where author_id matches a live agent name", () => {
    expect(resolveAuthorDisplayState("alpha-agent", "agent", LIVE_AGENTS, ARCHIVED_AGENTS)).toBe("active");
    expect(resolveAuthorDisplayState("beta-agent", "agent", LIVE_AGENTS, ARCHIVED_AGENTS)).toBe("active");
  });

  it("returns archived for agent messages matched by archived_slug", () => {
    expect(
      resolveAuthorDisplayState("gamma-agent-20240101T120000", "agent", LIVE_AGENTS, ARCHIVED_AGENTS),
    ).toBe("archived");
  });

  it("returns archived for agent messages matched by original.name", () => {
    expect(resolveAuthorDisplayState("gamma-agent", "agent", LIVE_AGENTS, ARCHIVED_AGENTS)).toBe("archived");
    expect(resolveAuthorDisplayState("delta-agent", "agent", LIVE_AGENTS, ARCHIVED_AGENTS)).toBe("archived");
  });

  it("returns removed for agent messages with unknown author_id", () => {
    expect(resolveAuthorDisplayState("phantom-agent", "agent", LIVE_AGENTS, ARCHIVED_AGENTS)).toBe("removed");
  });

  it("returns removed when both live and archived lists are empty", () => {
    expect(resolveAuthorDisplayState("some-agent", "agent", [], [])).toBe("removed");
  });

  it("returns active when a live agent matches and archived list also has it", () => {
    // live takes priority — name in both shouldn't happen in practice but guard it
    const liveAndArchived = [{ name: "gamma-agent" }];
    expect(resolveAuthorDisplayState("gamma-agent", "agent", liveAndArchived, ARCHIVED_AGENTS)).toBe("active");
  });
});
