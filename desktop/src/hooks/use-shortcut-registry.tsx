import { createContext, useContext, useCallback, useEffect, useRef, useState, type ReactNode } from "react";

export interface ParsedCombo {
  ctrl: boolean;
  shift: boolean;
  alt: boolean;
  key: string;
}

export function parseCombo(combo: string): ParsedCombo {
  const parts = combo.toLowerCase().split("+").map((p) => p.trim());
  return {
    ctrl: parts.includes("ctrl"),
    shift: parts.includes("shift"),
    alt: parts.includes("alt"),
    key: parts.filter((p) => !["ctrl", "shift", "alt"].includes(p))[0] ?? "",
  };
}

export function matchesEvent(parsed: ParsedCombo, e: KeyboardEvent): boolean {
  const ctrl = e.ctrlKey || e.metaKey;
  if (parsed.ctrl !== ctrl) return false;
  if (parsed.shift !== e.shiftKey) return false;
  if (parsed.alt !== e.altKey) return false;
  return e.key.toLowerCase() === parsed.key;
}

export type ShortcutScope = "system" | "app" | "overlay";

export interface ShortcutEntry {
  id: string;
  combo: ParsedCombo;
  action: () => void;
  label: string;
  scope: ShortcutScope;
  enabled: boolean;
}

interface ShortcutRegistryContext {
  register: (id: string, combo: string, action: () => void, label: string, scope?: ShortcutScope) => void;
  unregister: (id: string) => void;
  getAll: () => { combo: string; label: string; scope: ShortcutScope }[];
  keyboardLockActive: boolean;
}

const Ctx = createContext<ShortcutRegistryContext | null>(null);

const SCOPE_PRIORITY: Record<ShortcutScope, number> = { overlay: 0, app: 1, system: 2 };

function formatCombo(parsed: ParsedCombo): string {
  const parts: string[] = [];
  if (parsed.ctrl) parts.push("Ctrl");
  if (parsed.shift) parts.push("Shift");
  if (parsed.alt) parts.push("Alt");
  parts.push(parsed.key.charAt(0).toUpperCase() + parsed.key.slice(1));
  return parts.join("+");
}

export function ShortcutProvider({ children }: { children: ReactNode }) {
  const entriesRef = useRef<Map<string, ShortcutEntry>>(new Map());
  const [keyboardLockActive, setKeyboardLockActive] = useState(false);

  useEffect(() => {
    const onFullscreenChange = () => {
      if (document.fullscreenElement && (navigator as unknown as { keyboard?: { lock: () => Promise<void> } }).keyboard?.lock) {
        (navigator as unknown as { keyboard: { lock: () => Promise<void> } }).keyboard
          .lock()
          .then(() => setKeyboardLockActive(true))
          .catch(() => setKeyboardLockActive(false));
      } else {
        setKeyboardLockActive(false);
      }
    };
    document.addEventListener("fullscreenchange", onFullscreenChange);
    return () => document.removeEventListener("fullscreenchange", onFullscreenChange);
  }, []);

  useEffect(() => {
    const handler = (e: KeyboardEvent) => {
      const entries = Array.from(entriesRef.current.values())
        .filter((s) => s.enabled)
        .sort((a, b) => SCOPE_PRIORITY[a.scope] - SCOPE_PRIORITY[b.scope]);

      for (const entry of entries) {
        if (matchesEvent(entry.combo, e)) {
          e.preventDefault();
          e.stopPropagation();
          entry.action();
          return;
        }
      }
    };
    window.addEventListener("keydown", handler);
    return () => window.removeEventListener("keydown", handler);
  }, []);

  const register = useCallback(
    (id: string, combo: string, action: () => void, label: string, scope: ShortcutScope = "system") => {
      entriesRef.current.set(id, { id, combo: parseCombo(combo), action, label, scope, enabled: true });
    },
    []
  );

  const unregister = useCallback((id: string) => {
    entriesRef.current.delete(id);
  }, []);

  const getAll = useCallback(() => {
    return Array.from(entriesRef.current.values()).map((e) => ({
      combo: formatCombo(e.combo),
      label: e.label,
      scope: e.scope,
    }));
  }, []);

  return <Ctx.Provider value={{ register, unregister, getAll, keyboardLockActive }}>{children}</Ctx.Provider>;
}

export function useShortcut(combo: string, action: () => void, label: string, scope: ShortcutScope = "system") {
  const ctx = useContext(Ctx);
  const idRef = useRef(`shortcut-${combo}-${Math.random().toString(36).slice(2, 8)}`);

  useEffect(() => {
    ctx?.register(idRef.current, combo, action, label, scope);
    return () => ctx?.unregister(idRef.current);
  }, [ctx, combo, action, label, scope]);
}

export function useShortcuts() {
  const ctx = useContext(Ctx);
  return {
    getAll: ctx?.getAll ?? (() => []),
    keyboardLockActive: ctx?.keyboardLockActive ?? false,
  };
}
