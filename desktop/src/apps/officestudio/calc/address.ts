// Cell address helpers (0-indexed row/column <-> "A1" style labels).

export function colToLetters(col: number): string {
  let n = col + 1;
  let letters = "";
  while (n > 0) {
    const rem = (n - 1) % 26;
    letters = String.fromCharCode(65 + rem) + letters;
    n = Math.floor((n - 1) / 26);
  }
  return letters;
}

export function cellAddress(row: number, col: number): string {
  return `${colToLetters(col)}${row + 1}`;
}
