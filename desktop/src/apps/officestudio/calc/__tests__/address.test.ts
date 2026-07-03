import { describe, it, expect } from "vitest";
import { colToLetters, cellAddress } from "../address";

describe("colToLetters", () => {
  it("converts single-letter columns", () => {
    expect(colToLetters(0)).toBe("A");
    expect(colToLetters(1)).toBe("B");
    expect(colToLetters(25)).toBe("Z");
  });

  it("converts multi-letter columns", () => {
    expect(colToLetters(26)).toBe("AA");
    expect(colToLetters(27)).toBe("AB");
    expect(colToLetters(51)).toBe("AZ");
  });
});

describe("cellAddress", () => {
  it("combines column letters with a 1-indexed row number", () => {
    expect(cellAddress(0, 0)).toBe("A1");
    expect(cellAddress(6, 1)).toBe("B7");
    expect(cellAddress(9, 26)).toBe("AA10");
  });
});
