import { render, screen } from "@testing-library/react";
import { renderHook, act } from "@testing-library/react";
import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import {
  ShortcutProvider,
  useShortcuts,
  useShortcut,
  parseCombo,
  matchesEvent,
} from "./use-shortcut-registry";
import type { ReactNode } from "react";

function wrapper({ children }: { children: ReactNode }) {
  return <ShortcutProvider>{children}</ShortcutProvider>;
}

function makeFakeEvent(key: string, opts: { ctrl?: boolean; shift?: boolean; alt?: boolean; meta?: boolean } = {}) {
  return {
    key,
    ctrlKey: opts.ctrl ?? false,
    shiftKey: opts.shift ?? false,
    altKey: opts.alt ?? false,
    metaKey: opts.meta ?? false,
    preventDefault: vi.fn(),
    stopPropagation: vi.fn(),
  } as unknown as KeyboardEvent;
}

function ShortcutRegistrar({ combo, action, label, scope }: { combo: string; action: () => void; label: string; scope?: "system" | "app" | "overlay" }) {
  useShortcut(combo, action, label, scope);
  return null;
}

describe("parseCombo", () => {
  it("parses a simple key", () => {
    expect(parseCombo("k")).toEqual({ ctrl: false, shift: false, alt: false, key: "k" });
  });

  it("parses ctrl+key combo", () => {
    expect(parseCombo("Ctrl+K")).toEqual({ ctrl: true, shift: false, alt: false, key: "k" });
  });

  it("parses ctrl+shift+key combo", () => {
    expect(parseCombo("Ctrl+Shift+S")).toEqual({ ctrl: true, shift: true, alt: false, key: "s" });
  });

  it("parses ctrl+alt+key combo", () => {
    const result = parseCombo("ctrl+alt+delete");
    expect(result.ctrl).toBe(true);
    expect(result.alt).toBe(true);
    expect(result.key).toBe("delete");
  });
});

describe("matchesEvent", () => {
  it("matches a plain key press", () => {
    expect(matchesEvent(parseCombo("k"), makeFakeEvent("k"))).toBe(true);
  });

  it("does not match a different key", () => {
    expect(matchesEvent(parseCombo("k"), makeFakeEvent("j"))).toBe(false);
  });

  it("matches ctrl+key combo", () => {
    expect(matchesEvent(parseCombo("Ctrl+S"), makeFakeEvent("s", { ctrl: true }))).toBe(true);
  });

  it("does not match when ctrl is missing", () => {
    expect(matchesEvent(parseCombo("Ctrl+S"), makeFakeEvent("s"))).toBe(false);
  });

  it("uses metaKey as ctrl", () => {
    expect(matchesEvent(parseCombo("Ctrl+K"), makeFakeEvent("k", { meta: true }))).toBe(true);
  });

  it("requires shift when specified", () => {
    expect(matchesEvent(parseCombo("Ctrl+Shift+A"), makeFakeEvent("a", { ctrl: true }))).toBe(false);
  });

  it("does not match when extra modifier is pressed", () => {
    expect(matchesEvent(parseCombo("Ctrl+A"), makeFakeEvent("a", { ctrl: true, shift: true }))).toBe(false);
  });
});

describe("useShortcuts outside provider", () => {
  it("returns a no-op getAll and keyboardLockActive=false", () => {
    const { result } = renderHook(() => useShortcuts());
    expect(result.current.getAll()).toEqual([]);
    expect(result.current.keyboardLockActive).toBe(false);
  });
});

describe("useShortcuts inside provider", () => {
  it("returns empty list initially", () => {
    const { result } = renderHook(() => useShortcuts(), { wrapper });
    expect(result.current.getAll()).toEqual([]);
    expect(result.current.keyboardLockActive).toBe(false);
  });

  it("keyboardLockActive starts as false", () => {
    const { result } = renderHook(() => useShortcuts(), { wrapper });
    expect(result.current.keyboardLockActive).toBe(false);
  });
});

describe("shortcut registration", () => {
  it("registers a shortcut and getAll reflects it", () => {
    const action = vi.fn();
    let shortcutsRef: ReturnType<typeof useShortcuts> | null = null;

    function CaptureShortcuts() {
      shortcutsRef = useShortcuts();
      return null;
    }

    act(() => {
      render(
        <ShortcutProvider>
          <CaptureShortcuts />
          <ShortcutRegistrar combo="Ctrl+K" action={action} label="Test shortcut" scope="app" />
        </ShortcutProvider>
      );
    });

    expect(shortcutsRef).not.toBeNull();
    const all = shortcutsRef!.getAll();
    expect(all).toHaveLength(1);
    expect(all[0].combo).toBe("Ctrl+K");
    expect(all[0].scope).toBe("app");
  });

  it("unregisters a shortcut on unmount", () => {
    const action = vi.fn();
    let shortcutsRef: ReturnType<typeof useShortcuts> | null = null;

    function CaptureShortcuts() {
      shortcutsRef = useShortcuts();
      return null;
    }

    const { rerender } = render(
      <ShortcutProvider>
        <CaptureShortcuts />
        <ShortcutRegistrar combo="Ctrl+X" action={action} label="Cut" />
      </ShortcutProvider>
    );

    expect(shortcutsRef!.getAll()).toHaveLength(1);

    act(() => {
      rerender(
        <ShortcutProvider>
          <CaptureShortcuts />
        </ShortcutProvider>
      );
    });

    expect(shortcutsRef!.getAll()).toHaveLength(0);
  });

  it("registers multiple shortcuts", () => {
    let shortcutsRef: ReturnType<typeof useShortcuts> | null = null;

    function CaptureShortcuts() {
      shortcutsRef = useShortcuts();
      return null;
    }

    act(() => {
      render(
        <ShortcutProvider>
          <CaptureShortcuts />
          <ShortcutRegistrar combo="Ctrl+A" action={vi.fn()} label="A" scope="system" />
          <ShortcutRegistrar combo="Ctrl+B" action={vi.fn()} label="B" scope="app" />
        </ShortcutProvider>
      );
    });

    const all = shortcutsRef!.getAll();
    expect(all).toHaveLength(2);
    const scopes = all.map((s: { scope: string }) => s.scope).sort();
    expect(scopes).toEqual(["app", "system"]);
  });

  it("defaults scope to system when not specified", () => {
    let shortcutsRef: ReturnType<typeof useShortcuts> | null = null;

    function CaptureShortcuts() {
      shortcutsRef = useShortcuts();
      return null;
    }

    act(() => {
      render(
        <ShortcutProvider>
          <CaptureShortcuts />
          <ShortcutRegistrar combo="Ctrl+Z" action={vi.fn()} label="Undo" />
        </ShortcutProvider>
      );
    });

    const all = shortcutsRef!.getAll();
    expect(all).toHaveLength(1);
    expect(all[0].scope).toBe("system");
  });
});
