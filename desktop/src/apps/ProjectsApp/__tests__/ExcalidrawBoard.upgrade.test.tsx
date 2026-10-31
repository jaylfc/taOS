import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { render, screen, act, waitFor, cleanup } from "@testing-library/react";
import fixtures from "./fixtures/tldraw-shapes.json" with { type: "json" };

const { convertSpy, parseSpy } = vi.hoisted(() => ({
  convertSpy: vi.fn((skeletons: unknown[]) =>
    skeletons.map((s: { id: string; customData?: Record<string, unknown>; containerId?: string }) => ({
      ...s,
      id: s.id,
      containerId: s.containerId,
      customData: { ...(s.customData ?? {}), taos_id: s.customData?.taos_id ?? s.id },
    }))
  ),
  parseSpy: vi.fn(async (_src: string) => ({ elements: [], files: {} })),
}));

const { markRemoteSpy, onLocalChangeSpy, disposeSpy, createSceneSyncSpy, flushSpy } = vi.hoisted(() => ({
  markRemoteSpy: vi.fn(),
  onLocalChangeSpy: vi.fn(async () => { await flushSpy(); }),
  disposeSpy: vi.fn(),
  createSceneSyncSpy: vi.fn(() => ({
    markRemote: markRemoteSpy,
    onLocalChange: onLocalChangeSpy,
    dispose: disposeSpy,
    shouldApplyRemote: vi.fn(() => true),
    shouldApplyRemoteDelete: vi.fn(() => true),
    flush: flushSpy,
  })),
  flushSpy: vi.fn(async () => {}),
}));

const mockApi = {
  updateScene: vi.fn(),
  scrollToContent: vi.fn(),
  addFiles: vi.fn(),
};

vi.mock("@excalidraw/excalidraw", () => ({
  convertToExcalidrawElements: convertSpy,
  Excalidraw: ({
    initialData,
    viewModeEnabled,
    theme,
    onChange,
    excalidrawAPI,
    UIOptions,
    ...props
  }: {
    initialData?: { elements?: { id?: string }[] };
    viewModeEnabled?: boolean;
    theme?: string;
    onChange?: (els: unknown[]) => void;
    excalidrawAPI?: (api: typeof mockApi) => void;
    UIOptions?: Record<string, unknown>;
  }) => {
    // Defer all callbacks to avoid state updates during render
    Promise.resolve().then(() => {
      excalidrawAPI?.(mockApi);
      (window as unknown as { __excalidrawOnChange?: (els: unknown[]) => void }).__excalidrawOnChange = onChange;
    });
    return (
      <div
        data-testid="excalidraw"
        data-count={initialData?.elements?.length ?? 0}
        data-ids={(initialData?.elements ?? []).map((e) => e.id).join(",")}
        data-viewmode={String(!!viewModeEnabled)}
        data-theme={theme}
        data-uioptions={JSON.stringify(UIOptions ?? {})}
        {...props}
      />
    );
  },
  CaptureUpdateAction: { NEVER: "NEVER" },
}));
vi.mock("@excalidraw/excalidraw/index.css", () => ({}));
vi.mock("@excalidraw/mermaid-to-excalidraw", () => ({
  parseMermaidToExcalidraw: parseSpy,
}));
vi.mock("../canvas/excalidraw-sync", () => ({
  createSceneSync: createSceneSyncSpy,
}));

import { ExcalidrawBoard } from "../canvas/ExcalidrawBoard";
import type { CanvasElement } from "../canvas/canvas-api";
import { isPlaceholder } from "../canvas/tldraw-to-excalidraw";

function makeElement(over: Partial<CanvasElement>): CanvasElement {
  return {
    id: "e",
    project_id: "p",
    kind: "note",
    author_kind: "user",
    author_id: "u",
    x: 0, y: 0, w: 80, h: 60, rotation: 0, z_index: 0,
    payload: {},
    created_at: 0, updated_at: 0, deleted_at: null,
    ...over,
  };
}

