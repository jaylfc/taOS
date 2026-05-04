/**
 * Keyboard shortcuts for BrowserApp.
 *
 * Cmd+T  → new tab
 * Cmd+W  → close active tab
 * Cmd+L  → focus address bar (dispatched as a CustomEvent the AddressBar listens for)
 * Cmd+F  → open find-in-page overlay (state managed by parent component)
 * Cmd++ / Cmd+= → zoom in
 * Cmd+- → zoom out
 * Cmd+0 → reset zoom
 *
 * Uses the meta key on macOS, ctrl on others. Only fires when the
 * BrowserApp window has focus (parent passes hasFocus).
 */
import { useEffect } from "react";
import { useBrowserStore } from "@/stores/browser-store";

export interface KeyboardShortcutsOptions {
  windowId: string;
  hasFocus: boolean;
  onOpenFind: () => void;
}

/** True when a form control has keyboard focus (input, textarea, select, contenteditable). */
function isInputFocused(): boolean {
  const el = document.activeElement;
  if (!el) return false;
  const tag = (el as HTMLElement).tagName.toLowerCase();
  return (
    tag === "input" ||
    tag === "textarea" ||
    tag === "select" ||
    (el as HTMLElement).isContentEditable
  );
}

const ZOOM_STEP = 0.1;

function isModifierMatch(e: KeyboardEvent): boolean {
  // macOS: Meta key. Other platforms: Ctrl.
  // navigator.platform is deprecated but still widely available;
  // userAgentData would be cleaner but not yet ubiquitous.
  const isMac = typeof navigator !== "undefined"
    && /Mac|iPhone|iPad|iPod/.test(navigator.platform);
  return isMac ? e.metaKey : e.ctrlKey;
}

export function useBrowserKeyboardShortcuts(opts: KeyboardShortcutsOptions) {
  const { windowId, hasFocus, onOpenFind } = opts;

  useEffect(() => {
    if (!hasFocus) return;

    const handler = (e: KeyboardEvent) => {
      if (!isModifierMatch(e)) return;

      // Cmd+Shift+A — open agent picker (check before the switch so we can
      // guard input focus independently of the other shortcuts)
      if (e.shiftKey && e.key.toLowerCase() === "a") {
        if (isInputFocused()) return;
        e.preventDefault();
        e.stopPropagation();
        window.dispatchEvent(
          new CustomEvent("taos-browser:open-agent-picker", {
            detail: { windowId },
          }),
        );
        return;
      }

      const store = useBrowserStore.getState();
      const win = store.windows[windowId];
      if (!win) return;
      const activeTab = win.tabs.find((t) => t.id === win.activeTabId);

      switch (e.key) {
        case "t":
        case "T":
          e.preventDefault();
          store.addTab(windowId);
          break;
        case "w":
        case "W":
          if (activeTab) {
            e.preventDefault();
            store.closeTab(windowId, activeTab.id);
          }
          break;
        case "l":
        case "L":
          e.preventDefault();
          window.dispatchEvent(
            new CustomEvent("taos-browser:focus-address", { detail: { windowId } }),
          );
          break;
        case "f":
        case "F":
          e.preventDefault();
          onOpenFind();
          break;
        case "+":
        case "=":
          if (activeTab) {
            e.preventDefault();
            const next = Math.round((activeTab.zoom + ZOOM_STEP) * 10) / 10;
            store.setTabZoom(windowId, activeTab.id, next);
          }
          break;
        case "-":
        case "_":
          if (activeTab) {
            e.preventDefault();
            const next = Math.round((activeTab.zoom - ZOOM_STEP) * 10) / 10;
            store.setTabZoom(windowId, activeTab.id, next);
          }
          break;
        case "0":
          if (activeTab) {
            e.preventDefault();
            store.setTabZoom(windowId, activeTab.id, 1.0);
          }
          break;
      }
    };

    window.addEventListener("keydown", handler);
    return () => window.removeEventListener("keydown", handler);
  }, [windowId, hasFocus, onOpenFind]);
}
