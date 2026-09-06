import { describe, it, expect } from "vitest";
import { computeNextIndex } from "../src/hooks/use-list-nav";

describe("computeNextIndex", () => {
  it("moves down", () => {
    expect(computeNextIndex(0, 5, "ArrowDown")).toBe(1);
  });
  it("moves up", () => {
    expect(computeNextIndex(1, 5, "ArrowUp")).toBe(0);
  });
  it("wraps down", () => {
    expect(computeNextIndex(4, 5, "ArrowDown")).toBe(0);
  });
  it("wraps up", () => {
    expect(computeNextIndex(0, 5, "ArrowUp")).toBe(4);
  });
  it("Home goes to 0", () => {
    expect(computeNextIndex(3, 5, "Home")).toBe(0);
  });
  it("End goes to last", () => {
    expect(computeNextIndex(1, 5, "End")).toBe(4);
  });
  it("returns current for unknown key", () => {
    expect(computeNextIndex(2, 5, "a")).toBe(2);
  });
  it("returns -1 for empty list", () => {
    expect(computeNextIndex(0, 0, "ArrowDown")).toBe(-1);
  });
});
