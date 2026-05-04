/**
 * ReaderMode — replaces the active tab's iframe with a styled article view.
 *
 * The raw Readability HTML is sanitised through DOMPurify before rendering
 * (XSS prevention). Font size and family are local component state.
 */
import { useMemo, useState } from "react";
import DOMPurify from "dompurify";
import { useBrowserStore } from "@/stores/browser-store";
import type { Tab } from "./types";

type FontSize = "small" | "medium" | "large";
type FontFamily = "serif" | "sans-serif";

const FONT_SIZE_CLASS: Record<FontSize, string> = {
  small: "text-sm leading-relaxed",
  medium: "text-base leading-relaxed",
  large: "text-lg leading-relaxed",
};

interface ReaderModeProps {
  tab: Tab;
  windowId: string;
}

export function ReaderMode({ tab, windowId }: ReaderModeProps) {
  const setTabReader = useBrowserStore((s) => s.setTabReader);
  const [fontSize, setFontSize] = useState<FontSize>("medium");
  const [fontFamily, setFontFamily] = useState<FontFamily>("serif");

  const extract = tab.readerExtract;

  const sanitisedHtml = useMemo(
    () => DOMPurify.sanitize(extract?.html ?? "", { USE_PROFILES: { html: true } }),
    [extract?.html],
  );

  if (!extract) return null;

  let sourceDomain = "";
  try {
    sourceDomain = new URL(tab.url).hostname;
  } catch {
    sourceDomain = tab.url;
  }

  const fontFamilyStyle =
    fontFamily === "serif" ? "font-serif" : "font-sans";

  return (
    <div
      data-testid="reader-mode"
      className="absolute inset-0 overflow-y-auto bg-shell-surface text-shell-text"
    >
      <div className="mx-auto max-w-[700px] px-6 py-8">
        {/* Toolbar */}
        <div className="flex items-center justify-between mb-6 gap-4">
          {/* Font size controls */}
          <div
            role="group"
            aria-label="Font size"
            className="flex items-center gap-1"
          >
            {(["small", "medium", "large"] as FontSize[]).map((size) => (
              <button
                key={size}
                type="button"
                aria-label={`Font size ${size}`}
                aria-pressed={fontSize === size}
                onClick={() => setFontSize(size)}
                className={`px-2 py-1 rounded text-xs border ${
                  fontSize === size
                    ? "bg-accent text-white border-accent"
                    : "border-shell-border-subtle hover:bg-shell-hover"
                }`}
              >
                {size === "small" ? "Aˢ" : size === "medium" ? "A" : "Aᴷ"}
                <span className="sr-only">{size}</span>
              </button>
            ))}
          </div>

          {/* Font family toggle */}
          <div
            role="group"
            aria-label="Font family"
            className="flex items-center gap-1"
          >
            {(["serif", "sans-serif"] as FontFamily[]).map((family) => (
              <button
                key={family}
                type="button"
                aria-label={`Font family ${family}`}
                aria-pressed={fontFamily === family}
                onClick={() => setFontFamily(family)}
                className={`px-2 py-1 rounded text-xs border ${
                  fontFamily === family
                    ? "bg-accent text-white border-accent"
                    : "border-shell-border-subtle hover:bg-shell-hover"
                } ${family === "serif" ? "font-serif" : "font-sans"}`}
              >
                {family === "serif" ? "Serif" : "Sans"}
              </button>
            ))}
          </div>

          {/* Exit button */}
          <button
            type="button"
            aria-label="Exit Reader mode"
            onClick={() => setTabReader(windowId, tab.id, { readerActive: false })}
            className="ml-auto px-3 py-1 rounded text-xs border border-shell-border-subtle hover:bg-shell-hover"
          >
            Exit Reader
          </button>
        </div>

        {/* Article content */}
        <article aria-label={extract.title || "Article"}>
          <h1
            className={`font-bold mb-2 ${fontFamilyStyle} ${
              fontSize === "small"
                ? "text-xl"
                : fontSize === "medium"
                  ? "text-2xl"
                  : "text-3xl"
            }`}
          >
            {extract.title || tab.title || "Untitled"}
          </h1>
          {sourceDomain && (
            <p className="text-shell-text-secondary text-xs mb-6">{sourceDomain}</p>
          )}

          {/* Article body — sanitisedHtml passed through DOMPurify above */}
          <div
            data-testid="reader-body"
            className={`prose max-w-none ${fontFamilyStyle} ${FONT_SIZE_CLASS[fontSize]}`}
            dangerouslySetInnerHTML={{ __html: sanitisedHtml }}
          />
        </article>
      </div>
    </div>
  );
}
