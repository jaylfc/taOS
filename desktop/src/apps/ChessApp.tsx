import { useState, useCallback, useEffect, useRef } from "react";
import { Chess, type Square } from "chess.js";

type GameMode = "two-player" | "vs-agent";

const PIECE_SYMBOLS: Record<string, Record<string, string>> = {
  w: { k: "♔", q: "♕", r: "♖", b: "♗", n: "♘", p: "♙" },
  b: { k: "♚", q: "♛", r: "♜", b: "♝", n: "♞", p: "♟" },
};

const LIGHT = "#f0d9b5";
const DARK = "#b58863";
const SELECTED_BG = "#829769";
const VALID_MOVE_DOT = "rgba(0,0,0,0.25)";
const VALID_CAPTURE = "rgba(0,0,0,0.25)";

function squareName(row: number, col: number): Square {
  const file = String.fromCharCode(97 + col);
  const rank = String(8 - row);
  return (file + rank) as Square;
}

export function ChessApp({ windowId: _windowId }: { windowId: string }) {
  const [game, setGame] = useState(() => new Chess());
  const [selected, setSelected] = useState<Square | null>(null);
  const [validMoves, setValidMoves] = useState<Square[]>([]);
  const [mode, setMode] = useState<GameMode>("two-player");
  const [agentThinking, setAgentThinking] = useState(false);
  const [availableAgents, setAvailableAgents] = useState<string[]>([]);
  const [selectedAgent, setSelectedAgent] = useState<string>("");
  const [agentCommentary, setAgentCommentary] = useState<string>("");
  const agentTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);

  // Fetch available agents on mount
  useEffect(() => {
    fetch("/api/agents")
      .then((r) => (r.ok ? r.json() : []))
      .then((data) => {
        const names: string[] = Array.isArray(data)
          ? data.map((a: { name?: string }) => a?.name).filter((n): n is string => !!n)
          : [];
        setAvailableAgents(names);
        if (names.length > 0) setSelectedAgent((prev) => prev || names[0]!);
      })
      .catch(() => setAvailableAgents([]));
  }, []);

  const board = game.board();
  const turn = game.turn();
  const history = game.history();
  const isCheck = game.isCheck();
  const isCheckmate = game.isCheckmate();
  const isStalemate = game.isStalemate();
  const isDraw = game.isDraw();
  const isGameOver = game.isGameOver();

  const makeAgentMove = useCallback(
    async (g: Chess, agentName: string) => {
      if (g.isGameOver()) return;
      const verboseMoves = g.moves({ verbose: true }) as Array<{
        from: string;
        to: string;
        promotion?: string;
      }>;
      if (verboseMoves.length === 0) return;
      // UCI-format legal moves (e.g. e2e4, e7e8q)
      const uciMoves = verboseMoves.map(
        (m) => `${m.from}${m.to}${m.promotion ?? ""}`,
      );
      setAgentThinking(true);
      setAgentCommentary("");

      try {
        const res = await fetch("/api/games/chess/move", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            agent_name: agentName,
            fen: g.fen(),
            legal_moves: uciMoves,
            history: g.history(),
          }),
        });
        const data = (await res.json()) as {
          move?: string;
          commentary?: string;
          error?: string;
        };

        if (data.move) {
          // Parse UCI move (e.g. e2e4 or e7e8q)
          const from = data.move.slice(0, 2);
          const to = data.move.slice(2, 4);
          const promotion = data.move.length > 4 ? data.move.slice(4, 5) : undefined;
          try {
            g.move({ from, to, promotion });
            setGame(new Chess(g.fen()));
          } catch {
            // If the returned move isn't accepted, fall back to a random legal move
            const pick = verboseMoves[Math.floor(Math.random() * verboseMoves.length)]!;
            g.move({ from: pick.from, to: pick.to, promotion: pick.promotion });
            setGame(new Chess(g.fen()));
          }
        }
        if (data.commentary) setAgentCommentary(data.commentary);
        if (data.error) setAgentCommentary(data.error);
      } catch (e) {
        // Network error — fall back to random so the game keeps going
        const pick = verboseMoves[Math.floor(Math.random() * verboseMoves.length)]!;
        g.move({ from: pick.from, to: pick.to, promotion: pick.promotion });
        setGame(new Chess(g.fen()));
        setAgentCommentary(
          `Network error: ${e instanceof Error ? e.message : String(e)}`,
        );
      } finally {
        setAgentThinking(false);
      }
    },
    [],
  );

  useEffect(() => {
    return () => {
      if (agentTimerRef.current) clearTimeout(agentTimerRef.current);
    };
  }, []);

  // Trigger agent move when it's black's turn in agent mode
  useEffect(() => {
    if (
      mode === "vs-agent" &&
      turn === "b" &&
      !isGameOver &&
      !agentThinking &&
      selectedAgent
    ) {
      makeAgentMove(game, selectedAgent);
    }
  }, [mode, turn, isGameOver, agentThinking, game, selectedAgent, makeAgentMove]);

  function handleSquareClick(row: number, col: number) {
    if (isGameOver || agentThinking) return;
    if (mode === "vs-agent" && turn === "b") return;

    const sq = squareName(row, col);

    if (selected) {
      // Try to move
      const moveObj = game.moves({ verbose: true }).find(
        (m) => m.from === selected && m.to === sq,
      );
      if (moveObj) {
        // For simplicity, auto-queen promotion
        const promo = moveObj.flags.includes("p") ? "q" : undefined;
        game.move({ from: selected, to: sq, promotion: promo });
        setGame(new Chess(game.fen()));
        setSelected(null);
        setValidMoves([]);
        return;
      }

      // Clicked own piece — reselect
      const piece = board[row]![col];
      if (piece && piece.color === turn) {
        setSelected(sq);
        setValidMoves(
          game
            .moves({ square: sq, verbose: true })
            .map((m) => m.to as Square),
        );
        return;
      }

      // Deselect
      setSelected(null);
      setValidMoves([]);
      return;
    }

    // Nothing selected — select own piece
    const piece = board[row]![col];
    if (piece && piece.color === turn) {
      setSelected(sq);
      setValidMoves(
        game.moves({ square: sq, verbose: true }).map((m) => m.to as Square),
      );
    }
  }

  function handleNewGame() {
    if (agentTimerRef.current) clearTimeout(agentTimerRef.current);
    const fresh = new Chess();
    setGame(fresh);
    setSelected(null);
    setValidMoves([]);
    setAgentThinking(false);
    setAgentCommentary("");
  }

  function handleUndo() {
    if (agentThinking) return;
    if (mode === "vs-agent") {
      // Undo both agent and player move
      game.undo();
      game.undo();
    } else {
      game.undo();
    }
    setGame(new Chess(game.fen()));
    setSelected(null);
    setValidMoves([]);
  }

  function handleModeChange(newMode: GameMode) {
    if (agentTimerRef.current) clearTimeout(agentTimerRef.current);
    setMode(newMode);
    const fresh = new Chess();
    setGame(fresh);
    setSelected(null);
    setValidMoves([]);
    setAgentThinking(false);
    setAgentCommentary("");
  }

  function getStatus(): string {
    if (isCheckmate) return `Checkmate! ${turn === "w" ? "Black" : "White"} wins`;
    if (isStalemate) return "Stalemate — Draw";
    if (isDraw) return "Draw";
    if (agentThinking) return "Agent thinking...";
    const side = turn === "w" ? "White" : "Black";
    return `${side} to move${isCheck ? " (Check!)" : ""}`;
  }

  return (
    <div
      style={{
        display: "flex",
        height: "100%",
        background: "var(--color-shell-bg)",
        color: "var(--color-shell-text)",
        fontFamily: "system-ui, -apple-system, 'Segoe UI', sans-serif",
        overflow: "hidden",
      }}
    >
      {/* Board area */}
      <div
        style={{
          flex: 1,
          display: "flex",
          flexDirection: "column",
          alignItems: "center",
          justifyContent: "center",
          padding: 16,
          minWidth: 0,
        }}
      >
        {/* Status */}
        <div
          style={{
            fontSize: 16,
            fontWeight: 600,
            marginBottom: 10,
            color: isCheckmate
              ? "var(--color-traffic-close)"
              : isCheck
                ? "var(--color-traffic-minimize)"
                : "var(--color-shell-text)",
          }}
          role="status"
          aria-live="polite"
        >
          {getStatus()}
        </div>

        {/* Board */}
        <div
          style={{
            display: "grid",
            gridTemplateColumns: "repeat(8, 1fr)",
            gridTemplateRows: "repeat(8, 1fr)",
            width: "min(100%, 480px)",
            aspectRatio: "1",
            borderRadius: 4,
            overflow: "hidden",
            boxShadow: "0 4px 20px rgba(0,0,0,0.4)",
          }}
          role="grid"
          aria-label="Chess board"
        >
          {board.map((row, r) =>
            row.map((piece, c) => {
              const sq = squareName(r, c);
              const isLight = (r + c) % 2 === 0;
              const isSelected = selected === sq;
              const isValidTarget = validMoves.includes(sq);
              const hasPiece = piece !== null;

              let bg = isLight ? LIGHT : DARK;
              if (isSelected) bg = SELECTED_BG;

              return (
                <button
                  key={sq}
                  onClick={() => handleSquareClick(r, c)}
                  aria-label={`${sq}${piece ? ` ${piece.color === "w" ? "white" : "black"} ${piece.type}` : ""}`}
                  style={{
                    background: bg,
                    border: "none",
                    cursor: "pointer",
                    display: "flex",
                    alignItems: "center",
                    justifyContent: "center",
                    position: "relative",
                    fontSize: "clamp(24px, 5vw, 48px)",
                    lineHeight: 1,
                    padding: 0,
                    color: piece?.color === "b" ? "#1a1a1a" : "#fff",
                    textShadow:
                      piece?.color === "w"
                        ? "0 1px 3px rgba(0,0,0,0.4)"
                        : "0 1px 2px rgba(255,255,255,0.15)",
                  }}
                >
                  {/* Valid move indicator */}
                  {isValidTarget && !hasPiece && (
                    <div
                      style={{
                        position: "absolute",
                        width: "30%",
                        height: "30%",
                        borderRadius: "50%",
                        background: VALID_MOVE_DOT,
                      }}
                    />
                  )}
                  {isValidTarget && hasPiece && (
                    <div
                      style={{
                        position: "absolute",
                        inset: 2,
                        borderRadius: "50%",
                        border: `3px solid ${VALID_CAPTURE}`,
                      }}
                    />
                  )}
                  {piece && PIECE_SYMBOLS[piece.color]![piece.type]}
                </button>
              );
            }),
          )}
        </div>

        {/* Controls */}
        <div
          style={{
            display: "flex",
            gap: 8,
            marginTop: 12,
            flexWrap: "wrap",
            justifyContent: "center",
          }}
        >
          <select
            value={mode}
            onChange={(e) => handleModeChange(e.target.value as GameMode)}
            aria-label="Game mode"
            style={{
              padding: "6px 10px",
              borderRadius: 6,
              border: "1px solid var(--color-shell-border-strong)",
              background: "var(--color-shell-surface)",
              color: "var(--color-shell-text)",
              fontSize: 13,
              cursor: "pointer",
            }}
          >
            <option value="two-player">Two Player</option>
            <option value="vs-agent">Play vs Agent</option>
          </select>
          {mode === "vs-agent" && (
            availableAgents.length > 0 ? (
              <select
                value={selectedAgent}
                onChange={(e) => {
                  setSelectedAgent(e.target.value);
                  handleNewGame();
                }}
                aria-label="Select agent opponent"
                style={{
                  padding: "6px 10px",
                  borderRadius: 6,
                  border: "1px solid var(--color-shell-border-strong)",
                  background: "var(--color-shell-surface)",
                  color: "var(--color-shell-text)",
                  fontSize: 13,
                  cursor: "pointer",
                  maxWidth: 160,
                }}
              >
                {availableAgents.map((name) => (
                  <option key={name} value={name}>
                    {name}
                  </option>
                ))}
              </select>
            ) : (
              <span
                style={{
                  padding: "6px 10px",
                  borderRadius: 6,
                  border: "1px dashed var(--color-shell-border-strong)",
                  background: "var(--color-shell-surface)",
                  color: "var(--color-shell-text-secondary)",
                  fontSize: 12,
                  fontStyle: "italic",
                }}
                role="note"
              >
                No agents configured
              </span>
            )
          )}
          <button
            onClick={handleNewGame}
            aria-label="New game"
            style={{
              padding: "6px 14px",
              borderRadius: 6,
              border: "1px solid var(--color-shell-border-strong)",
              background: "var(--color-shell-surface)",
              color: "var(--color-shell-text)",
              fontSize: 13,
              cursor: "pointer",
            }}
          >
            New Game
          </button>
          <button
            onClick={handleUndo}
            disabled={history.length === 0 || agentThinking}
            aria-label="Undo move"
            style={{
              padding: "6px 14px",
              borderRadius: 6,
              border: "1px solid var(--color-shell-border-strong)",
              background: "var(--color-shell-surface)",
              color:
                history.length === 0 || agentThinking
                  ? "var(--color-shell-text-tertiary)"
                  : "var(--color-shell-text)",
              fontSize: 13,
              cursor: history.length === 0 || agentThinking ? "default" : "pointer",
            }}
          >
            Undo
          </button>
        </div>
      </div>

      {/* Move history sidebar */}
      <div
        style={{
          width: 180,
          borderLeft: "1px solid var(--color-shell-border)",
          display: "flex",
          flexDirection: "column",
          background: "var(--color-shell-bg-deep)",
        }}
      >
        <div
          style={{
            padding: "10px 12px",
            fontSize: 13,
            fontWeight: 600,
            borderBottom: "1px solid var(--color-shell-border)",
            color: "var(--color-shell-text-secondary)",
          }}
        >
          Moves
        </div>
        <div
          style={{
            flex: 1,
            overflowY: "auto",
            padding: "8px 12px",
            fontSize: 13,
            lineHeight: 1.8,
          }}
          role="log"
          aria-label="Move history"
        >
          {history.length === 0 && (
            <span style={{ color: "var(--color-shell-text-tertiary)", fontStyle: "italic" }}>
              No moves yet
            </span>
          )}
          {Array.from({ length: Math.ceil(history.length / 2) }).map((_, i) => (
            <div key={i} style={{ display: "flex", gap: 6 }}>
              <span style={{ color: "var(--color-shell-text-tertiary)", minWidth: 24 }}>
                {i + 1}.
              </span>
              <span style={{ color: "var(--color-shell-text)", minWidth: 40 }}>
                {history[i * 2]}
              </span>
              <span style={{ color: "var(--color-shell-text-secondary)" }}>
                {history[i * 2 + 1] ?? ""}
              </span>
            </div>
          ))}
        </div>
        {mode === "vs-agent" && agentCommentary && (
          <div
            style={{
              borderTop: "1px solid var(--color-shell-border)",
              padding: "10px 12px",
              fontSize: 12,
              color: "var(--color-shell-text)",
              background: "var(--color-shell-surface)",
              maxHeight: 140,
              overflowY: "auto",
            }}
            role="note"
            aria-label="Agent commentary"
          >
            <div
              style={{
                color: "var(--color-shell-text-secondary)",
                fontWeight: 600,
                marginBottom: 4,
              }}
            >
              {selectedAgent || "Agent"}
            </div>
            <div style={{ whiteSpace: "pre-wrap", lineHeight: 1.4 }}>
              {agentCommentary}
            </div>
          </div>
        )}
      </div>
    </div>
  );
}
