// Table model for the Database view. Persisted as the office doc's `content`
// (kind "db"): the whole table is serialized to JSON, mirroring how Calc
// serializes its workbook (see ../calc/workbook.ts) and Slides its deck
// (see ../slides/deck.ts).

import { randomId } from "@/lib/uid";

export type ColumnType = "text" | "number" | "checkbox" | "date";

export type Column = {
  id: string;
  name: string;
  type: ColumnType;
};

export type CellValue = string | number | boolean | null;

export type Row = {
  id: string;
  cells: Record<string, CellValue>;
};

export type DbTable = {
  version: 1;
  columns: Column[];
  rows: Row[];
};

export const COLUMN_TYPES: { id: ColumnType; label: string }[] = [
  { id: "text", label: "Text" },
  { id: "number", label: "Number" },
  { id: "checkbox", label: "Checkbox" },
  { id: "date", label: "Date" },
];

export function defaultValueForType(type: ColumnType): CellValue {
  switch (type) {
    case "checkbox":
      return false;
    case "number":
      return null;
    default:
      return "";
  }
}

export function newColumn(name: string, type: ColumnType = "text"): Column {
  return { id: randomId("col-"), name, type };
}

export function newRow(columns: Column[]): Row {
  const cells: Record<string, CellValue> = {};
  for (const col of columns) cells[col.id] = defaultValueForType(col.type);
  return { id: randomId("row-"), cells };
}

export function blankTable(): DbTable {
  const nameCol = newColumn("Name", "text");
  return { version: 1, columns: [nameCol], rows: [newRow([nameCol])] };
}

export function addColumn(table: DbTable, type: ColumnType = "text"): DbTable {
  const col = newColumn(`Column ${table.columns.length + 1}`, type);
  const rows = table.rows.map((r) => ({
    ...r,
    cells: { ...r.cells, [col.id]: defaultValueForType(type) },
  }));
  return { ...table, columns: [...table.columns, col], rows };
}

export function removeColumn(table: DbTable, columnId: string): DbTable {
  if (table.columns.length <= 1) return table;
  const columns = table.columns.filter((c) => c.id !== columnId);
  const rows = table.rows.map((r) => {
    const cells = { ...r.cells };
    delete cells[columnId];
    return { ...r, cells };
  });
  return { ...table, columns, rows };
}

export function renameColumn(table: DbTable, columnId: string, name: string): DbTable {
  return {
    ...table,
    columns: table.columns.map((c) => (c.id === columnId ? { ...c, name } : c)),
  };
}

// Changing a column's type resets that column's cells to the new type's
// default rather than trying to coerce old values (e.g. free text into a
// checkbox), which would otherwise silently produce nonsense values.
export function setColumnType(table: DbTable, columnId: string, type: ColumnType): DbTable {
  const columns = table.columns.map((c) => (c.id === columnId ? { ...c, type } : c));
  const rows = table.rows.map((r) => ({
    ...r,
    cells: { ...r.cells, [columnId]: defaultValueForType(type) },
  }));
  return { ...table, columns, rows };
}

export function addRow(table: DbTable): DbTable {
  return { ...table, rows: [...table.rows, newRow(table.columns)] };
}

export function removeRow(table: DbTable, rowId: string): DbTable {
  return { ...table, rows: table.rows.filter((r) => r.id !== rowId) };
}

export function setCell(table: DbTable, rowId: string, columnId: string, value: CellValue): DbTable {
  return {
    ...table,
    rows: table.rows.map((r) =>
      r.id === rowId ? { ...r, cells: { ...r.cells, [columnId]: value } } : r,
    ),
  };
}

export function serializeTable(table: DbTable): string {
  return JSON.stringify(table);
}

const VALID_TYPES: ColumnType[] = ["text", "number", "checkbox", "date"];

function isValidColumn(value: unknown): value is Column {
  if (!value || typeof value !== "object") return false;
  const c = value as Partial<Column>;
  if (typeof c.id !== "string") return false;
  if (typeof c.name !== "string") return false;
  if (!VALID_TYPES.includes(c.type as ColumnType)) return false;
  return true;
}

function isValidRow(value: unknown): value is Row {
  if (!value || typeof value !== "object") return false;
  const r = value as Partial<Row>;
  if (typeof r.id !== "string") return false;
  if (!r.cells || typeof r.cells !== "object") return false;
  return true;
}

// Parses saved content into a DbTable, falling back to a fresh blank table on
// any malformed or corrupted content instead of crashing the view.
export function parseTableContent(content: string): DbTable {
  if (!content || !content.trim()) return blankTable();
  try {
    const parsed = JSON.parse(content) as Partial<DbTable>;
    if (!parsed || !Array.isArray(parsed.columns) || !Array.isArray(parsed.rows)) {
      return blankTable();
    }
    if (parsed.columns.length === 0 || !parsed.columns.every(isValidColumn)) {
      return blankTable();
    }
    if (!parsed.rows.every(isValidRow)) {
      return blankTable();
    }
    return { version: 1, columns: parsed.columns as Column[], rows: parsed.rows as Row[] };
  } catch {
    return blankTable();
  }
}
