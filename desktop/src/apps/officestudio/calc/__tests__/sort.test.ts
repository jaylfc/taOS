import { describe, it, expect } from "vitest";
import { compareCellValues } from "../sort";

describe("compareCellValues", () => {
  it("sorts numbers numerically, not lexically", () => {
    const values = [10, 2, 33, 4];
    const sorted = [...values].sort((a, b) => compareCellValues(a, b, "asc"));
    expect(sorted).toEqual([2, 4, 10, 33]);
  });

  it("sorts numeric strings numerically", () => {
    const values = ["10", "2", "33", "4"];
    const sorted = [...values].sort((a, b) => compareCellValues(a, b, "asc"));
    expect(sorted).toEqual(["2", "4", "10", "33"]);
  });

  it("falls back to locale string compare for mixed number/text values", () => {
    // "5" is numeric, "hi" is not: one side fails to parse as a finite
    // number, so the whole comparison falls back to string compare rather
    // than mixing numeric and lexical ordering.
    expect(compareCellValues("5", "hi", "asc")).toBe("5".localeCompare("hi"));
    expect(compareCellValues("hi", "5", "asc")).toBe("hi".localeCompare("5"));
  });

  it("treats scientific-notation strings as numbers", () => {
    expect(compareCellValues("1e3", "500", "asc")).toBeGreaterThan(0);
    expect(compareCellValues("1e3", "2000", "asc")).toBeLessThan(0);
  });

  it("does not coerce blank cells to zero", () => {
    // Number("") is 0, which would otherwise sort an empty cell among
    // numeric values instead of alongside other text.
    expect(compareCellValues("", 5, "asc")).toBe("".localeCompare("5"));
    expect(compareCellValues("", "", "asc")).toBe(0);
  });

  it("reverses order for descending sorts", () => {
    expect(compareCellValues(1, 2, "desc")).toBeGreaterThan(0);
    expect(compareCellValues("a", "b", "desc")).toBeGreaterThan(0);
  });

  it("returns 0 for equal values so sorts remain stable", () => {
    expect(compareCellValues(5, 5, "asc")).toBe(0);
    expect(compareCellValues("x", "x", "asc")).toBe(0);
  });

  it("keeps a mixed number/text column in a deterministic, repeatable order", () => {
    const values: unknown[] = [5, "hi", 2, "apple", 10, ""];
    const sorted = [...values].sort((a, b) => compareCellValues(a, b, "asc"));
    const resorted = [...values].sort((a, b) => compareCellValues(a, b, "asc"));
    // Sorting the same input twice must give the same result.
    expect(sorted).toEqual(resorted);
    // Numeric values keep their numeric relative order...
    expect(sorted.filter((v) => typeof v === "number")).toEqual([2, 5, 10]);
    // ...and text values (including the blank) keep their locale-compare
    // relative order.
    expect(sorted.filter((v) => typeof v === "string")).toEqual(["", "apple", "hi"]);
  });
});
