import { describe, expect, it } from "vitest";
import { cn } from "./utils";

describe("cn", () => {
  it("merges class strings", () => {
    expect(cn("foo", "bar")).toBe("foo bar");
  });

  it("handles conditional classes", () => {
    expect(cn("foo", false && "bar", "baz")).toBe("foo baz");
  });

  it("merges tailwind classes without conflict", () => {
    expect(cn("p-4", "m-4")).toBe("p-4 m-4");
  });

  it("resolves tailwind conflicts with twMerge", () => {
    expect(cn("p-4", "p-6")).toBe("p-6");
  });

  it("returns empty string for no inputs", () => {
    expect(cn()).toBe("");
  });

  it("handles empty string inputs", () => {
    expect(cn("", "foo", "")).toBe("foo");
  });

  it("handles arrays of classes", () => {
    expect(cn(["foo", "bar"])).toBe("foo bar");
  });

  it("handles mixed types (string, array, object)", () => {
    expect(cn("foo", { bar: true, baz: false }, ["qux"])).toBe("foo bar qux");
  });

  it("resolves conflicting tailwind utilities in arrays", () => {
    expect(cn(["px-2", "py-1"], "px-4")).toBe("py-1 px-4");
  });
});
