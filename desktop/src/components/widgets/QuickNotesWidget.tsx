import { useState, useCallback } from "react";
import { useWidgetSize } from "@/hooks/use-widget-size";

const STORAGE_KEY = "tinyagentos-quick-notes";

export function QuickNotesWidget() {
  const [text, setText] = useState(() => {
    try { return localStorage.getItem(STORAGE_KEY) ?? ""; } catch { return ""; }
  });
  const [containerRef, { tier }] = useWidgetSize();

  const handleChange = useCallback((e: React.ChangeEvent<HTMLTextAreaElement>) => {
    const val = e.target.value;
    setText(val);
    try { localStorage.setItem(STORAGE_KEY, val); } catch { /* storage full */ }
  }, []);

  const showLabel = tier !== "s";

  return (
    <div
      ref={containerRef}
      style={{ height: "100%", display: "flex", flexDirection: "column", padding: tier === "s" ? "2px" : "2px 2px 4px" }}
      aria-label="Quick notes widget"
      role="region"
    >
      {showLabel && (
        <div style={{ fontSize: "0.65rem", fontWeight: 600, color: "rgba(255,255,255,0.4)", textTransform: "uppercase", letterSpacing: "0.06em", marginBottom: 5 }}>
          Quick Notes
        </div>
      )}
      <textarea
        value={text}
        onChange={handleChange}
        placeholder={tier === "s" ? "Notes…" : "Type your notes here…"}
        aria-label="Quick notes"
        style={{
          flex: 1,
          resize: "none",
          background: "rgba(255,255,255,0.05)",
          border: "1px solid rgba(255,255,255,0.08)",
          borderRadius: 8,
          color: "rgba(255,255,255,0.85)",
          fontSize: tier === "s" ? "0.75rem" : "0.8rem",
          padding: tier === "s" ? "6px 8px" : "8px 10px",
          outline: "none",
          fontFamily: "inherit",
          lineHeight: 1.55,
          transition: "border-color 0.15s, background 0.15s",
          caretColor: "rgba(255,255,255,0.7)",
        }}
        onFocus={(e) => {
          e.currentTarget.style.borderColor = "rgba(255,255,255,0.2)";
          e.currentTarget.style.background = "rgba(255,255,255,0.07)";
        }}
        onBlur={(e) => {
          e.currentTarget.style.borderColor = "rgba(255,255,255,0.08)";
          e.currentTarget.style.background = "rgba(255,255,255,0.05)";
        }}
      />
    </div>
  );
}
