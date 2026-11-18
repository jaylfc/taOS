import { describe, it, expect } from "vitest";
import { buildSoulMd, parseAbout, suggestedPrompts } from "./about";

describe("about you", () => {
  it("builds the persona from the name and answers", () => {
    const soul = buildSoulMd("Ada", { help: ["research", "coding"], tone: "brief", autonomy: "act" });
    expect(soul).toContain("You are Ada's personal assistant");
    expect(soul).toContain("research, coding");
    expect(soul).toContain("Keep answers short");
    expect(soul).toContain("without asking");
  });

  it("leaves out the topics line when none were picked", () => {
    expect(buildSoulMd("", { help: [], tone: "warm", autonomy: "ask" })).not.toContain("mostly want");
  });

  it("offers three prompts, picked topics first, never repeated", () => {
    expect(suggestedPrompts({ help: ["coding"], tone: "warm", autonomy: "ask" })).toEqual([
      "Explain what a piece of code does.",
      "Help me plan my week.",
      "Find the latest news on a topic I care about.",
    ]);
    const p = suggestedPrompts({ help: ["planning", "research", "learning"], tone: "warm", autonomy: "ask" });
    expect(new Set(p).size).toBe(3);
  });

  it("drops stored values this build does not know", () => {
    expect(parseAbout({ help: ["coding", "bogus"], tone: "shouty", autonomy: 7 })).toEqual({
      help: ["coding"],
      tone: "warm",
      autonomy: "ask",
    });
    expect(parseAbout(null)).toBeUndefined();
  });
});
