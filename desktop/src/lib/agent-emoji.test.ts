import { describe, expect, it } from "vitest";
import { resolveAgentEmoji } from "./agent-emoji";

describe("resolveAgentEmoji", () => {
  it("returns the agent emoji when provided", () => {
    expect(resolveAgentEmoji("🦊", "openclaw")).toBe("🦊");
  });

  it("returns the agent emoji even when framework is null", () => {
    expect(resolveAgentEmoji("🔥", null)).toBe("🔥");
  });

  it("trims whitespace from agent emoji before returning", () => {
    expect(resolveAgentEmoji(" 🦊 ", "openclaw")).toBe("🦊");
  });

  it("falls back to the framework emoji for openclaw", () => {
    expect(resolveAgentEmoji(undefined, "openclaw")).toBe("\u{1F916}");
  });

  it("falls back to the framework emoji for smolagents", () => {
    expect(resolveAgentEmoji(undefined, "smolagents")).toBe("\u{1F9EA}");
  });

  it("falls back to the framework emoji for pocketflow", () => {
    expect(resolveAgentEmoji(undefined, "pocketflow")).toBe("\u{1F517}");
  });

  it("falls back to the framework emoji for shibaclaw", () => {
    expect(resolveAgentEmoji(undefined, "shibaclaw")).toBe("\u{1F436}");
  });

  it("falls back to the framework emoji for zeroclaw", () => {
    expect(resolveAgentEmoji(undefined, "zeroclaw")).toBe("\u{1F300}");
  });

  it("returns the default emoji for an unrecognised framework", () => {
    expect(resolveAgentEmoji(undefined, "unknown-framework")).toBe("\u{1F916}");
  });

  it("returns the default emoji when both args are undefined", () => {
    expect(resolveAgentEmoji(undefined, undefined)).toBe("\u{1F916}");
  });

  it("returns the default emoji when both args are null", () => {
    expect(resolveAgentEmoji(null, null)).toBe("\u{1F916}");
  });

  it("returns the default emoji for empty string agent emoji and no framework", () => {
    expect(resolveAgentEmoji("", undefined)).toBe("\u{1F916}");
  });

  it("returns the default emoji for whitespace-only agent emoji and no framework", () => {
    expect(resolveAgentEmoji("   ", null)).toBe("\u{1F916}");
  });
});
