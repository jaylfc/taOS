import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { render, screen, fireEvent, act } from "@testing-library/react";
import React from "react";
import { EditorView } from "@codemirror/view";

import { TextEditorApp } from "../TextEditorApp";

describe("TextEditorApp", () => {
  beforeEach(() => {
    localStorage.clear();
    vi.spyOn(window, "fetch").mockResolvedValue({ ok: true, json: async () => ({}) } as Response);
  });

  afterEach(() => {
    vi.restoreAllMocks();
  });

  it("creates a note and shows it in the sidebar (crypto.randomUUID available)", () => {
    render(<TextEditorApp windowId="w1" />);

    fireEvent.click(screen.getByRole("button", { name: /create your first note/i }));

    // The new note becomes the active/selected entry, with a delete affordance
    // in the sidebar keyed to its (unique) title.
    expect(screen.getByRole("button", { name: "Delete New Note" })).toBeInTheDocument();

    // Persisted to localStorage so it survives a reload.
    const stored = JSON.parse(localStorage.getItem("tinyagentos-notes") ?? "[]");
    expect(stored).toHaveLength(1);
    expect(stored[0].content).toContain("New Note");
  });

  it("still creates a note when crypto.randomUUID is unavailable (insecure-context regression, #1584)", () => {
    // taOS is normally served over plain http (e.g. http://taos.local:6969),
    // which is not a "secure context" per the spec, so window.crypto.randomUUID
    // is undefined there. Simulate that and confirm note creation still works
    // instead of silently throwing inside the click handler.
    // randomUUID lives on the Crypto prototype, not as an own property, so
    // `delete crypto.randomUUID` is a silent no-op — shadow it with an own
    // property instead to faithfully simulate the missing API.
    const originalRandomUUID = crypto.randomUUID;
    Object.defineProperty(crypto, "randomUUID", { value: undefined, configurable: true });

    try {
      render(<TextEditorApp windowId="w1" />);

      fireEvent.click(screen.getByRole("button", { name: /create your first note/i }));

      expect(screen.getAllByText("New Note").length).toBeGreaterThan(0);
      const stored = JSON.parse(localStorage.getItem("tinyagentos-notes") ?? "[]");
      expect(stored).toHaveLength(1);
      expect(typeof stored[0].id).toBe("string");
      expect(stored[0].id.length).toBeGreaterThan(0);
    } finally {
      Object.defineProperty(crypto, "randomUUID", { value: originalRandomUUID, configurable: true });
    }
  });

  it("keeps the CodeMirror view mounted and focused across consecutive keystrokes (#1596)", () => {
    // Simulate the non-secure-context (plain http) environment taOS normally
    // runs in, same as the regression test above, so this also proves the
    // remaining fix isn't hiding behind an available crypto.randomUUID.
    const originalRandomUUID = crypto.randomUUID;
    Object.defineProperty(crypto, "randomUUID", { value: undefined, configurable: true });

    try {
      const { container } = render(<TextEditorApp windowId="w1" />);
      fireEvent.click(screen.getByRole("button", { name: /create your first note/i }));

      const cmDom = container.querySelector(".cm-editor") as HTMLElement;
      expect(cmDom).toBeTruthy();
      const initialView = EditorView.findFromDOM(cmDom);
      expect(initialView).toBeTruthy();
      act(() => initialView!.focus());

      // Type several characters one at a time, the way a real keystroke
      // reaches CodeMirror: each one dispatches a doc change, which fires the
      // app's onChange -> setNotes -> re-render with the updated content.
      let view = initialView!;
      for (const ch of ["h", "e", "l", "l", "o"]) {
        expect(() => {
          act(() => {
            const head = view.state.selection.main.head;
            view.dispatch({
              changes: { from: head, insert: ch },
              selection: { anchor: head + ch.length },
            });
          });
        }).not.toThrow();

        // The view must survive the resulting re-render untouched: same DOM
        // node, same EditorView instance, still focused. Before the fix, the
        // editor's mount effect depended on `content` and tore the whole view
        // down and rebuilt it (unfocused) on every keystroke, matching the
        // reported "type one char, lose focus, click, type one more" bug.
        const domAfter = container.querySelector(".cm-editor") as HTMLElement;
        expect(domAfter).toBe(cmDom);
        const viewAfter = EditorView.findFromDOM(domAfter);
        expect(viewAfter).toBe(view);
        expect(viewAfter!.hasFocus).toBe(true);
        view = viewAfter!;
      }

      const stored = JSON.parse(localStorage.getItem("tinyagentos-notes") ?? "[]");
      expect(stored[0].content).toContain("hello");
    } finally {
      Object.defineProperty(crypto, "randomUUID", { value: originalRandomUUID, configurable: true });
    }
  });
});
