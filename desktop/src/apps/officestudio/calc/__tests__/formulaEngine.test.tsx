// Regression tests for the formula engine we rely on (@fortune-sheet/react's
// built-in formula parser). These mount the Workbook component headlessly
// (jsdom has no canvas, so the grid paints nothing, but the underlying data
// model and formula evaluation run independent of rendering) and drive it
// through the imperative ref API the same way CalcView does. The ref object
// itself (not a snapshot of ref.current) is captured, and always
// dereferenced fresh, since the exposed API instance can be replaced across
// internal re-renders.
import { render, waitFor } from "@testing-library/react";
import { describe, it, expect } from "vitest";
import { useEffect, useRef, useState, type RefObject } from "react";
import { Workbook, type WorkbookInstance } from "@fortune-sheet/react";
import "@fortune-sheet/react/dist/index.css";

// Re-reads ref.current at the point of use instead of caching it in a local
// variable, since the exposed API instance can be replaced across internal
// re-renders; the RefObject itself is the stable handle to hold on to.
function currentWb(ref: RefObject<WorkbookInstance | null>): WorkbookInstance {
  const wb = ref.current;
  if (!wb) throw new Error("workbook ref not ready");
  return wb;
}

function Harness({
  id,
  setup,
  onReady,
}: {
  id: string;
  setup: (wb: WorkbookInstance) => void;
  onReady: (ref: RefObject<WorkbookInstance | null>) => void;
}) {
  const ref = useRef<WorkbookInstance>(null);
  const [done, setDone] = useState(false);
  useEffect(() => {
    if (!ref.current || done) return;
    setDone(true);
    setup(ref.current);
    onReady(ref);
  }, [done, setup, onReady]);
  return (
    <Workbook
      ref={ref}
      data={[{ name: "Sheet1", id, celldata: [], row: 20, column: 10 }]}
      showToolbar={false}
      showFormulaBar={false}
      showSheetTabs={false}
    />
  );
}

describe("spreadsheet formula engine", () => {
  it("computes =SUM(A1:A3)", async () => {
    let wbRef: RefObject<WorkbookInstance | null> | undefined;
    render(
      <Harness
        id="sum"
        setup={(wb) => {
          wb.setCellValue(0, 0, 1);
          wb.setCellValue(1, 0, 2);
          wb.setCellValue(2, 0, 3);
          wb.setCellValue(3, 0, "=SUM(A1:A3)");
        }}
        onReady={(ref) => {
          wbRef = ref;
        }}
      />,
    );
    await waitFor(() => expect(currentWb(wbRef!).getCellValue(3, 0)).toBe(6));
  });

  it("recomputes a dependent cell after the cell it references changes", async () => {
    let wbRef: RefObject<WorkbookInstance | null> | undefined;
    render(
      <Harness
        id="dependent"
        setup={(wb) => {
          wb.setCellValue(0, 0, 5); // A1
          wb.setCellValue(0, 1, "=A1*2"); // B1
        }}
        onReady={(ref) => {
          wbRef = ref;
        }}
      />,
    );
    await waitFor(() => expect(currentWb(wbRef!).getCellValue(0, 1)).toBe(10));

    currentWb(wbRef!).setCellValue(0, 0, 7); // A1 changes
    currentWb(wbRef!).calculateFormula();
    await waitFor(() => expect(currentWb(wbRef!).getCellValue(0, 1)).toBe(14));
  });

  it("supports AVERAGE, MIN, MAX, COUNT and IF over a range", async () => {
    let wbRef: RefObject<WorkbookInstance | null> | undefined;
    render(
      <Harness
        id="functions"
        setup={(wb) => {
          wb.setCellValue(0, 0, 2);
          wb.setCellValue(1, 0, 4);
          wb.setCellValue(2, 0, 6);
          wb.setCellValue(0, 1, "=AVERAGE(A1:A3)");
          wb.setCellValue(1, 1, "=MIN(A1:A3)");
          wb.setCellValue(2, 1, "=MAX(A1:A3)");
          wb.setCellValue(3, 1, "=COUNT(A1:A3)");
          wb.setCellValue(4, 1, '=IF(A1>1,"yes","no")');
        }}
        onReady={(ref) => {
          wbRef = ref;
        }}
      />,
    );
    await waitFor(() => {
      expect(currentWb(wbRef!).getCellValue(0, 1)).toBe(4);
      expect(currentWb(wbRef!).getCellValue(1, 1)).toBe(2);
      expect(currentWb(wbRef!).getCellValue(2, 1)).toBe(6);
      expect(currentWb(wbRef!).getCellValue(3, 1)).toBe(3);
      expect(currentWb(wbRef!).getCellValue(4, 1)).toBe("yes");
    });
  });

  it("evaluates basic arithmetic formulas", async () => {
    let wbRef: RefObject<WorkbookInstance | null> | undefined;
    render(
      <Harness
        id="arithmetic"
        setup={(wb) => {
          wb.setCellValue(0, 0, "=(2+3)*4-1");
        }}
        onReady={(ref) => {
          wbRef = ref;
        }}
      />,
    );
    await waitFor(() => expect(currentWb(wbRef!).getCellValue(0, 0)).toBe(19));
  });
});
