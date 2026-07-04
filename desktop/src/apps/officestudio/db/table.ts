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

// Coerces a single cell value into the target column type, preserving data
// wherever a sensible conversion exists rather than wiping it. Only genuinely
// unconvertible values collapse to null (or "" for text):
//   - number:   parsed via Number (booleans -> 1/0); non-numeric -> null
//   - date:     parsed via Date.parse, normalized to YYYY-MM-DD; invalid -> null
//   - checkbox: truthiness of the existing value
//   - text:     String(value); null/undefined -> ""
export function coerceValue(value: CellValue | undefined, type: ColumnType): CellValue {
  switch (type) {
    case "checkbox":
      return Boolean(value);
    case "number": {
      if (value === null || value === undefined || value === "") return null;
      const n = typeof value === "boolean" ? (value ? 1 : 0) : Number(value);
      return Number.isFinite(n) ? n : null;
    }
    case "date": {
      // Only text is a meaningful date source; booleans and numbers (a bare
      // number string like "42" would otherwise parse to a year via Date.parse)
      // are not dates and collapse to null.
      if (typeof value !== "string" || value === "") return null;
      const ts = Date.parse(value);
      if (Number.isNaN(ts)) return null;
      return new Date(ts).toISOString().slice(0, 10);
    }
    case "text":
    default:
      return value === null || value === undefined ? "" : String(value);
  }
}

// Changes a column's type and coerces every existing cell in that column into
// the new type (see coerceValue), so a type change preserves convertible data
// instead of silently wiping the column.
export function changeType(table: DbTable, columnId: string, type: ColumnType): DbTable {
  const columns = table.columns.map((c) => (c.id === columnId ? { ...c, type } : c));
  const rows = table.rows.map((r) => ({
    ...r,
    cells: { ...r.cells, [columnId]: coerceValue(r.cells[columnId], type) },
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
    const columns = parsed.columns as Column[];
    // Drop any orphan cell keys that don't reference a current column id
    // (e.g. from a removed column left behind by a manual JSON edit or a
    // future schema migration), so dead data isn't silently re-serialized.
    const validIds = new Set(columns.map((c) => c.id));
    const rows = (parsed.rows as Row[]).map((r) => {
      const cells: Record<string, CellValue> = {};
      for (const [colId, value] of Object.entries(r.cells)) {
        if (validIds.has(colId)) cells[colId] = value;
      }
      return { id: r.id, cells };
    });
    return { version: 1, columns, rows };
  } catch {
    return blankTable();
  }
}
