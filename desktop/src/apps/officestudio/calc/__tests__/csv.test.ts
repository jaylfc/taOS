import { describe, it, expect } from "vitest";
import type { CellWithRowAndCol } from "@fortune-sheet/core";
import { sheetToCsv, parseCsv } from "../csv";

describe("sheetToCsv", () => {
  it("produces expected CSV text for a small sheet", () => {
    const celldata: CellWithRowAndCol[] = [
      { r: 0, c: 0, v: { v: "Month" } },
      { r: 0, c: 1, v: { v: "Revenue" } },
      { r: 1, c: 0, v: { v: "January" } },
      { r: 1, c: 1, v: { v: 4200 } },
      { r: 2, c: 0, v: { v: "February" } },
      { r: 2, c: 1, v: { v: 5100 } },
    ];
    expect(sheetToCsv(celldata)).toBe(
      "Month,Revenue\r\nJanuary,4200\r\nFebruary,5100",
    );
  });

  it("quotes fields containing commas or quotes", () => {
    const celldata: CellWithRowAndCol[] = [
      { r: 0, c: 0, v: { v: "Smith, Jane" } },
      { r: 0, c: 1, v: { v: 'She said "hi"' } },
    ];
    expect(sheetToCsv(celldata)).toBe('"Smith, Jane","She said ""hi"""');
  });

  it("prefers the formatted display value over the raw value", () => {
    const celldata: CellWithRowAndCol[] = [{ r: 0, c: 0, v: { v: 3, m: "$3.00" } }];
    expect(sheetToCsv(celldata)).toBe("$3.00");
  });

  it("returns an empty string for an empty sheet", () => {
    expect(sheetToCsv([])).toBe("");
  });
});

describe("parseCsv", () => {
  it("parses simple rows", () => {
    expect(parseCsv("a,b,c\n1,2,3")).toEqual([
      ["a", "b", "c"],
      ["1", "2", "3"],
    ]);
  });

  it("handles quoted fields with embedded commas and escaped quotes", () => {
    expect(parseCsv('"Smith, Jane","She said ""hi"""')).toEqual([
      ["Smith, Jane", 'She said "hi"'],
    ]);
  });

  it("handles CRLF line endings and a trailing newline", () => {
    expect(parseCsv("a,b\r\n1,2\r\n")).toEqual([
      ["a", "b"],
      ["1", "2"],
    ]);
  });

  it("returns an empty array for empty input", () => {
    expect(parseCsv("")).toEqual([]);
  });
});
