import { useCallback, useEffect, useState } from "react";

const WORDS = [
  "about","above","abuse","actor","acute","admit","adopt","adult","after","again",
  "agent","agree","ahead","alarm","album","alert","alien","align","alive","allow",
  "alone","alter","amino","angel","anger","angle","angry","anime","ankle","apart",
  "apple","arena","argue","arise","aside","asset","audio","avoid","aware","badge",
  "basic","beach","begin","being","below","bench","berry","black","blade","blame",
  "blank","blast","blaze","bleed","blend","bless","blind","block","bloom","blown",
  "board","bonus","boost","brain","brand","brave","bread","break","breed","brief",
  "bring","broad","brown","brush","build","bunch","burst",
];

const KEYBOARD_ROWS = [
  ["Q","W","E","R","T","Y","U","I","O","P"],
  ["A","S","D","F","G","H","J","K","L"],
  ["Enter","Z","X","C","V","B","N","M","Backspace"],
];

type TileState = "correct" | "present" | "absent" | "empty";

function pickWord(): string {
  return WORDS[Math.floor(Math.random() * WORDS.length)] ?? "apple";
}

function evaluateGuess(guess: string, target: string): TileState[] {
  const result: TileState[] = Array(5).fill("absent");
  const targetChars = target.split("");
  const used = Array(5).fill(false);

  // First pass: correct positions
  for (let i = 0; i < 5; i++) {
    if (guess[i] === targetChars[i]) {
      result[i] = "correct";
      used[i] = true;
    }
  }

  // Second pass: present but wrong position
  for (let i = 0; i < 5; i++) {
    if (result[i] === "correct") continue;
    for (let j = 0; j < 5; j++) {
      if (!used[j] && guess[i] === targetChars[j]) {
        result[i] = "present";
        used[j] = true;
        break;
      }
    }
  }

  return result;
}

function tileClass(state: TileState): string {
  switch (state) {
    case "correct": return "bg-green-600 text-white border-green-600";
    case "present": return "bg-yellow-500 text-white border-yellow-500";
    case "absent":  return "bg-shell-surface-active text-shell-text border-shell-surface-active";
    case "empty":   return "bg-shell-bg-deep text-shell-text border-shell-border";
  }
}

function keyClass(state: TileState | undefined): string {
  const base = "rounded-md font-semibold transition-colors active:scale-95 flex items-center justify-center ";
  switch (state) {
    case "correct": return base + "bg-green-600 text-white";
    case "present": return base + "bg-yellow-500 text-white";
    case "absent":  return base + "bg-shell-surface-active text-shell-text-secondary";
    default:        return base + "bg-shell-surface text-shell-text hover:bg-shell-surface/80";
  }
}

