import { describe, it, expect } from "vitest";
import {
  addColumn,
  addRow,
  blankTable,
  changeType,
  coerceValue,
  parseTableContent,
  removeColumn,
  removeRow,
  renameColumn,
  serializeTable,
  setCell,
  type DbTable,
} from "../table";

describe("blankTable", () => {
  it("starts with one text column and one row", () => {
    const table = blankTable();
    expect(table.columns).toHaveLength(1);
    expect(table.columns[0].name).toBe("Name");
    expect(table.columns[0].type).toBe("text");
    expect(table.rows).toHaveLength(1);
  });
});

describe("addColumn / removeColumn", () => {
  it("adds a column and seeds the default value onto every existing row", () => {
    let table = blankTable();
    table = addRow(table);
    table = addColumn(table, "number");
    expect(table.columns).toHaveLength(2);
    const newCol = table.columns[1];
    expect(newCol.type).toBe("number");
    for (const row of table.rows) {
      expect(row.cells[newCol.id]).toBeNull();
    }
  });

  it("refuses to remove the last remaining column", () => {
    const table = blankTable();
    const next = removeColumn(table, table.columns[0].id);
    expect(next).toBe(table);
  });

  it("removes a column and its cells from every row", () => {
    let table = blankTable();
    table = addColumn(table, "text");
    const toRemove = table.columns[1].id;
    table = removeColumn(table, toRemove);
    expect(table.columns).toHaveLength(1);
    for (const row of table.rows) {
      expect(toRemove in row.cells).toBe(false);
    }
  });
});

describe("renameColumn", () => {
  it("renames a column", () => {
    const table = blankTable();
    const renamed = renameColumn(table, table.columns[0].id, "Title");
    expect(renamed.columns[0].name).toBe("Title");
  });
});

describe("coerceValue", () => {
  it("coerces into number: parses strings, maps booleans, non-numeric -> null", () => {
    expect(coerceValue("42", "number")).toBe(42);
    expect(coerceValue("3.5", "number")).toBe(3.5);
    expect(coerceValue("abc", "number")).toBeNull();
    expect(coerceValue("", "number")).toBeNull();
    expect(coerceValue(null, "number")).toBeNull();
    expect(coerceValue(true, "number")).toBe(1);
    expect(coerceValue(false, "number")).toBe(0);
  });

  it("coerces into date: normalizes valid dates to YYYY-MM-DD, invalid -> null", () => {
    expect(coerceValue("2024-01-15", "date")).toBe("2024-01-15");
    expect(coerceValue("not a date", "date")).toBeNull();
    expect(coerceValue("", "date")).toBeNull();
    expect(coerceValue(null, "date")).toBeNull();
    expect(coerceValue(true, "date")).toBeNull();
    expect(coerceValue(42, "date")).toBeNull();
  });

  it("coerces into checkbox via truthiness", () => {
    expect(coerceValue("hello", "checkbox")).toBe(true);
    expect(coerceValue("", "checkbox")).toBe(false);
    expect(coerceValue(1, "checkbox")).toBe(true);
    expect(coerceValue(0, "checkbox")).toBe(false);
    expect(coerceValue(null, "checkbox")).toBe(false);
    expect(coerceValue(true, "checkbox")).toBe(true);
  });

  it("coerces into text via String(), null/undefined -> empty string", () => {
    expect(coerceValue(42, "text")).toBe("42");
    expect(coerceValue(true, "text")).toBe("true");
    expect(coerceValue(false, "text")).toBe("false");
    expect(coerceValue(null, "text")).toBe("");
    expect(coerceValue(undefined, "text")).toBe("");
    expect(coerceValue("keep", "text")).toBe("keep");
  });
});

