import { describe, expect, it } from "vitest";
import { resolveAgentEmoji } from "./agent-emoji";

describe("resolveAgentEmoji", () => {
  it("returns agent emoji when provided", () => {
    expect(resolveAgentEmoji("🚀", "openclaw")).toBe("🚀");
  });

  it("returns framework emoji when agent emoji is empty", () => {
    expect(resolveAgentEmoji("", "smolagents")).toBe("🧪");
  });

  it("returns framework emoji when agent emoji is undefined", () => {
    expect(resolveAgentEmoji(undefined, "langroid")).toBe("🌳");
  });

  it("returns default emoji when framework is unknown", () => {
    expect(resolveAgentEmoji("", "unknown-framework")).toBe("🤖");
  });

  it("returns default emoji when both inputs are null", () => {
    expect(resolveAgentEmoji(null, null)).toBe("🤖");
  });

  it("trims whitespace-only agent emoji", () => {
    expect(resolveAgentEmoji("   ", "openclaw")).toBe("🤖");
  });
});
