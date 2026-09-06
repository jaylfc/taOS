import type { Cell, CellWithRowAndCol } from "@fortune-sheet/core";

// CSV import/export for a single sheet. Values are exported using the
// cell's formatted display text (m) when present, falling back to the raw
// value (v). Formulas are exported as their computed value, not the formula
// text, matching what a user sees on screen and what Excel/Sheets export.

export function cellDisplayValue(cell: Cell | null | undefined): string {
  if (!cell) return "";
  if (cell.m != null) return String(cell.m);
  if (cell.v != null) return String(cell.v);
  return "";
}

function csvEscape(value: string): string {
  if (/[",\n\r]/.test(value)) {
    return `"${value.replace(/"/g, '""')}"`;
  }
  return value;
}

export function sheetToCsv(celldata: CellWithRowAndCol[]): string {
  let maxRow = -1;
  let maxCol = -1;
  for (const entry of celldata) {
    if (entry.v == null) continue;
    if (cellDisplayValue(entry.v) === "") continue;
    maxRow = Math.max(maxRow, entry.r);
    maxCol = Math.max(maxCol, entry.c);
  }
  if (maxRow < 0 || maxCol < 0) return "";

  const grid: string[][] = Array.from({ length: maxRow + 1 }, () =>
    Array.from({ length: maxCol + 1 }, () => ""),
  );
  for (const entry of celldata) {
    if (entry.r > maxRow || entry.c > maxCol) continue;
    const row = grid[entry.r];
    if (row) row[entry.c] = cellDisplayValue(entry.v);
  }
  // RFC 4180 line endings (CRLF), including a trailing CRLF after the last
  // row, matching what Excel/Sheets emit.
  return grid.map((row) => row.map(csvEscape).join(",")).join("\r\n") + "\r\n";
}

// Minimal RFC 4180 CSV parser: handles quoted fields, escaped quotes ("")
// inside quoted fields, and both \n and \r\n line endings. Throws if a
// quoted field is never closed, rather than silently swallowing the rest of
// the file into a single field.
export function parseCsv(text: string): string[][] {
  const rows: string[][] = [];
  let row: string[] = [];
  let field = "";
  let inQuotes = false;
  const normalized = text.replace(/\r\n/g, "\n").replace(/\r/g, "\n");
  for (let i = 0; i < normalized.length; i++) {
    const ch = normalized[i];
    if (inQuotes) {
      if (ch === '"') {
        if (normalized[i + 1] === '"') {
          field += '"';
          i++;
        } else {
          inQuotes = false;
        }
      } else {
        field += ch;
      }
      continue;
    }
    if (ch === '"') {
      inQuotes = true;
    } else if (ch === ",") {
      row.push(field);
      field = "";
    } else if (ch === "\n") {
      row.push(field);
      rows.push(row);
      row = [];
      field = "";
    } else {
      field += ch;
    }
  }
  if (inQuotes) {
    throw new Error("Unterminated quoted field in CSV file");
  }

  row.push(field);
  rows.push(row);

  // A trailing newline produces one bogus final row of a single empty field.
  const lastRow = rows[rows.length - 1];
  if (rows.length > 1 && lastRow?.length === 1 && lastRow[0] === "") {
    rows.pop();
  }
  if (text.trim() === "") return [];
  return rows;
}
