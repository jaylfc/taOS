import { describe, expect, it } from "vitest";
import { slugifyClient, slugifyWithFallback, isValidSlug, SLUG_REGEX } from "./slug";

describe("slugifyClient", () => {
  it("lowercases and replaces spaces with hyphens", () => {
    expect(slugifyClient("Hello World")).toBe("hello-world");
  });

  it("replaces special characters with hyphens", () => {
    expect(slugifyClient("foo@bar!baz")).toBe("foo-bar-baz");
  });

  it("collapses multiple non-alphanumeric chars into one hyphen", () => {
    expect(slugifyClient("a   b")).toBe("a-b");
  });

  it("strips leading and trailing hyphens", () => {
    expect(slugifyClient("!!hello!!")).toBe("hello");
  });

  it("truncates to 63 characters", () => {
    const long = "a".repeat(100);
    expect(slugifyClient(long)).toHaveLength(63);
  });

  it("returns empty string for empty input", () => {
    expect(slugifyClient("")).toBe("");
  });

  it("returns empty string for input with only special characters", () => {
    expect(slugifyClient("!!!")).toBe("");
  });

  it("handles mixed case with numbers", () => {
    expect(slugifyClient("My App v2")).toBe("my-app-v2");
  });
});

describe("isValidSlug", () => {
  it("returns true for a valid slug", () => {
    expect(isValidSlug("hello-world")).toBe(true);
  });

  it("returns true for a single lowercase letter", () => {
    expect(isValidSlug("a")).toBe(true);
  });

  it("returns true for max length 63 chars", () => {
    const s = "a" + "b".repeat(62);
    expect(isValidSlug(s)).toBe(true);
  });

  it("returns false for uppercase letters", () => {
    expect(isValidSlug("Hello")).toBe(false);
  });

  it("returns false when starting with a hyphen", () => {
    expect(isValidSlug("-hello")).toBe(false);
  });

  it("returns false for empty string", () => {
    expect(isValidSlug("")).toBe(false);
  });

  it("returns false when exceeding 63 chars", () => {
    const s = "a".repeat(64);
    expect(isValidSlug(s)).toBe(false);
  });

  it("returns false for spaces", () => {
    expect(isValidSlug("hello world")).toBe(false);
  });

  it("returns false for special characters", () => {
    expect(isValidSlug("hello!")).toBe(false);
  });
});

describe("SLUG_REGEX", () => {
  it("is a RegExp", () => {
    expect(SLUG_REGEX).toBeInstanceOf(RegExp);
  });

  it("matches a simple slug", () => {
    expect(SLUG_REGEX.test("my-slug")).toBe(true);
  });

  it("does not match an empty string", () => {
    expect(SLUG_REGEX.test("")).toBe(false);
  });
});

describe("slugifyClient with non-ASCII input", () => {
  it("folds accents onto their base letter instead of dropping them", () => {
    expect(slugifyClient("naïve résumé")).toBe("naive-resume");
    expect(slugifyClient("München")).toBe("munchen");
  });

  it("returns an empty string when no character folds to ASCII", () => {
    // Transliteration is a server-side capability (python-slugify); the client
    // only folds combining marks, so a CJK name has no client-derived slug.
    expect(slugifyClient("我的代理")).toBe("");
  });

  it("does NOT transliterate letters with no NFKD decomposition (known gap)", () => {
    // kilo-code-bot review on #2798: "ß" is not a combining mark, so it
    // survives NFKD unchanged and is then dropped by the [^a-z0-9]+ strip.
    // The server transliterates it (python-slugify: "straße" -> "strasse"),
    // so this is a real, accepted client/server preview mismatch for this
    // narrow set of characters -- pinned here so it stays a documented
    // choice rather than a silent regression.
    expect(slugifyClient("straße")).toBe("stra-e");
  });
});

describe("slugifyWithFallback", () => {
  it("returns the derived slug when there is one", () => {
    expect(slugifyWithFallback("Hello World", "project")).toBe("hello-world");
  });

  it("returns a non-empty valid slug when nothing survives", () => {
    expect(slugifyWithFallback("我的代理", "project")).not.toBe("");
    expect(isValidSlug(slugifyWithFallback("我的代理", "project"))).toBe(true);
  });

  it("gives two different unslugifiable names two different slugs", () => {
    expect(slugifyWithFallback("我的代理", "project")).not.toBe(
      slugifyWithFallback("我的代理人", "project"),
    );
  });

  it("is deterministic for the same name", () => {
    expect(slugifyWithFallback("🚀", "agent")).toBe(slugifyWithFallback("🚀", "agent"));
  });

  it("uses the caller's prefix", () => {
    expect(slugifyWithFallback("🚀", "project").startsWith("project-")).toBe(true);
  });
});
