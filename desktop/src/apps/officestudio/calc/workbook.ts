import type { Sheet } from "@fortune-sheet/core";

// Persisted shape saved as the office doc's `content` (kind "calc"). The
// workbook is the full multi-sheet state used to initialize the Workbook
// component on load, so a save/load round trip is just JSON stringify/parse.
export type CalcWorkbookFile = {
  version: 1;
  sheets: Sheet[];
};

export const DEFAULT_SHEET_ROWS = 100;
export const DEFAULT_SHEET_COLUMNS = 26;

export function blankSheet(id: string, name: string, order = 0): Sheet {
  return {
    id,
    name,
    order,
    celldata: [],
    row: DEFAULT_SHEET_ROWS,
    column: DEFAULT_SHEET_COLUMNS,
  };
}

export function blankWorkbook(): CalcWorkbookFile {
  return { version: 1, sheets: [blankSheet("sheet-1", "Sheet1", 0)] };
}

export function serializeWorkbook(sheets: Sheet[]): string {
  const file: CalcWorkbookFile = { version: 1, sheets };
  return JSON.stringify(file);
}

// Checks that a parsed value has the minimal shape a Sheet needs before we
// hand it to the Workbook component: corrupted or truncated saved content
// (e.g. a non-object entry, or celldata/row/column of the wrong type) would
// otherwise crash the renderer downstream instead of failing safely here.
function isValidSheet(value: unknown): value is Sheet {
  if (!value || typeof value !== "object") return false;
  const s = value as Partial<Sheet>;
  if (typeof s.name !== "string") return false;
  if (s.celldata !== undefined && !Array.isArray(s.celldata)) return false;
  if (s.row !== undefined && typeof s.row !== "number") return false;
  if (s.column !== undefined && typeof s.column !== "number") return false;
  return true;
}

export function parseWorkbookContent(content: string): CalcWorkbookFile {
  if (!content || !content.trim()) return blankWorkbook();
  try {
    const parsed = JSON.parse(content) as Partial<CalcWorkbookFile>;
    if (!parsed || !Array.isArray(parsed.sheets) || parsed.sheets.length === 0) {
      return blankWorkbook();
    }
    if (!parsed.sheets.every(isValidSheet)) {
      return blankWorkbook();
    }
    return { version: 1, sheets: parsed.sheets };
  } catch {
    return blankWorkbook();
  }
}