export function WordleApp({ windowId: _windowId }: { windowId: string }) {
  const [target, setTarget] = useState(pickWord);
  const [guesses, setGuesses] = useState<string[]>([]);
  const [states, setStates] = useState<TileState[][]>([]);
  const [current, setCurrent] = useState("");
  const [gameOver, setGameOver] = useState(false);
  const [message, setMessage] = useState("");

  // Track best known state per letter for keyboard colouring
  const keyStates: Record<string, TileState> = {};
  for (let g = 0; g < guesses.length; g++) {
    const guess = guesses[g]!;
    const st = states[g]!;
    for (let i = 0; i < 5; i++) {
      const letter = guess[i]!.toUpperCase();
      const s = st[i]!;
      const existing = keyStates[letter];
      if (s === "correct" || (!existing && s !== "empty") || (existing === "absent" && s === "present")) {
        keyStates[letter] = s;
      }
    }
  }

  const handleKey = useCallback((key: string) => {
    if (gameOver) return;

    if (key === "Enter") {
      if (current.length !== 5) return;
      const guess = current.toLowerCase();
      const result = evaluateGuess(guess, target);
      const newGuesses = [...guesses, guess];
      const newStates = [...states, result];
      setGuesses(newGuesses);
      setStates(newStates);
      setCurrent("");

      if (guess === target) {
        setMessage("You win!");
        setGameOver(true);
      } else if (newGuesses.length >= 6) {
        setMessage(`Game over! The word was "${target.toUpperCase()}".`);
        setGameOver(true);
      }
    } else if (key === "Backspace") {
      setCurrent((prev) => prev.slice(0, -1));
    } else if (/^[A-Za-z]$/.test(key) && current.length < 5) {
      setCurrent((prev) => prev + key.toUpperCase());
    }
  }, [gameOver, current, guesses, states, target]);

  useEffect(() => {
    function onKeyDown(e: KeyboardEvent) {
      if (e.ctrlKey || e.metaKey || e.altKey) return;
      handleKey(e.key);
    }
    window.addEventListener("keydown", onKeyDown);
    return () => window.removeEventListener("keydown", onKeyDown);
  }, [handleKey]);

  function newGame() {
    setTarget(pickWord());
    setGuesses([]);
    setStates([]);
    setCurrent("");
    setGameOver(false);
    setMessage("");
  }

  // Build the 6-row grid
  const rows: { letter: string; state: TileState }[][] = [];
  for (let r = 0; r < 6; r++) {
    const row: { letter: string; state: TileState }[] = [];
    if (r < guesses.length) {
      for (let c = 0; c < 5; c++) {
        row.push({ letter: guesses[r]![c]!.toUpperCase(), state: states[r]![c]! });
      }
    } else if (r === guesses.length) {
      for (let c = 0; c < 5; c++) {
        row.push({ letter: current[c] || "", state: "empty" });
      }
    } else {
      for (let c = 0; c < 5; c++) {
        row.push({ letter: "", state: "empty" });
      }
    }
    rows.push(row);
  }

  return (
    <div className="flex flex-col h-full bg-shell-bg-deep select-none items-center py-4 px-2 gap-4 overflow-auto">
      {/* Title + New Game */}
      <div className="flex items-center gap-4">
        <h1 className="text-shell-text text-xl font-bold tracking-wide">WORDLE</h1>
        <button
          onClick={newGame}
          className="px-3 py-1 rounded-md bg-accent text-white text-sm font-medium hover:bg-accent/90 transition-colors"
          aria-label="New Game"
        >
          New Game
        </button>
      </div>

      {/* Message */}
      {message && (
        <p className="text-shell-text font-semibold text-sm" role="status" aria-live="polite">
          {message}
        </p>
      )}

      {/* Grid */}
      <div className="grid grid-rows-6 gap-1.5" role="grid" aria-label="Wordle board">
        {rows.map((row, r) => (
          <div key={r} className="grid grid-cols-5 gap-1.5" role="row">
            {row.map((cell, c) => (
              <div
                key={c}
                className={`w-14 h-14 flex items-center justify-center text-2xl font-bold border-2 rounded ${tileClass(cell.state)}`}
                role="gridcell"
                aria-label={cell.letter || "empty"}
              >
                {cell.letter}
              </div>
            ))}
          </div>
        ))}
      </div>

      {/* On-screen keyboard */}
      <div className="flex flex-col gap-1.5 w-full max-w-[500px]" role="group" aria-label="Keyboard">
        {KEYBOARD_ROWS.map((row, r) => (
          <div key={r} className="flex gap-1 justify-center">
            {row.map((key) => {
              const isWide = key === "Enter" || key === "Backspace";
              const display = key === "Backspace" ? "\u232B" : key;
              return (
                <button
                  key={key}
                  className={keyClass(keyStates[key.toUpperCase()]) + (isWide ? " px-3 text-xs h-12 min-w-[60px]" : " w-9 h-12 text-sm")}
                  onClick={() => handleKey(key)}
                  aria-label={key}
                >
                  {display}
                </button>
              );
            })}
          </div>
        ))}
      </div>
    </div>
  );
}
