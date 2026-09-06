import { useEffect, useRef, type RefObject } from "react";

const FOCUSABLE = 'a[href], button:not([disabled]), input:not([disabled]), select:not([disabled]), textarea:not([disabled]), [tabindex]:not([tabindex="-1"])';

export function getFocusableElements(container: HTMLElement | null): HTMLElement[] {
  if (!container) return [];
  return Array.from(container.querySelectorAll<HTMLElement>(FOCUSABLE));
}

export function useFocusTrap(ref: RefObject<HTMLElement | null>, active: boolean) {
  const previousFocus = useRef<HTMLElement | null>(null);

  useEffect(() => {
    if (!active || !ref.current) return;

    previousFocus.current = document.activeElement as HTMLElement;
    const focusable = getFocusableElements(ref.current);
    if (focusable.length > 0) focusable[0]!.focus();

    const handler = (e: KeyboardEvent) => {
      if (e.key !== "Tab") return;
      const elements = getFocusableElements(ref.current);
      if (elements.length === 0) return;

      const first = elements[0]!;
      const last = elements[elements.length - 1]!;

      if (e.shiftKey && document.activeElement === first) {
        e.preventDefault();
        last.focus();
      } else if (!e.shiftKey && document.activeElement === last) {
        e.preventDefault();
        first.focus();
      }
    };

    const el = ref.current;
    el.addEventListener("keydown", handler);
    return () => {
      el.removeEventListener("keydown", handler);
      previousFocus.current?.focus();
    };
  }, [ref, active]);
}
