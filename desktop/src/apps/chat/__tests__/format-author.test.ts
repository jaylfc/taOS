import { describe, it, expect } from "vitest";
import { displayAuthor, isOwnMessage, type AuthorContext } from "../format-author";

const ctx: AuthorContext = {
  currentUserId: "t4QH4PpyCmY",
  currentUserDisplayName: "Jay",
};

describe("displayAuthor", () => {
  it("returns 'system' for system messages", () => {
    expect(displayAuthor({ author_id: "t4QH4PpyCmY", author_type: "system" }, ctx)).toBe("system");
  });

  it("returns display name for current user messages", () => {
    expect(displayAuthor({ author_id: "t4QH4PpyCmY", author_type: "user" }, ctx)).toBe("Jay");
  });

  it("returns raw author_id for other user messages", () => {
    expect(displayAuthor({ author_id: "anotherHexId", author_type: "user" }, ctx)).toBe("anotherHexId");
  });

  it("returns agent slug for agent messages", () => {
    expect(displayAuthor({ author_id: "tom", author_type: "agent" }, ctx)).toBe("tom");
  });

  it("falls back to author_id when currentUserId is null", () => {
    const noCtx: AuthorContext = { currentUserId: null, currentUserDisplayName: null };
    expect(displayAuthor({ author_id: "t4QH4PpyCmY", author_type: "user" }, noCtx)).toBe("t4QH4PpyCmY");
  });
});

describe("own-message identity (web client posts author_id 'user')", () => {
  const real: AuthorContext = { currentUserId: "u-123", currentUserDisplayName: null };
  it("isOwnMessage is true for the literal 'user' author id", () => {
    expect(isOwnMessage({ author_id: "user", author_type: "user" }, "u-123")).toBe(true);
    expect(isOwnMessage({ author_id: "u-123", author_type: "user" }, "u-123")).toBe(true);
    expect(isOwnMessage({ author_id: "other", author_type: "user" }, "u-123")).toBe(false);
    expect(isOwnMessage({ author_id: "user", author_type: "agent" }, "u-123")).toBe(false);
  });
  it("displayAuthor returns 'You' for own message without a display name", () => {
    expect(displayAuthor({ author_id: "user", author_type: "user" }, real)).toBe("You");
  });
  it("displayAuthor prefers the display name for the literal 'user' id", () => {
    expect(displayAuthor({ author_id: "user", author_type: "user" }, { ...real, currentUserDisplayName: "Jay" })).toBe("Jay");
  });
});
