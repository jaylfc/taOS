import { describe, expect, it, beforeEach, vi } from "vitest";
import {
  findA2aChannelId,
  projectChannelStorageKey,
  readLastChannel,
  writeLastChannel,
} from "../src/apps/MessagesApp.a2aSelection";

describe("a2a selection storage", () => {
  beforeEach(() => {
    window.localStorage.clear();
  });

  it("storage key is project-scoped", () => {
    expect(projectChannelStorageKey("p1")).toBe("taos.projects.p1.lastChannel");
  });

  it("readLastChannel returns null when unset", () => {
    expect(readLastChannel("p1")).toBeNull();
  });

  it("writeLastChannel persists, readLastChannel returns it", () => {
    writeLastChannel("p1", "ch1");
    expect(readLastChannel("p1")).toBe("ch1");
  });

  it("read swallows localStorage errors", () => {
    const spy = vi
      .spyOn(window.localStorage, "getItem")
      .mockImplementation(() => {
        throw new Error("denied");
      });
    expect(readLastChannel("p1")).toBeNull();
    spy.mockRestore();
  });

  it("findA2aChannelId picks the channel with settings.kind === 'a2a'", () => {
    const channels = [
      { id: "ch1", settings: { kind: undefined } },
      { id: "ch2", settings: { kind: "a2a" } },
      { id: "ch3", settings: { kind: "topic" } },
    ];
    expect(findA2aChannelId(channels)).toBe("ch2");
  });

  it("findA2aChannelId returns null when no A2A channel present", () => {
    expect(findA2aChannelId([{ id: "ch1", settings: {} }])).toBeNull();
  });
});
