import { useState, useCallback, useEffect, useRef } from "react";

/* ------------------------------------------------------------------ */
/*  Puzzle data types                                                  */
/* ------------------------------------------------------------------ */

interface Clue {
  num: number;
  direction: "across" | "down";
  text: string;
  answer: string;
  row: number;
  col: number;
}

interface Puzzle {
  size: number;
  grid: (string | null)[][]; // null = blocked cell, letter = answer
  clues: Clue[];
}

/* ------------------------------------------------------------------ */
/*  Hardcoded puzzles                                                  */
/* ------------------------------------------------------------------ */

const PUZZLES: Puzzle[] = [
  {
    size: 5,
    grid: [
      ["S", "T", "A", "R", "S"],
      ["O", null, "I", null, "A"],
      ["L", "I", "M", "E", "D"],
      ["A", null, "S", null, null],
      ["R", "A", "N", "G", "E"],
    ],
    clues: [
      { num: 1, direction: "across", text: "Celestial objects", answer: "STARS", row: 0, col: 0 },
      { num: 5, direction: "across", text: "Citrus fruit", answer: "LIME", row: 2, col: 1 },
      { num: 6, direction: "across", text: "A spread or extent", answer: "RANGE", row: 4, col: 0 },
      { num: 1, direction: "down", text: "Relating to the sun", answer: "SOLAR", row: 0, col: 0 },
      { num: 2, direction: "down", text: "Goals or objectives", answer: "AIMS", row: 0, col: 2 },
      { num: 3, direction: "down", text: "Unhappy", answer: "SAD", row: 0, col: 4 },
    ],
  },
  {
    size: 5,
    grid: [
      ["B", "R", "A", "V", "E"],
      ["O", null, "N", null, "A"],
      ["A", "R", "T", "S", "R"],
      ["T", null, "S", null, "T"],
      ["S", "K", "I", "N", "H"],
    ],
    clues: [
      { num: 1, direction: "across", text: "Courageous", answer: "BRAVE", row: 0, col: 0 },
      { num: 4, direction: "across", text: "Creative works", answer: "ARTS", row: 2, col: 1 },
      { num: 5, direction: "across", text: "Outer covering", answer: "SKIN", row: 4, col: 1 },
      { num: 1, direction: "down", text: "Watercraft (plural)", answer: "BOATS", row: 0, col: 0 },
      { num: 2, direction: "down", text: "Insects", answer: "ANTS", row: 0, col: 2 },
      { num: 3, direction: "down", text: "The planet", answer: "EARTH", row: 0, col: 4 },
    ],
  },
  {
    size: 5,
    grid: [
      ["C", "L", "A", "S", "P"],
      ["H", null, "R", null, "E"],
      ["A", "W", "E", "E", "D"],
      ["R", null, "A", null, "A"],
      ["M", "I", "S", "T", "L"],
    ],
    clues: [
      { num: 1, direction: "across", text: "To grip tightly", answer: "CLASP", row: 0, col: 0 },
      { num: 4, direction: "across", text: "Unwanted garden plant", answer: "WEED", row: 2, col: 1 },
      { num: 5, direction: "across", text: "Foggy vapour", answer: "MISTL", row: 4, col: 0 },
      { num: 1, direction: "down", text: "Attractiveness", answer: "CHARM", row: 0, col: 0 },
      { num: 2, direction: "down", text: "Region or zone", answer: "AREAS", row: 0, col: 2 },
      { num: 3, direction: "down", text: "Bicycle part", answer: "PEDAL", row: 0, col: 4 },
    ],
  },
];

/* ------------------------------------------------------------------ */
/*  Helpers                                                            */
/* ------------------------------------------------------------------ */

/** Safely access a grid cell (returns null for out-of-bounds or blocked) */
function cell(grid: (string | null)[][], r: number, c: number): string | null {
  return grid[r]?.[c] ?? null;
}

/** Build a map of (row,col) -> clue number for numbered cells */
function buildNumberMap(puzzle: Puzzle): Map<string, number> {
  const map = new Map<string, number>();
  for (const clue of puzzle.clues) {
    const key = `${clue.row},${clue.col}`;
    if (!map.has(key)) map.set(key, clue.num);
  }
  return map;
}