describe("changeType", () => {
  it("changes the column type", () => {
    let table = blankTable();
    table = changeType(table, table.columns[0].id, "number");
    expect(table.columns[0].type).toBe("number");
  });

  it("coerces convertible values instead of wiping them (text -> number)", () => {
    let table = blankTable();
    const colId = table.columns[0].id;
    table = addRow(table);
    table = setCell(table, table.rows[0].id, colId, "42");
    table = setCell(table, table.rows[1].id, colId, "oops");
    table = changeType(table, colId, "number");
    expect(table.rows[0].cells[colId]).toBe(42);
    expect(table.rows[1].cells[colId]).toBeNull();
  });

  it("coerces across every transition (number/date/checkbox/text)", () => {
    let table = blankTable();
    const colId = table.columns[0].id;

    // text -> checkbox (truthiness)
    table = setCell(table, table.rows[0].id, colId, "yes");
    table = changeType(table, colId, "checkbox");
    expect(table.rows[0].cells[colId]).toBe(true);

    // checkbox -> number (true -> 1)
    table = changeType(table, colId, "number");
    expect(table.rows[0].cells[colId]).toBe(1);

    // number -> text (String())
    table = changeType(table, colId, "text");
    expect(table.rows[0].cells[colId]).toBe("1");

    // text -> date (valid date normalizes; other text -> null)
    table = setCell(table, table.rows[0].id, colId, "2024-03-09");
    table = changeType(table, colId, "date");
    expect(table.rows[0].cells[colId]).toBe("2024-03-09");
  });
});

describe("addRow / removeRow / setCell", () => {
  it("adds a row seeded with default values for every column", () => {
    let table = blankTable();
    table = addColumn(table, "checkbox");
    table = addRow(table);
    const row = table.rows[table.rows.length - 1];
    expect(row.cells[table.columns[0].id]).toBe("");
    expect(row.cells[table.columns[1].id]).toBe(false);
  });

  it("removes a row by id", () => {
    let table = blankTable();
    table = addRow(table);
    const [first, second] = table.rows;
    table = removeRow(table, first.id);
    expect(table.rows).toEqual([second]);
  });

  it("edits a single cell without touching others", () => {
    let table = blankTable();
    const rowId = table.rows[0].id;
    const colId = table.columns[0].id;
    table = setCell(table, rowId, colId, "Widget");
    expect(table.rows[0].cells[colId]).toBe("Widget");
  });
});

describe("serializeTable / parseTableContent round trip", () => {
  it("round-trips columns and rows through JSON", () => {
    let table = blankTable();
    table = addColumn(table, "number");
    table = setCell(table, table.rows[0].id, table.columns[0].id, "Widget");
    table = setCell(table, table.rows[0].id, table.columns[1].id, 42);

    const content = serializeTable(table);
    const restored = parseTableContent(content);

    expect(restored).toEqual(table);
  });

  it("drops orphan cell keys that don't reference a current column", () => {
    const restored = parseTableContent(
      JSON.stringify({
        version: 1,
        columns: [{ id: "c1", name: "Name", type: "text" }],
        rows: [{ id: "r1", cells: { c1: "keep", cGONE: "drop me" } }],
      }),
    );
    expect(restored.rows[0].cells).toEqual({ c1: "keep" });
  });

  // blankTable() mints fresh random column/row ids each call, so a fallback
  // table can't be compared with toEqual(blankTable()) directly; check its
  // shape instead (single "Name" text column with one blank row).
  function expectBlankTableShape(table: DbTable) {
    expect(table.columns).toHaveLength(1);
    expect(table.columns[0]).toMatchObject({ name: "Name", type: "text" });
    expect(table.rows).toHaveLength(1);
    expect(table.rows[0].cells[table.columns[0].id]).toBe("");
  }

  it("falls back to a blank table for empty or invalid content", () => {
    expectBlankTableShape(parseTableContent(""));
    expectBlankTableShape(parseTableContent("not json"));
    expectBlankTableShape(parseTableContent(JSON.stringify({ version: 1, columns: [], rows: [] })));
  });

  it("falls back to a blank table when a column or row is corrupted", () => {
    expectBlankTableShape(
      parseTableContent(JSON.stringify({ version: 1, columns: [{ notAColumn: true }], rows: [] })),
    );
    expectBlankTableShape(
      parseTableContent(
        JSON.stringify({
          version: 1,
          columns: [{ id: "c1", name: "Name", type: "bogus" }],
          rows: [],
        }),
      ),
    );
    expectBlankTableShape(
      parseTableContent(
        JSON.stringify({
          version: 1,
          columns: [{ id: "c1", name: "Name", type: "text" }],
          rows: [{ notARow: true }],
        }),
      ),
    );
    const badShape: Partial<DbTable> = { version: 1, columns: "not-an-array" as never };
    expectBlankTableShape(parseTableContent(JSON.stringify(badShape)));
  });
});