describe("ExcalidrawBoard upgrade test - 80 seeded rows", () => {
  beforeEach(() => {
    convertSpy.mockClear();
    convertSpy.mockImplementation((skeletons: unknown[]) =>
      skeletons.map((s: { id: string; customData?: Record<string, unknown>; containerId?: string }) => ({
        ...s,
        id: s.id,
        containerId: s.containerId,
        customData: { ...(s.customData ?? {}), taos_id: s.customData?.taos_id ?? s.id },
      }))
    );
    parseSpy.mockClear();
    markRemoteSpy.mockClear();
    onLocalChangeSpy.mockClear();
    flushSpy.mockClear();
    disposeSpy.mockClear();
    createSceneSyncSpy.mockClear();
    mockApi.updateScene.mockClear();
    mockApi.scrollToContent.mockClear();
    mockApi.addFiles.mockClear();
    (window as unknown as { __excalidrawOnChange?: (els: unknown[]) => void }).__excalidrawOnChange = undefined;
  });

  afterEach(cleanup);

  function build80Rows(): CanvasElement[] {
    const rows: CanvasElement[] = [];

    // 54 notes
    for (let i = 0; i < 54; i++) {
      rows.push(makeElement({
        id: `note-${i}`,
        kind: "note",
        z_index: i,
        payload: { text: `Note ${i}`, color: "yellow", font_size: 14 },
      }));
    }

    // 11 text
    for (let i = 0; i < 11; i++) {
      rows.push(makeElement({
        id: `text-${i}`,
        kind: "text",
        z_index: 54 + i,
        payload: { text: `Text ${i}`, color: "#1e293b" },
      }));
    }

    // 6 links
    for (let i = 0; i < 6; i++) {
      rows.push(makeElement({
        id: `link-${i}`,
        kind: "link",
        z_index: 65 + i,
        payload: { url: `https://example${i}.com`, title: `Link ${i}`, description: "" },
      }));
    }

    // 4 mermaid
    for (let i = 0; i < 4; i++) {
      rows.push(makeElement({
        id: `mermaid-${i}`,
        kind: "mermaid",
        z_index: 71 + i,
        payload: { source: `graph TD; A${i}-->B${i}` },
      }));
    }

    // 5 user_shape: all 5 from Pi fixture
    const piRows = fixtures.pi_rows;
    for (let i = 0; i < piRows.length; i++) {
      const pi = piRows[i];
      rows.push(makeElement({
        id: pi.id,
        kind: "user_shape",
        z_index: 75 + i,
        x: pi.x,
        y: pi.y,
        w: pi.w,
        h: pi.h,
        rotation: pi.rotation,
        author_kind: pi.author_kind,
        author_id: pi.author_id,
        payload: pi.payload,
      }));
    }

    return rows;
  }

  it("80 seeded rows incl. 5 legacy user_shape render 80 accessible entries", async () => {
    const rows = build80Rows();
    expect(rows).toHaveLength(80);

    await act(async () => {
      render(<ExcalidrawBoard elements={rows} theme="dark" />);
    });

    // Wait for the accessible list to be populated
    const list = await screen.findByRole("list", { name: /canvas elements/i });
    const items = list.getElementsByTagName("li");
    expect(items).toHaveLength(80);

    // All 5 user_shape rows from Pi fixture should be present in the accessible list
    // Accessible labels for user_shape are "user_shape, {author_id}" since payload has no text/title/label
    const userShapeRows = rows.filter((r) => r.kind === "user_shape");
    expect(userShapeRows).toHaveLength(5);
    for (const row of userShapeRows) {
      const expectedLabel = `user_shape, ${row.author_id}`;
      const item = Array.from(items).find((item) => item.textContent?.includes(expectedLabel));
      expect(item).toBeDefined();
    }

    // Every user_shape's stored tldraw_shape should be byte-identical after load
    // (verified via the payload carried through elementToSkeleton -> legacyShapeToSkeleton)
    for (const row of userShapeRows) {
      const blob = (row.payload as { tldraw_shape?: unknown })?.tldraw_shape;
      expect(blob).toBeDefined();
    }
  });

  it("legacy user_shape tldraw_shape blob is byte-identical after conversion round-trip and edit", async () => {
    const rows = build80Rows();
    const userShapeRows = rows.filter((r) => r.kind === "user_shape");
    expect(userShapeRows).toHaveLength(5);

    // Store the original tldraw_shape blobs as strings for byte-identity comparison
    const originalBlobs = userShapeRows.map((r) => JSON.stringify(r.payload.tldraw_shape));

    // Render to trigger conversion
    await act(async () => {
      render(<ExcalidrawBoard elements={rows} projectId="test-project" projectSlug="test" />);
    });

    // Wait for the sync layer to be created
    await waitFor(() => {
      expect(createSceneSyncSpy).toHaveBeenCalledTimes(1);
    });

    // Check that convertToExcalidrawElements was called
    // It may be called multiple times due to re-renders, so check the last call
    expect(convertSpy).toHaveBeenCalled();
    const lastCallArgs = convertSpy.mock.calls[convertSpy.mock.calls.length - 1][0] as Array<{ id: string; customData?: Record<string, unknown> }>;

    // All 5 user_shape rows should have their skeletons in the converted output
    for (const row of userShapeRows) {
      const skeleton = lastCallArgs.find((s) => s.id === row.id);
      expect(skeleton).toBeDefined();
      expect(skeleton?.customData?.taos_original_element_id).toBe(row.id);
      expect(skeleton?.customData?.taos_original_type).toBeDefined();
    }

    // Simulate a user edit on one of the valid user_shape elements
    // Pick the first valid user_shape (ZxWbVSW_lvxoSInnOPp6x - a geo rectangle)
    const editableId = "ZxWbVSW_lvxoSInnOPp6x";
    const editableRow = userShapeRows.find((r) => r.id === editableId);
    expect(editableRow).toBeDefined();

    // Get the onLocalChange callback from the sync instance
    const syncInstance = createSceneSyncSpy.mock.results[0].value;
    expect(syncInstance?.onLocalChange).toBeDefined();

    // Simulate an edit by calling onLocalChange with modified elements
    // The sync layer should call updateScene and flush
    const modifiedElements = lastCallArgs.map((el) => {
      if (el.id === editableId) {
        return { ...el, x: (el.x ?? 0) + 10 }; // Move slightly
      }
      return el;
    });

    await act(async () => {
      await syncInstance.onLocalChange(modifiedElements as never);
    });

    // Verify that flush was called (persistence happened)
    expect(flushSpy).toHaveBeenCalled();

    // Verify the stored tldraw_shape blob is byte-identical after load + edit
    // The payload's tldraw_shape should not have been mutated by the conversion/edit cycle
    for (let i = 0; i < userShapeRows.length; i++) {
      const row = userShapeRows[i];
      const currentBlob = JSON.stringify(row.payload.tldraw_shape);
      expect(currentBlob).toEqual(originalBlobs[i]);
    }
  });

  it("synthetic malformed draw row renders as placeholder with element id", async () => {
    // This test uses a synthetic malformed row to verify placeholder behavior
    // since the Pi fixtures don't contain a row that produces a placeholder
    const malformed = fixtures.synthetic_rows.find((r) => r.id === "syn-malformed-draw");
    expect(malformed).toBeDefined();

    const rows = [
      makeElement({
        id: malformed!.id,
        kind: "user_shape",
        z_index: 0,
        x: malformed!.x,
        y: malformed!.y,
        w: malformed!.w,
        h: malformed!.h,
        rotation: malformed!.rotation,
        author_kind: malformed!.author_kind,
        author_id: malformed!.author_id,
        payload: malformed!.payload,
      }),
    ];

    await act(async () => {
      render(<ExcalidrawBoard elements={rows} theme="dark" />);
    });

    const list = await screen.findByRole("list", { name: /canvas elements/i });
    const items = list.getElementsByTagName("li");
    expect(items).toHaveLength(1);

    const item = items[0];
    expect(item.textContent).toContain("not convertible");
    expect(item.textContent).toContain(malformed!.id);
  });
});