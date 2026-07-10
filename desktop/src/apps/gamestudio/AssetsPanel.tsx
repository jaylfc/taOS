import { useState } from "react";
import { ImagePlus, Loader2, AlertCircle, Sparkles, Check, Cpu } from "lucide-react";
import { generateTexture, type AssetKind, type TextureResult } from "./game-assets-api";

/* ------------------------------------------------------------------ */
/*  AssetsPanel -- AI texture/sprite generation for the Game Studio     */
/*                                                                     */
/*  Prompt + size/tileable controls drive POST /assets/texture. The     */
/*  backend writes the PNG into the game's file set and returns its      */
/*  preview path; "Insert" appends a reference to the current file. When  */
/*  the backend reports available:false (no GPU/NPU worker) the panel     */
/*  shows a clear disabled state rather than a broken Generate button --  */
/*  mirroring the Images Studio tier-gate precedent.                     */
/* ------------------------------------------------------------------ */

export interface AssetsPanelProps {
  gameId: string;
  /** The file the generated reference will be appended to (null = no file). */
  activePath: string | null;
  /** Append a reference to the generated asset into the current file. */
  onInsert: (filename: string) => void;
}

const SIZES = [256, 512, 768, 1024] as const;

export function AssetsPanel({ gameId, activePath, onInsert }: AssetsPanelProps) {
  const [prompt, setPrompt] = useState("");
  const [kind, setKind] = useState<AssetKind>("texture");
  const [size, setSize] = useState<number>(512);
  const [tileable, setTileable] = useState(false);

  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [result, setResult] = useState<TextureResult | null>(null);
  const [inserted, setInserted] = useState(false);

  const unavailable = result?.available === false;

  const handleGenerate = async () => {
    const text = prompt.trim();
    if (!text || busy) return;
    setBusy(true);
    setError(null);
    setResult(null);
    setInserted(false);
    try {
      const res = await generateTexture(gameId, {
        prompt: text,
        width: size,
        height: size,
        tileable,
        kind,
      });
      setResult(res);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  };

  const handleInsert = () => {
    if (!result?.filename || !activePath) return;
    onInsert(result.filename);
    setInserted(true);
  };

  return (
    <div className="flex min-h-0 flex-1 flex-col">
      <div className="flex items-center gap-2 border-b border-shell-border px-3.5 py-3">
        <ImagePlus size={15} className="text-accent" />
        <h3 className="text-[13px] font-bold">Generate an asset</h3>
      </div>

      <div className="min-h-0 flex-1 overflow-auto px-3 py-3">
        {/* prompt */}
        <label htmlFor="gs-asset-prompt" className="mb-1 block text-[11px] font-semibold text-shell-text-secondary">
          Describe the texture or sprite
        </label>
        <textarea
          id="gs-asset-prompt"
          value={prompt}
          onChange={(e) => setPrompt(e.target.value)}
          rows={3}
          placeholder="a mossy stone wall, top-down"
          className="mb-3 w-full resize-none rounded-lg border border-shell-border bg-shell-surface px-2.5 py-2 text-[12px] text-shell-text placeholder:text-shell-text-tertiary focus-visible:border-accent/40 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent/20"
        />

        {/* controls */}
        <div className="mb-3 grid grid-cols-2 gap-2">
          <div>
            <label htmlFor="gs-asset-kind" className="mb-1 block text-[11px] font-semibold text-shell-text-secondary">
              Kind
            </label>
            <select
              id="gs-asset-kind"
              value={kind}
              onChange={(e) => setKind(e.target.value as AssetKind)}
              className="w-full rounded-lg border border-shell-border bg-shell-surface px-2 py-1.5 text-[12px] text-shell-text"
            >
              <option value="texture">Texture</option>
              <option value="sprite">Sprite</option>
            </select>
          </div>
          <div>
            <label htmlFor="gs-asset-size" className="mb-1 block text-[11px] font-semibold text-shell-text-secondary">
              Size
            </label>
            <select
              id="gs-asset-size"
              value={size}
              onChange={(e) => setSize(Number(e.target.value))}
              className="w-full rounded-lg border border-shell-border bg-shell-surface px-2 py-1.5 text-[12px] text-shell-text"
            >
              {SIZES.map((s) => (
                <option key={s} value={s}>
                  {s}×{s}
                </option>
              ))}
            </select>
          </div>
        </div>

        <label className="mb-3 flex items-center gap-2 text-[12px] text-shell-text-secondary">
          <input
            type="checkbox"
            checked={tileable}
            onChange={(e) => setTileable(e.target.checked)}
            className="h-3.5 w-3.5 accent-accent"
          />
          Tileable / seamless
        </label>

        <button
          type="button"
          onClick={() => void handleGenerate()}
          disabled={busy || !prompt.trim()}
          className="flex h-9 w-full items-center justify-center gap-1.5 rounded-lg bg-accent px-3 text-[12px] font-bold text-white disabled:cursor-not-allowed disabled:opacity-50"
        >
          {busy ? <Loader2 size={15} className="animate-spin" /> : <Sparkles size={14} />}
          {busy ? "Generating..." : "Generate"}
        </button>

        {/* tier-gated: no GPU/NPU worker */}
        {unavailable && (
          <div
            role="status"
            className="mt-3 flex items-start gap-2 rounded-xl border border-amber-500/30 bg-amber-500/10 px-3 py-2.5 text-[12px] text-amber-200"
          >
            <Cpu size={15} className="mt-0.5 flex-none" />
            <div>
              <div className="font-bold">Needs a GPU worker</div>
              <div className="text-amber-200/80">
                {result?.reason ?? "Connect a GPU or NPU worker to generate assets."}
              </div>
            </div>
          </div>
        )}

        {/* error */}
        {error && (
          <div role="alert" className="mt-3 flex items-center gap-2 rounded-xl border border-red-500/30 bg-red-500/10 px-3 py-2 text-[12px] text-red-300">
            <AlertCircle size={14} className="flex-none" />
            {error}
          </div>
        )}

        {/* preview + insert */}
        {result?.available && result.path && (
          <div className="mt-3 rounded-xl border border-shell-border bg-shell-surface p-2.5">
            <img
              src={result.path}
              alt={`Generated ${result.kind ?? "asset"}`}
              className="mb-2 w-full rounded-lg border border-shell-border"
            />
            <div className="mb-2 truncate text-[11px] text-shell-text-tertiary">
              {result.filename}
              {result.tier ? ` · ${result.tier}` : ""}
            </div>
            <button
              type="button"
              onClick={handleInsert}
              disabled={!activePath || inserted}
              className="flex h-8 w-full items-center justify-center gap-1.5 rounded-lg border border-accent/40 bg-accent-soft px-3 text-[12px] font-bold text-shell-text disabled:opacity-60"
            >
              <Check size={13} />
              {inserted ? "Inserted" : activePath ? `Insert into ${activePath}` : "Open a file to insert"}
            </button>
          </div>
        )}
      </div>
    </div>
  );
}
