import { describe, it, expect } from "vitest";
import {
  addColumn,
  addRow,
  blankTable,
  parseTableContent,
  removeColumn,
  removeRow,
  renameColumn,
  serializeTable,
  setCell,
  setColumnType,
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

describe("renameColumn / setColumnType", () => {
  it("renames a column", () => {
    const table = blankTable();
    const renamed = renameColumn(table, table.columns[0].id, "Title");
    expect(renamed.columns[0].name).toBe("Title");
  });

  it("resets cell values to the new type's default", () => {
    let table = blankTable();
    table = setCell(table, table.rows[0].id, table.columns[0].id, "hello");
    table = setColumnType(table, table.columns[0].id, "checkbox");
    expect(table.columns[0].type).toBe("checkbox");
    expect(table.rows[0].cells[table.columns[0].id]).toBe(false);
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
