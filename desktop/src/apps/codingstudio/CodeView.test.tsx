import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { render, screen, fireEvent, waitFor, act } from "@testing-library/react";
import { CodeView } from "./CodeView";

function mockFetch(
  responses: Record<string, { ok: boolean; status?: number; body: unknown }>,
) {
  return vi.fn().mockImplementation((input: string) => {
    const hit = responses[input] ?? responses["*"];
    if (!hit) throw new Error(`Unmocked fetch: ${input}`);
    return Promise.resolve({
      ok: hit.ok,
      status: hit.status ?? (hit.ok ? 200 : 500),
      json: () => Promise.resolve(hit.body),
      text: () =>
        Promise.resolve(typeof hit.body === "string" ? hit.body : JSON.stringify(hit.body)),
    } as Response);
  });
}

async function flush() {
  await act(async () => {
    await new Promise((r) => setTimeout(r, 0));
  });
}

const capturedViews: any[] = [];

vi.mock("@codemirror/view", async () => {
  const actual = await vi.importActual("@codemirror/view");
  const OriginalEditorView = actual.EditorView;

  function MockEditorView(...args: any[]) {
    const view = new OriginalEditorView(...args);
    capturedViews.push(view);
    return view;
  }

  const descriptors = Object.getOwnPropertyDescriptors(OriginalEditorView);
  delete (descriptors as any).prototype;
  Object.defineProperties(MockEditorView, descriptors);

  return {
    ...actual,
    EditorView: MockEditorView,
  };
});

describe("CodeView", () => {
  beforeEach(() => {
    vi.restoreAllMocks();
    capturedViews.length = 0;
    vi.stubGlobal("fetch", mockFetch({
      "/api/coding/workspaces": {
        ok: true,
        body: [{ id: "ws-1", name: "My Workspace", path: "/tmp/ws", created_at: "2024-01-01" }],
      },
      "/api/coding/workspaces/ws-1/files": { ok: true, body: [{ name: "App.tsx", is_dir: false }] },
      "/api/coding/workspaces/ws-1/file?path=App.tsx": {
        ok: true,
        body: { path: "App.tsx", content: "export default function App() { return null; }" },
      },
      "*": { ok: true, body: {} },
    }));
  });

  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("loads file content and clears dirty state on save", async () => {
    vi.spyOn(window, "confirm").mockReturnValue(true);

    render(<CodeView />);
    await flush();

    await waitFor(() => {
      expect(screen.getByLabelText("Select workspace")).toBeTruthy();
    });

    const select = screen.getByLabelText("Select workspace");
    fireEvent.change(select, { target: { value: "ws-1" } });
    await flush();

    await waitFor(() => {
      expect(screen.getByText("App.tsx")).toBeTruthy();
    });

    fireEvent.click(screen.getByText("App.tsx"));
    await flush();

    await waitFor(() => {
      expect(
        screen.getByText("export default function App() { return null; }"),
      ).toBeTruthy();
    });

    const saveBtn = screen.getByRole("button", { name: "Save file" });
    expect(saveBtn).toBeDisabled();

    if (capturedViews.length > 0) {
      const view = capturedViews[0];
      act(() => {
        view.dispatch({
          changes: { from: view.state.doc.length, insert: "x" },
        });
      });
      await flush();
    }

    expect(saveBtn).not.toBeDisabled();

    fireEvent.click(saveBtn);
    await flush();

    await waitFor(() => {
      const fetchCalls = (global.fetch as any).mock.calls;
      expect(
        fetchCalls.some(
          (c: any[]) =>
            String(c[0]).includes("/api/coding/workspaces/ws-1/file") &&
            c[1]?.method === "PUT",
        ),
      ).toBe(true);
    });

    expect(saveBtn).toBeDisabled();
  });
});
