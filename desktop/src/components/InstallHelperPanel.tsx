import { useEffect, useState } from "react";
import { createPortal } from "react-dom";
import { isIOS } from "@/lib/platform";

interface Props {
  appId: string;
  appName: string;
  onClose: () => void;
}

export function InstallHelperPanel({ appId, appName, onClose }: Props) {
  const [copied, setCopied] = useState(false);

  const url = `${window.location.origin}/app.html?app=${encodeURIComponent(appId)}`;

  useEffect(() => {
    const handler = (e: KeyboardEvent) => {
      if (e.key === "Escape") onClose();
    };
    window.addEventListener("keydown", handler);
    return () => window.removeEventListener("keydown", handler);
  }, [onClose]);

  function fallbackCopy(): boolean {
    // Non-secure contexts (plain HTTP on a LAN / Tailscale IP) don't expose
    // navigator.clipboard. Operate on the already-visible URL input rather than
    // a hidden textarea: iOS Safari refuses to copy from opacity:0 elements and
    // will not select a readonly field, so toggle readonly off, select the real
    // on-screen input, copy, then restore. The definitive fix is HTTPS (taOSgo
    // or Tailscale Serve), where navigator.clipboard works directly.
    const el = document.getElementById("install-helper-url-input") as HTMLInputElement | null;
    if (!el) return false;
    const wasReadOnly = el.readOnly;
    try {
      el.readOnly = false;
      el.focus();
      el.setSelectionRange(0, el.value.length);
      return document.execCommand("copy");
    } catch {
      return false;
    } finally {
      el.readOnly = wasReadOnly;
    }
  }

  async function handleCopy() {
    let ok = false;
    try {
      if (navigator.clipboard && window.isSecureContext) {
        await navigator.clipboard.writeText(url);
        ok = true;
      }
    } catch {
      ok = false;
    }
    if (!ok) ok = fallbackCopy();
    if (ok) {
      setCopied(true);
      setTimeout(() => setCopied(false), 2000);
    } else {
      // Couldn't copy programmatically — select the visible field so the
      // user can copy it by hand.
      const el = document.getElementById("install-helper-url-input") as HTMLInputElement | null;
      el?.focus();
      el?.setSelectionRange(0, url.length);
    }
  }

  const platformHint = isIOS()
    ? "In Safari, tap the Share button, then Add to Home Screen."
    : "In Chrome, open the menu and choose Install app or Add to Home screen.";

  // Portal to body so the fixed overlay is not mispositioned when mounted
  // inside a transformed ancestor (the desktop window uses CSS transforms).
  return createPortal(
    <div
      style={{
        position: "fixed",
        inset: 0,
        zIndex: 200,
        display: "flex",
        alignItems: "center",
        justifyContent: "center",
        background: "rgba(0,0,0,0.6)",
        padding: "1rem",
      }}
      onClick={onClose}
      onKeyDown={(e) => { if (e.key === "Escape") onClose(); }}
      tabIndex={-1}
      role="dialog"
      aria-modal="true"
      aria-labelledby="install-helper-title"
    >
      <div
        style={{
          // Must be fully opaque: --color-shell-surface is a translucent
          // elevation overlay (rgba white 0.04) and let the page show through.
          // --color-shell-bg is the solid shell base and is theme-aware.
          background: "var(--color-shell-bg, #1d1d1f)",
          border: "1px solid rgba(255,255,255,0.12)",
          borderRadius: "12px",
          boxShadow: "0 24px 64px rgba(0,0,0,0.5)",
          width: "100%",
          maxWidth: "420px",
          padding: "1.25rem 1.5rem 1.5rem",
          display: "flex",
          flexDirection: "column",
          gap: "0.75rem",
        }}
        onClick={(e) => e.stopPropagation()}
      >
        <h2
          id="install-helper-title"
          style={{
            margin: 0,
            fontSize: "0.875rem",
            fontWeight: 600,
            color: "var(--color-shell-text, #fff)",
          }}
        >
          Install {appName}
        </h2>

        <p
          style={{
            margin: 0,
            fontSize: "0.8125rem",
            color: "var(--color-shell-text-secondary, rgba(255,255,255,0.55))",
            lineHeight: 1.5,
          }}
        >
          To use {appName} as its own app, open the link below in your browser,
          then add it to your home screen.
        </p>

        <input
          id="install-helper-url-input"
          readOnly
          value={url}
          style={{
            fontFamily: "ui-monospace, monospace",
            fontSize: "0.75rem",
            background: "rgba(0,0,0,0.25)",
            border: "1px solid rgba(255,255,255,0.1)",
            borderRadius: "6px",
            padding: "0.5rem 0.625rem",
            color: "var(--color-shell-text, #fff)",
            width: "100%",
            boxSizing: "border-box",
          }}
          onClick={(e) => (e.target as HTMLInputElement).select()}
          aria-label="Install URL"
        />

        <p
          style={{
            margin: 0,
            fontSize: "0.8125rem",
            color: "var(--color-shell-text-secondary, rgba(255,255,255,0.55))",
            lineHeight: 1.5,
          }}
        >
          {platformHint}
        </p>

        <div style={{ display: "flex", gap: "0.5rem", justifyContent: "flex-end", marginTop: "0.25rem" }}>
          <button
            onClick={handleCopy}
            style={{
              fontSize: "0.8125rem",
              padding: "0.4rem 0.875rem",
              borderRadius: "6px",
              border: "1px solid rgba(255,255,255,0.15)",
              background: "rgba(255,255,255,0.07)",
              color: "var(--color-shell-text, #fff)",
              cursor: "pointer",
              minWidth: "64px",
            }}
          >
            {copied ? "Copied!" : "Copy"}
          </button>
          <button
            onClick={onClose}
            style={{
              fontSize: "0.8125rem",
              padding: "0.4rem 0.875rem",
              borderRadius: "6px",
              border: "none",
              background: "rgba(255,255,255,0.15)",
              color: "var(--color-shell-text, #fff)",
              cursor: "pointer",
            }}
          >
            Done
          </button>
        </div>
      </div>
    </div>,
    document.body,
  );
}
