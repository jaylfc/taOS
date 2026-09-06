import { describe, it, expect } from "vitest";
import { displayAuthor, type AuthorContext } from "../format-author";

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