/** Get the cells belonging to a clue */
function clueCells(clue: Clue, size: number): [number, number][] {
  const cells: [number, number][] = [];
  const len = clue.answer.length;
  for (let i = 0; i < len; i++) {
    const r = clue.direction === "down" ? clue.row + i : clue.row;
    const c = clue.direction === "across" ? clue.col + i : clue.col;
    if (r < size && c < size) cells.push([r, c]);
  }
  return cells;
}

/* ------------------------------------------------------------------ */
/*  Component                                                          */
/* ------------------------------------------------------------------ */

export function CrosswordsApp({ windowId: _windowId }: { windowId: string }) {
  const [puzzleIdx, setPuzzleIdx] = useState(0);
  const puzzle = PUZZLES[puzzleIdx]!;
  const { size, grid, clues } = puzzle;

  // User-entered letters: size x size, "" = empty
  const [letters, setLetters] = useState<string[][]>(() =>
    Array.from({ length: size }, () => Array(size).fill(""))
  );
  const [selectedRow, setSelectedRow] = useState(0);
  const [selectedCol, setSelectedCol] = useState(0);
  const [direction, setDirection] = useState<"across" | "down">("across");
  const [checkMode, setCheckMode] = useState(false);

  const gridRef = useRef<HTMLDivElement>(null);
  const numberMap = buildNumberMap(puzzle);

  /* Find the active clue based on selection + direction */
  const activeClue = clues.find((c) => {
    if (c.direction !== direction) return false;
    const cells = clueCells(c, size);
    return cells.some(([r, cc]) => r === selectedRow && cc === selectedCol);
  });

  const highlightedCells = new Set<string>();
  if (activeClue) {
    for (const [r, c] of clueCells(activeClue, size)) {
      highlightedCells.add(`${r},${c}`);
    }
  }

  /* Reset board when switching puzzles */
  const resetBoard = useCallback(
    (idx: number) => {
      const p = PUZZLES[idx]!;
      setLetters(Array.from({ length: p.size }, () => Array(p.size).fill("")));
      setSelectedRow(0);
      setSelectedCol(0);
      setDirection("across");
      setCheckMode(false);
    },
    []
  );

  /* Focus grid on mount */
  useEffect(() => {
    gridRef.current?.focus();
  }, [puzzleIdx]);

  /* Navigate to next open cell in current direction */
  const moveNext = useCallback(
    (r: number, c: number, dir: "across" | "down") => {
      const nr = dir === "down" ? r + 1 : r;
      const nc = dir === "across" ? c + 1 : c;
      if (nr < size && nc < size && cell(grid, nr, nc) !== null) {
        setSelectedRow(nr);
        setSelectedCol(nc);
      }
    },
    [grid, size]
  );

  const movePrev = useCallback(
    (r: number, c: number, dir: "across" | "down") => {
      const nr = dir === "down" ? r - 1 : r;
      const nc = dir === "across" ? c - 1 : c;
      if (nr >= 0 && nc >= 0 && cell(grid, nr, nc) !== null) {
        setSelectedRow(nr);
        setSelectedCol(nc);
      }
    },
    [grid, size]
  );

  /* Keyboard handler */
  const handleKeyDown = useCallback(
    (e: React.KeyboardEvent) => {
      if (cell(grid, selectedRow, selectedCol) === null) return;

      if (e.key === "ArrowRight") {
        e.preventDefault();
        setDirection("across");
        const nc = Math.min(selectedCol + 1, size - 1);
        if (cell(grid, selectedRow, nc) !== null) setSelectedCol(nc);
      } else if (e.key === "ArrowLeft") {
        e.preventDefault();
        setDirection("across");
        const nc = Math.max(selectedCol - 1, 0);
        if (cell(grid, selectedRow, nc) !== null) setSelectedCol(nc);
      } else if (e.key === "ArrowDown") {
        e.preventDefault();
        setDirection("down");
        const nr = Math.min(selectedRow + 1, size - 1);
        if (cell(grid, nr, selectedCol) !== null) setSelectedRow(nr);
      } else if (e.key === "ArrowUp") {
        e.preventDefault();
        setDirection("down");
        const nr = Math.max(selectedRow - 1, 0);
        if (cell(grid, nr, selectedCol) !== null) setSelectedRow(nr);
      } else if (e.key === "Backspace") {
        e.preventDefault();
        setLetters((prev) => {
          const next = prev.map((row) => [...row]);
          const row = next[selectedRow];
          if (row && row[selectedCol] !== "") {
            row[selectedCol] = "";
          } else {
            movePrev(selectedRow, selectedCol, direction);
          }
          return next;
        });
        setCheckMode(false);
      } else if (/^[a-zA-Z]$/.test(e.key)) {
        e.preventDefault();
        setLetters((prev) => {
          const next = prev.map((row) => [...row]);
          const row = next[selectedRow];
          if (row) row[selectedCol] = e.key.toUpperCase();
          return next;
        });
        moveNext(selectedRow, selectedCol, direction);
        setCheckMode(false);
      }
    },
    [selectedRow, selectedCol, direction, grid, size, moveNext, movePrev]
  );

  /* Cell click */
  const handleCellClick = (r: number, c: number) => {
    if (cell(grid, r, c) === null) return;
    if (r === selectedRow && c === selectedCol) {
      setDirection((d) => (d === "across" ? "down" : "across"));
    } else {
      setSelectedRow(r);
      setSelectedCol(c);
    }
    gridRef.current?.focus();
  };

  /* Clue click */
  const handleClueClick = (clue: Clue) => {
    setSelectedRow(clue.row);
    setSelectedCol(clue.col);
    setDirection(clue.direction);
    gridRef.current?.focus();
  };

  /* Cell colour logic */
  const cellStyle = (r: number, c: number): React.CSSProperties => {
    if (cell(grid, r, c) === null) return { background: "#1a1a2e" };

    const isSelected = r === selectedRow && c === selectedCol;
    const isHighlighted = highlightedCells.has(`${r},${c}`);

    let bg = "#ffffff";
    if (isSelected) bg = "#fbbf24";
    else if (isHighlighted) bg = "#fef3c7";

    let color = "#111827";

    const userLetter = letters[r]?.[c] ?? "";
    if (checkMode && userLetter !== "") {
      const correct = userLetter === cell(grid, r, c);
      if (isSelected) {
        color = correct ? "#16a34a" : "#dc2626";
      } else {
        bg = correct ? "#bbf7d0" : "#fecaca";
      }
    }

    return { background: bg, color, cursor: "pointer" };
  };

  const acrossClues = clues.filter((c) => c.direction === "across");
  const downClues = clues.filter((c) => c.direction === "down");

  return (
    <div
      style={{
        display: "flex",
        flexDirection: "column",
        height: "100%",
        background: "var(--color-shell-bg)",
        color: "var(--color-shell-text)",
        fontFamily: "system-ui, -apple-system, sans-serif",
        padding: 16,
        gap: 12,
        overflow: "auto",
      }}
    >
      {/* Toolbar */}
      <div style={{ display: "flex", gap: 8, alignItems: "center" }}>
        <span style={{ fontWeight: 700, fontSize: 18, marginRight: "auto" }}>
          Crossword #{puzzleIdx + 1}
        </span>
        <button
          onClick={() => setCheckMode(true)}
          aria-label="Check answers"
          style={btnStyle}
        >
          Check
        </button>
        <button
          onClick={() => {
            const next = (puzzleIdx + 1) % PUZZLES.length;
            setPuzzleIdx(next);
            resetBoard(next);
          }}
          aria-label="New puzzle"
          style={btnStyle}
        >
          New Puzzle
        </button>
      </div>

      {/* Main area: grid + clues */}
      <div
        style={{
          display: "flex",
          gap: 20,
          flex: 1,
          minHeight: 0,
          flexWrap: "wrap",
        }}
      >
        {/* Grid */}
        <div
          ref={gridRef}
          tabIndex={0}
          onKeyDown={handleKeyDown}
          role="grid"
          aria-label="Crossword grid"
          style={{
            display: "inline-grid",
            gridTemplateColumns: `repeat(${size}, 48px)`,
            gridTemplateRows: `repeat(${size}, 48px)`,
            gap: 2,
            outline: "none",
            flexShrink: 0,
            alignSelf: "flex-start",
          }}
        >
          {Array.from({ length: size }, (_, r) =>
            Array.from({ length: size }, (_, c) => {
              const num = numberMap.get(`${r},${c}`);
              const letter = letters[r]?.[c] ?? "";
              const isBlocked = cell(grid, r, c) === null;
              return (
                <div
                  key={`${r}-${c}`}
                  role="gridcell"
                  aria-label={
                    isBlocked
                      ? "Blocked"
                      : `Row ${r + 1} Column ${c + 1}${letter ? `, letter ${letter}` : ""}`
                  }
                  onClick={() => handleCellClick(r, c)}
                  style={{
                    width: 48,
                    height: 48,
                    position: "relative",
                    display: "flex",
                    alignItems: "center",
                    justifyContent: "center",
                    fontSize: 20,
                    fontWeight: 600,
                    borderRadius: 3,
                    userSelect: "none",
                    transition: "background 0.1s",
                    ...cellStyle(r, c),
                  }}
                >
                  {num != null && (
                    <span
                      style={{
                        position: "absolute",
                        top: 2,
                        left: 4,
                        fontSize: 10,
                        fontWeight: 700,
                        lineHeight: 1,
                        color: "#6b7280",
                      }}
                    >
                      {num}
                    </span>
                  )}
                  {!isBlocked && letter}
                </div>
              );
            })
          )}
        </div>

        {/* Clues panel */}
        <div
          style={{
            display: "flex",
            gap: 16,
            flex: 1,
            minWidth: 200,
            overflow: "auto",
          }}
        >
          {/* Across */}
          <div style={{ flex: 1 }}>
            <h3
              style={{
                fontSize: 14,
                fontWeight: 700,
                textTransform: "uppercase",
                letterSpacing: 1,
                marginBottom: 8,
                color: "var(--color-shell-text-secondary)",
              }}
            >
              Across
            </h3>
            <ul style={{ listStyle: "none", padding: 0, margin: 0 }}>
              {acrossClues.map((clue) => (
                <li
                  key={`a-${clue.num}`}
                  onClick={() => handleClueClick(clue)}
                  role="button"
                  tabIndex={0}
                  onKeyDown={(e) => {
                    if (e.key === "Enter" || e.key === " ") handleClueClick(clue);
                  }}
                  aria-label={`${clue.num} across: ${clue.text}`}
                  style={{
                    padding: "4px 8px",
                    borderRadius: 4,
                    cursor: "pointer",
                    fontSize: 13,
                    lineHeight: 1.5,
                    background:
                      activeClue === clue ? "rgba(251,191,36,0.25)" : "transparent",
                    marginBottom: 2,
                  }}
                >
                  <strong>{clue.num}.</strong> {clue.text}
                </li>
              ))}
            </ul>
          </div>
          {/* Down */}
          <div style={{ flex: 1 }}>
            <h3
              style={{
                fontSize: 14,
                fontWeight: 700,
                textTransform: "uppercase",
                letterSpacing: 1,
                marginBottom: 8,
                color: "var(--color-shell-text-secondary)",
              }}
            >
              Down
            </h3>
            <ul style={{ listStyle: "none", padding: 0, margin: 0 }}>
              {downClues.map((clue) => (
                <li
                  key={`d-${clue.num}`}
                  onClick={() => handleClueClick(clue)}
                  role="button"
                  tabIndex={0}
                  onKeyDown={(e) => {
                    if (e.key === "Enter" || e.key === " ") handleClueClick(clue);
                  }}
                  aria-label={`${clue.num} down: ${clue.text}`}
                  style={{
                    padding: "4px 8px",
                    borderRadius: 4,
                    cursor: "pointer",
                    fontSize: 13,
                    lineHeight: 1.5,
                    background:
                      activeClue === clue ? "rgba(251,191,36,0.25)" : "transparent",
                    marginBottom: 2,
                  }}
                >
                  <strong>{clue.num}.</strong> {clue.text}
                </li>
              ))}
            </ul>
          </div>
        </div>
      </div>

      {/* Status bar */}
      {activeClue && (
        <div
          style={{
            fontSize: 13,
            color: "var(--color-shell-text-secondary)",
            borderTop: "1px solid var(--color-shell-border)",
            paddingTop: 8,
          }}
        >
          <strong>
            {activeClue.num} {activeClue.direction}:
          </strong>{" "}
          {activeClue.text}
        </div>
      )}
    </div>
  );
}

/* ------------------------------------------------------------------ */
/*  Shared button style                                                */
/* ------------------------------------------------------------------ */

const btnStyle: React.CSSProperties = {
  padding: "6px 14px",
  borderRadius: 6,
  border: "1px solid var(--color-shell-border-strong)",
  background: "var(--color-shell-surface)",
  color: "var(--color-shell-text)",
  fontSize: 13,
  fontWeight: 600,
  cursor: "pointer",
};
