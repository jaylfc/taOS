import { describe, it, expect } from "vitest";
import { getFocusableElements } from "../src/hooks/use-focus-trap";

describe("getFocusableElements", () => {
  it("returns empty array for null", () => {
    expect(getFocusableElements(null)).toEqual([]);
  });
});
