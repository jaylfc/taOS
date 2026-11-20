import { useEffect, useState } from "react";

const TEXT_INPUT_TYPES = new Set(["text", "search", "email", "url", "tel", "password", "number"]);

/** Follow open shadow roots and same-origin iframes down to the focused node. */
function deepActiveElement(doc: Document): Element | null {
  let el: Element | null = doc.activeElement;
  for (let i = 0; el && i < 10; i++) {
    if (el.shadowRoot?.activeElement) {
      el = el.shadowRoot.activeElement;
    } else if (el instanceof HTMLIFrameElement) {
      let inner: Document | null = null;
      try {
        inner = el.contentDocument;
      } catch {
        inner = null; // cross-origin: cannot look inside
      }
      if (!inner?.activeElement) break;
      el = inner.activeElement;
    } else {
      break;
    }
  }
  return el;
}

/** True when the element takes typed text and would raise a system keyboard. */
export function isTextEntryElement(el: Element | null): boolean {
  if (!el) return false;
  if (el.getAttribute("inputmode") === "none") return false;
  if (el instanceof HTMLInputElement) {
    return TEXT_INPUT_TYPES.has(el.type) && !el.readOnly && !el.disabled;
  }
  if (el instanceof HTMLTextAreaElement) return !el.readOnly && !el.disabled;
  if (el instanceof HTMLElement) {
    const ce = el.getAttribute("contenteditable");
    return ce !== null && ce !== "false" && el.isContentEditable !== false;
  }
  return false;
}

/**
 * True while a text-entry element holds focus. Focus, not window height, is the
 * signal: it survives rotation and desktop-window resizes and fires before the
 * on-screen keyboard has finished animating. Debounced so moving from one
 * field to the next does not flash false in between.
 */
export function useTextEntryActive(enabled = true, debounceMs = 100): boolean {
  const [active, setActive] = useState(false);

  useEffect(() => {
    if (!enabled) {
      setActive(false);
      return;
    }
    let timer: ReturnType<typeof setTimeout> | undefined;
    const evaluate = () => {
      if (timer) clearTimeout(timer);
      timer = setTimeout(() => setActive(isTextEntryElement(deepActiveElement(document))), debounceMs);
    };
    document.addEventListener("focusin", evaluate);
    document.addEventListener("focusout", evaluate);
    evaluate();
    return () => {
      if (timer) clearTimeout(timer);
      document.removeEventListener("focusin", evaluate);
      document.removeEventListener("focusout", evaluate);
    };
  }, [enabled, debounceMs]);

  return active;
}
