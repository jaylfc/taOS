import { describe, it, expect } from "vitest";
import {
  pickWatchAgent,
  computeStallInfo,
  STALL_HINT_MS,
  STALL_WARN_MS,
  type StallWatch,
} from "./MessagesApp.stallWatch";

const AGENTS = ["naira", "atlas"];

describe("pickWatchAgent", () => {
  it("waits on the agent in a DM", () => {
    expect(
      pickWatchAgent({ id: "c1", type: "dm", members: ["user", "naira"] }, "hi", AGENTS),
    ).toBe("naira");
  });

  it("returns null for a human-only DM", () => {
    expect(
      pickWatchAgent({ id: "c1", type: "dm", members: ["user", "bob"] }, "hi", AGENTS),
    ).toBeNull();
  });

  it("waits only on an @mentioned agent in a non-DM channel", () => {
    const chan = { id: "c2", type: "topic", members: ["user", "naira", "atlas"] };
    expect(pickWatchAgent(chan, "hey @atlas can you help", AGENTS)).toBe("atlas");
  });

  it("does not wait in a non-DM channel with no mention", () => {
    const chan = { id: "c2", type: "topic", members: ["user", "naira"] };
    expect(pickWatchAgent(chan, "just chatting", AGENTS)).toBeNull();
  });

  it("ignores a mentioned name that is not a known agent", () => {
    const chan = { id: "c2", type: "topic", members: ["user", "naira"] };
    expect(pickWatchAgent(chan, "@bob hello", AGENTS)).toBeNull();
  });

  it("returns null when there is no channel", () => {
    expect(pickWatchAgent(undefined, "hi", AGENTS)).toBeNull();
  });
});

describe("computeStallInfo", () => {
  const watch: StallWatch = { channelId: "c1", agent: "naira", lastActivityAt: 0 };

  it("stays silent while activity is recent", () => {
    expect(computeStallInfo(watch, "c1", STALL_HINT_MS - 1)).toBeNull();
  });

  it("shows the soft hint after the hint threshold", () => {
    const info = computeStallInfo(watch, "c1", STALL_HINT_MS + 5_000);
    expect(info).toEqual({ agent: "naira", seconds: 25, stalled: false });
  });

  it("escalates to a stalled warning at the warn threshold", () => {
    const info = computeStallInfo(watch, "c1", STALL_WARN_MS);
    expect(info?.stalled).toBe(true);
  });

  it("returns null when the watch is for another channel", () => {
    expect(computeStallInfo(watch, "c2", STALL_WARN_MS)).toBeNull();
  });

  it("returns null when there is no watch", () => {
    expect(computeStallInfo(null, "c1", STALL_WARN_MS)).toBeNull();
  });
});
