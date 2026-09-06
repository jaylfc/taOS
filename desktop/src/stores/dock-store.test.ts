import { beforeEach, describe, expect, it } from "vitest";
import { useDockStore } from "./dock-store";

const DEFAULT_PINNED = ["messages", "agents", "files", "store", "settings"];

beforeEach(() => {
  useDockStore.setState({ pinned: [...DEFAULT_PINNED], iconSize: "medium", position: "bottom" });
});

describe("dock-store — defaults", () => {
  it("starts with the default pinned list", () => {
    expect(useDockStore.getState().pinned).toEqual(DEFAULT_PINNED);
  });

  it("starts with medium icon size and bottom position", () => {
    expect(useDockStore.getState().iconSize).toBe("medium");
    expect(useDockStore.getState().position).toBe("bottom");
  });
});

describe("dock-store — setIconSize / setPosition", () => {
  it("updates the icon size", () => {
    useDockStore.getState().setIconSize("large");
    expect(useDockStore.getState().iconSize).toBe("large");
  });

  it("updates the position", () => {
    useDockStore.getState().setPosition("left");
    expect(useDockStore.getState().position).toBe("left");
  });
});

describe("dock-store — pin", () => {
  it("appends a new app id to the pinned list", () => {
    useDockStore.getState().pin("terminal");
    const pinned = useDockStore.getState().pinned;
    expect(pinned).toContain("terminal");
    expect(pinned).toEqual([...DEFAULT_PINNED, "terminal"]);
  });

  it("does not duplicate an already-pinned app id", () => {
    useDockStore.getState().pin("messages");
    const pinned = useDockStore.getState().pinned;
    expect(pinned).toEqual(DEFAULT_PINNED);
    expect(pinned.filter((id) => id === "messages")).toHaveLength(1);
  });
});

describe("dock-store — unpin", () => {
  it("removes an app id from the pinned list", () => {
    useDockStore.getState().unpin("agents");
    const pinned = useDockStore.getState().pinned;
    expect(pinned).not.toContain("agents");
    expect(pinned).toEqual(["messages", "files", "store", "settings"]);
  });

  it("does nothing when unpinning an id that is not pinned", () => {
    useDockStore.getState().unpin("nonexistent");
    expect(useDockStore.getState().pinned).toEqual(DEFAULT_PINNED);
  });
});

describe("dock-store — reorder", () => {
  it("replaces the pinned list with the provided order", () => {
    const reordered = ["settings", "store", "files", "agents", "messages"];
    useDockStore.getState().reorder(reordered);
    expect(useDockStore.getState().pinned).toEqual(reordered);
  });

  it("accepts an empty list", () => {
    useDockStore.getState().reorder([]);
    expect(useDockStore.getState().pinned).toEqual([]);
  });
});

describe("dock-store — reset to defaults", () => {
  it("restores the default pinned list after mutations", () => {
    const store = useDockStore.getState();
    store.pin("terminal");
    store.unpin("messages");
    store.reorder(["agents", "files"]);

    useDockStore.setState({ pinned: [...DEFAULT_PINNED] });
    expect(useDockStore.getState().pinned).toEqual(DEFAULT_PINNED);
  });
});
