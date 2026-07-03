import { describe, it, expect } from "vitest";
import type { Sheet } from "@fortune-sheet/core";
import { blankWorkbook, serializeWorkbook, parseWorkbookContent } from "../workbook";

describe("workbook serialization round trip", () => {
  it("round-trips a multi-sheet workbook with formulas through JSON", () => {
    const sheets: Sheet[] = [
      {
        id: "sheet-1",
        name: "Sheet1",
        order: 0,
        row: 100,
        column: 26,
        celldata: [
          { r: 0, c: 0, v: { v: 1 } },
          { r: 1, c: 0, v: { v: 2 } },
          { r: 2, c: 0, v: { v: 3 } },
          { r: 3, c: 0, v: { v: 6, f: "=SUM(A1:A3)" } },
        ],
      },
      {
        id: "sheet-2",
        name: "Notes",
        order: 1,
        row: 100,
        column: 26,
        celldata: [{ r: 0, c: 0, v: { v: "hello" } }],
      },
    ];

    const content = serializeWorkbook(sheets);
    const restored = parseWorkbookContent(content);

    expect(restored.version).toBe(1);
    expect(restored.sheets).toEqual(sheets);
    expect(restored.sheets[0].celldata?.[3].v?.f).toBe("=SUM(A1:A3)");
  });

  it("falls back to a blank workbook for empty or invalid content", () => {
    expect(parseWorkbookContent("")).toEqual(blankWorkbook());
    expect(parseWorkbookContent("not json")).toEqual(blankWorkbook());
    expect(parseWorkbookContent(JSON.stringify({ version: 1, sheets: [] }))).toEqual(
      blankWorkbook(),
    );
  });

  it("falls back to a blank workbook when a sheet element is corrupted", () => {
    expect(
      parseWorkbookContent(JSON.stringify({ version: 1, sheets: [null] })),
    ).toEqual(blankWorkbook());
    expect(
      parseWorkbookContent(JSON.stringify({ version: 1, sheets: [{ notASheet: true }] })),
    ).toEqual(blankWorkbook());
    expect(
      parseWorkbookContent(
        JSON.stringify({ version: 1, sheets: [{ name: "Sheet1", celldata: "not-an-array" }] }),
      ),
    ).toEqual(blankWorkbook());
    expect(
      parseWorkbookContent(
        JSON.stringify({ version: 1, sheets: [{ name: "Sheet1", row: "100" }] }),
      ),
    ).toEqual(blankWorkbook());
    expect(parseWorkbookContent(JSON.stringify({ version: 1, sheets: "not-an-array" }))).toEqual(
      blankWorkbook(),
    );
  });
});
