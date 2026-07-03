import { describe, it, expect } from "vitest";
import type { CellWithRowAndCol } from "@fortune-sheet/core";
import { sheetToCsv, parseCsv } from "../csv";

describe("sheetToCsv", () => {
  it("produces expected CSV text for a small sheet, with a trailing CRLF", () => {
    const celldata: CellWithRowAndCol[] = [
      { r: 0, c: 0, v: { v: "Month" } },
      { r: 0, c: 1, v: { v: "Revenue" } },
      { r: 1, c: 0, v: { v: "January" } },
      { r: 1, c: 1, v: { v: 4200 } },
      { r: 2, c: 0, v: { v: "February" } },
      { r: 2, c: 1, v: { v: 5100 } },
    ];
    expect(sheetToCsv(celldata)).toBe(
      "Month,Revenue\r\nJanuary,4200\r\nFebruary,5100\r\n",
    );
  });

  it("quotes fields containing commas or quotes", () => {
    const celldata: CellWithRowAndCol[] = [
      { r: 0, c: 0, v: { v: "Smith, Jane" } },
      { r: 0, c: 1, v: { v: 'She said "hi"' } },
    ];
    expect(sheetToCsv(celldata)).toBe('"Smith, Jane","She said ""hi"""\r\n');
  });

  it("prefers the formatted display value over the raw value", () => {
    const celldata: CellWithRowAndCol[] = [{ r: 0, c: 0, v: { v: 3, m: "$3.00" } }];
    expect(sheetToCsv(celldata)).toBe("$3.00\r\n");
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

  it("handles a quoted field containing an embedded newline", () => {
    expect(parseCsv('a,"line one\nline two",c\n1,2,3')).toEqual([
      ["a", "line one\nline two", "c"],
      ["1", "2", "3"],
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

  it("round-trips through sheetToCsv for values with commas and quotes", () => {
    const celldata: CellWithRowAndCol[] = [
      { r: 0, c: 0, v: { v: "Smith, Jane" } },
      { r: 0, c: 1, v: { v: 'She said "hi"' } },
      { r: 1, c: 0, v: { v: "Doe, John" } },
      { r: 1, c: 1, v: { v: "plain text" } },
    ];
    const csv = sheetToCsv(celldata);
    expect(parseCsv(csv)).toEqual([
      ["Smith, Jane", 'She said "hi"'],
      ["Doe, John", "plain text"],
    ]);
  });

  it("throws a descriptive error for an unterminated quoted field instead of swallowing the rest of the file", () => {
    expect(() => parseCsv('a,"unterminated\nb,c')).toThrow(/unterminated/i);
  });
});
