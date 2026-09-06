import { describe, it, expect, beforeEach } from "vitest";
import { useDockStore } from "../src/stores/dock-store";

beforeEach(() => {
  useDockStore.setState({
    pinned: ["messages", "agents", "files", "store", "settings"],
  });
});

describe("dock store", () => {
  it("returns default pinned apps", () => {
    expect(useDockStore.getState().pinned).toEqual([
      "messages", "agents", "files", "store", "settings",
    ]);
  });

  it("adds a pinned app", () => {
    useDockStore.getState().pin("calculator");
    expect(useDockStore.getState().pinned).toContain("calculator");
  });

  it("does not duplicate a pinned app", () => {
    useDockStore.getState().pin("messages");
    const count = useDockStore.getState().pinned.filter((id) => id === "messages").length;
    expect(count).toBe(1);
  });

  it("removes a pinned app", () => {
    useDockStore.getState().unpin("store");
    expect(useDockStore.getState().pinned).not.toContain("store");
  });

  it("reorders pinned apps", () => {
    useDockStore.getState().reorder(["settings", "agents", "messages", "files", "store"]);
    expect(useDockStore.getState().pinned[0]).toBe("settings");
  });
});
