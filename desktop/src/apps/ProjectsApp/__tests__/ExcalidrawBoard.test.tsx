import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, waitFor, act, screen } from "@testing-library/react";

// Hoisted so the vi.mock factories below can close over them.
const { convertSpy, parseSpy } = vi.hoisted(() => ({
  convertSpy: vi.fn((els: unknown[]) => els),
  parseSpy: vi.fn(async (_src: string) => ({ elements: [] as unknown[], files: {} })),
}));

// Mock excalidraw-sync with a spy object we can assert against.
const { markRemoteSpy, onLocalChangeSpy, disposeSpy, createSceneSyncSpy, flushSpy } = vi.hoisted(() => ({
  markRemoteSpy: vi.fn(),
  onLocalChangeSpy: vi.fn(async () => {}),
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

// Excalidraw is a heavy canvas/worker component jsdom cannot run. Mock it so the
// board's mapping + wiring can be asserted: convertToExcalidrawElements passes
// the skeletons through so we can count them, and Excalidraw records the props
// it was handed.
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
    // Call the excalidrawAPI callback so the board gets the imperative API.
    // This runs during render but React 18 in tests allows it with act().
    excalidrawAPI?.(mockApi);
    // Expose onChange so tests can call it.
    (window as unknown as { __excalidrawOnChange?: (els: unknown[]) => void }).__excalidrawOnChange = onChange;
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
// Keep the heavy mermaid parser out of jsdom; a test that needs a ready
// diagram overrides the parse result.
vi.mock("@excalidraw/mermaid-to-excalidraw", () => ({
  parseMermaidToExcalidraw: parseSpy,
}));
// Mock excalidraw-sync so we can spy on its calls.
vi.mock("../canvas/excalidraw-sync", () => ({
  createSceneSync: createSceneSyncSpy,
}));

import { ExcalidrawBoard } from "../canvas/ExcalidrawBoard";
import type { CanvasElement } from "../canvas/canvas-api";

function el(over: Partial<CanvasElement>): CanvasElement {
  return {
    id: "e", project_id: "p", kind: "note", author_kind: "user", author_id: "u",
    x: 0, y: 0, w: 80, h: 60, rotation: 0, z_index: 0, payload: {},
    created_at: 0, updated_at: 0, deleted_at: null, ...over,
  };
}

function skeletonIds(call: unknown[]): string[] {
  return (call[0] as { id: string }[]).map((s) => s.id);
}

describe("ExcalidrawBoard", () => {
  beforeEach(() => {
    // mockReset also restores the pass-through after a test overrides it.
    convertSpy.mockReset();
    parseSpy.mockClear();
    markRemoteSpy.mockClear();
    onLocalChangeSpy.mockClear();
    flushSpy.mockClear();
    disposeSpy.mockClear();
    createSceneSyncSpy.mockClear();
    mockApi.updateScene.mockClear();
    mockApi.scrollToContent.mockClear();
    // Reset the exposed onChange.
    (window as unknown as { __excalidrawOnChange?: (els: unknown[]) => void }).__excalidrawOnChange = undefined;
  });

  it("maps non-deleted elements into the scene and renders read-only", () => {
    const { getByTestId } = render(
      <ExcalidrawBoard
        theme="dark"
        elements={[
          el({ id: "n1", kind: "note", payload: { text: "a" } }),
          el({ id: "t1", kind: "text", payload: { text: "idea" } }),
          el({ id: "gone", deleted_at: 1 }),
        ]}
      />,
    );
    const ex = getByTestId("excalidraw");
    expect(ex.getAttribute("data-count")).toBe("2");
    expect(ex.getAttribute("data-viewmode")).toBe("true");
    expect(ex.getAttribute("data-theme")).toBe("dark");
  });

  it("defaults to the light theme", () => {
    const { getByTestId } = render(<ExcalidrawBoard elements={[el({})]} />);
    expect(getByTestId("excalidraw").getAttribute("data-theme")).toBe("light");
  });

  it("renders an empty scene without crashing", () => {
    const { getByTestId } = render(<ExcalidrawBoard elements={[]} />);
    expect(getByTestId("excalidraw").getAttribute("data-count")).toBe("0");
  });

  it("mindmap_edge between two notes is converted in the same batch", () => {
    // An arrow binds start/end by the target's id, and the converter can only
    // resolve a binding to an element passed in the SAME call.
    const rows = [
      el({ id: "n1", kind: "note", z_index: 0, payload: { text: "a" } }),
      el({ id: "n2", kind: "note", z_index: 1, payload: { text: "b" } }),
      el({ id: "e1", kind: "mindmap_edge", z_index: 2, payload: { from: "n1", to: "n2" } }),
    ];
    const { rerender, getByTestId } = render(<ExcalidrawBoard elements={rows} />);
    // Count the conversions for one scene build: a fresh elements array
    // recomputes the scene exactly once.
    convertSpy.mockClear();
    rerender(<ExcalidrawBoard elements={[...rows]} />);
    expect(convertSpy).toHaveBeenCalledTimes(1);
    expect(skeletonIds(convertSpy.mock.calls[0])).toEqual(["n1", "n2", "e1"]);
    expect(getByTestId("excalidraw").getAttribute("data-ids")).toBe("n1,n2,e1");
  });

  it("a ready diagram keeps its z_index position", async () => {
    parseSpy.mockResolvedValueOnce({
      elements: [{ id: "d1", type: "rectangle", x: 0, y: 0 }],
      files: {},
    });
    const { getByTestId } = render(
      <ExcalidrawBoard
        elements={[
          el({ id: "top", kind: "note", z_index: 2, payload: { text: "top" } }),
          el({ id: "m1", kind: "mermaid", z_index: 1, payload: { source: "graph TD; A-->B" } }),
          el({ id: "bottom", kind: "note", z_index: 0, payload: { text: "bottom" } }),
        ]}
      />,
    );
    await waitFor(() =>
      expect(getByTestId("excalidraw").getAttribute("data-ids")).toBe("bottom,d1,top"),
    );
    // The ready diagram is spliced in after conversion: the last scene build
    // converted only the two notes, together, in one call.
    const last = convertSpy.mock.calls[convertSpy.mock.calls.length - 1];
    expect(skeletonIds(last)).toEqual(["bottom", "top"]);
  });

  it("a bound label text follows its container whatever the converter's order", () => {
    // A labelled skeleton converts to a container plus a text element that
    // carries no customData, only containerId. Emit n2's text BEFORE its
    // container: grouping must not depend on the converter's output order.
    convertSpy.mockImplementation((skeletons: unknown[]) =>
      (skeletons as { id: string; label?: { text: string } }[]).flatMap((s) => {
        const text = { id: `t-${s.id}`, type: "text", containerId: s.id, text: s.label?.text };
        return s.id === "n2" ? [text, s] : [s, text];
      }),
    );
    const { getByTestId } = render(
      <ExcalidrawBoard
        elements={[
          el({ id: "n1", kind: "note", z_index: 1, payload: { text: "a" } }),
          el({ id: "n2", kind: "note", z_index: 0, payload: { text: "b" } }),
        ]}
      />,
    );
    expect(getByTestId("excalidraw").getAttribute("data-ids")).toBe("t-n2,n2,n1,t-n1");
  });

  it("with projectId the board is editable and onChange reaches the sync", () => {
    const getElement = vi.fn((id: string) => ({ id, kind: "note" } as CanvasElement));
    const { getByTestId } = render(
      <ExcalidrawBoard
        projectId="prj-1"
        elements={[el({ id: "n1" })]}
        getElement={getElement}
      />,
    );
    // Board should be editable (viewModeEnabled = false).
    expect(getByTestId("excalidraw").getAttribute("data-viewmode")).toBe("false");
    // Sync should have been created once.
    expect(createSceneSyncSpy).toHaveBeenCalledTimes(1);
    // The sync receives a stable getElement wrapper that forwards to the latest getElement.
    const createCall = createSceneSyncSpy.mock.calls[0][0] as { getElement?: (id: string) => CanvasElement | undefined };
    expect(typeof createCall.getElement).toBe("function");
    // Verify the wrapper forwards to the provided getElement.
    expect(createCall.getElement?.("n1")).toEqual({ id: "n1", kind: "note" });
    // Call the exposed onChange (simulating Excalidraw firing onChange).
    const onChange = (window as unknown as { __excalidrawOnChange?: (els: unknown[]) => void }).__excalidrawOnChange;
    expect(onChange).toBeDefined();
    act(() => { onChange?.([{ id: "n1", type: "rectangle" }]); });
    // onLocalChange should have been called with the elements.
    expect(onLocalChangeSpy).toHaveBeenCalledTimes(1);
    expect(onLocalChangeSpy).toHaveBeenCalledWith([{ id: "n1", type: "rectangle" }]);
  });

  it("updateScene is followed by markRemote", async () => {
    const getElement = vi.fn((id: string) => ({ id, kind: "note" } as CanvasElement));
    const { rerender, getByTestId } = render(
      <ExcalidrawBoard
        projectId="prj-1"
        elements={[el({ id: "n1" })]}
        getElement={getElement}
      />,
    );
    // Wait for the effect to run and call markRemote.
    await waitFor(() => expect(markRemoteSpy).toHaveBeenCalledTimes(1));
    const firstCall = markRemoteSpy.mock.calls[0][0];
    expect(Array.isArray(firstCall)).toBe(true);
    // Re-render with new elements (simulates sceneElements change).
    markRemoteSpy.mockClear();
    rerender(
      <ExcalidrawBoard
        projectId="prj-1"
        elements={[el({ id: "n1" }), el({ id: "n2" })]}
        getElement={getElement}
      />,
    );
    // markRemote should be called again with the new scene.
    await waitFor(() => expect(markRemoteSpy).toHaveBeenCalledTimes(1));
    const secondCall = markRemoteSpy.mock.calls[0][0];
    expect(Array.isArray(secondCall)).toBe(true);
  });

  it("without projectId the board stays view-only", () => {
    const { getByTestId } = render(
      <ExcalidrawBoard elements={[el({ id: "n1" })]} />,
    );
    // Board should be view-only (viewModeEnabled = true).
    expect(getByTestId("excalidraw").getAttribute("data-viewmode")).toBe("true");
    // Sync should NOT have been created.
    expect(createSceneSyncSpy).not.toHaveBeenCalled();
  });

  it("readOnly=true forces view-only even with projectId", () => {
    const getElement = vi.fn((id: string) => ({ id, kind: "note" } as CanvasElement));
    const { getByTestId } = render(
      <ExcalidrawBoard
        projectId="prj-1"
        readOnly={true}
        elements={[el({ id: "n1" })]}
        getElement={getElement}
      />,
    );
    // Board should be view-only.
    expect(getByTestId("excalidraw").getAttribute("data-viewmode")).toBe("true");
    // Sync should NOT have been created.
    expect(createSceneSyncSpy).not.toHaveBeenCalled();
  });

  it("disposes sync on unmount", async () => {
    // Reset the mock to clear any previous calls
    createSceneSyncSpy.mockReset();
    markRemoteSpy.mockReset();
    onLocalChangeSpy.mockReset();
    // dispose is a property of the returned sync object

    const getElement = vi.fn((id: string) => ({ id, kind: "note" } as CanvasElement));

    const { unmount } = render(
      <ExcalidrawBoard
        projectId="prj-1"
        elements={[el({ id: "n1" })]}
        getElement={getElement}
      />,
    );

    // Wait for effects to run
    await vi.waitFor(() => expect(createSceneSyncSpy).toHaveBeenCalledTimes(1));

    // Unmount the component
    unmount();

    // The flush and dispose functions should have been called
    await vi.waitFor(() => {
      expect(flushSpy).toHaveBeenCalledTimes(1);
      expect(disposeSpy).toHaveBeenCalledTimes(1);
    });
  });

  it("keeps the same SceneSync when getElement changes but projectId stays the same", () => {
    const getElementV1 = vi.fn((id: string) => (id === "n1" ? ({ id, kind: "note" } as CanvasElement) : undefined));
    const { rerender } = render(
      <ExcalidrawBoard
        projectId="prj-1"
        elements={[el({ id: "n1" })]}
        getElement={getElementV1}
      />,
    );
    expect(createSceneSyncSpy).toHaveBeenCalledTimes(1);
    expect(disposeSpy).not.toHaveBeenCalled();

    // Capture the getElement that was passed to createSceneSync
    const createCall = createSceneSyncSpy.mock.calls[0][0] as { getElement?: (id: string) => CanvasElement | undefined };
    const syncedGetElement = createCall.getElement;

    // Rerender with a NEW element list and a NEW getElement function (same projectId)
    const getElementV2 = vi.fn((id: string) => (id === "n2" ? ({ id, kind: "note" } as CanvasElement) : undefined));
    rerender(
      <ExcalidrawBoard
        projectId="prj-1"
        elements={[el({ id: "n1" }), el({ id: "n2" })]}
        getElement={getElementV2}
      />,
    );

    // createSceneSync should NOT be called again (still exactly once total)
    expect(createSceneSyncSpy).toHaveBeenCalledTimes(1);
    // dispose should NOT have been called
    expect(disposeSpy).not.toHaveBeenCalled();

    // The getElement passed to createSceneSync should now forward to the latest (V2) map
    // and return the element that only exists in the NEW list
    expect(syncedGetElement?.("n2")).toEqual({ id: "n2", kind: "note" });
    expect(syncedGetElement?.("n1")).toBeUndefined(); // V2 doesn't know about n1
  });

  it("flush pending SceneSync writes before dispose", async () => {
    // Reset the mock to clear any previous calls
    createSceneSyncSpy.mockReset();
    markRemoteSpy.mockReset();
    onLocalChangeSpy.mockReset();

    const { unmount } = render(
      <ExcalidrawBoard
        projectId="prj-1"
        elements={[el({ id: "n1" })]}
      />,
    );

    // Wait for effects to run
    await vi.waitFor(() => expect(createSceneSyncSpy).toHaveBeenCalledTimes(1));

    // Unmount the component
    unmount();

    // The flush and dispose functions should have been called
    await vi.waitFor(() => {
      expect(flushSpy).toHaveBeenCalledTimes(1);
      expect(disposeSpy).toHaveBeenCalledTimes(1);
    });
  });

  it("api.updateScene receives captureUpdate: CaptureUpdateAction.NEVER", async () => {
    const { unmount } = render(
      <ExcalidrawBoard
        projectId="prj-1"
        elements={[el({ id: "n1" })]}
      />,
    );

    // Wait for effects to run
    await vi.waitFor(() => expect(markRemoteSpy).toHaveBeenCalledTimes(1));

    // Assert the mocked api.updateScene received captureUpdate equal to CaptureUpdateAction.NEVER
    expect(mockApi.updateScene).toHaveBeenCalledTimes(1);
    const callArgs = mockApi.updateScene.mock.calls[0][0];
    expect(callArgs.captureUpdate).toBe("NEVER");

    unmount();
  });

  it("viewModeEnabled is true when readOnly=false and no projectId", () => {
    const { getByTestId } = render(
      <ExcalidrawBoard
        readOnly={false}
        elements={[el({ id: "n1" })]}
      />,
    );
    // Board should be view-only (viewModeEnabled = true) because no projectId
    expect(getByTestId("excalidraw").getAttribute("data-viewmode")).toBe("true");
  });

  it("the accessible list has one entry per live row", () => {
    const rows = [
      el({ id: "n1", kind: "note", author_id: "user-1", payload: { text: "hello" } }),
      el({ id: "t1", kind: "text", author_id: "agent-2", payload: { text: "idea" } }),
      el({ id: "gone", deleted_at: 1 }),
      el({ id: "l1", kind: "link", author_id: "user-1", payload: { title: "link-title", url: "http://x" } }),
    ];
    render(<ExcalidrawBoard elements={rows} />);
    const list = screen.getByRole("list", { name: /canvas elements/i });
    const items = list.getElementsByTagName("li");
    expect(items).toHaveLength(3);
    expect(items[0].textContent).toBe("note, user-1, hello");
    expect(items[1].textContent).toBe("text, agent-2, idea");
    expect(items[2].textContent).toBe("link, user-1, link-title");
  });

  it("a legacy user_shape with a malformed blob shows a placeholder list entry carrying its element id", () => {
    const rows = [
      el({
        id: "legacy-1",
        kind: "user_shape",
        author_id: "user-1",
        payload: { tldraw_shape: { type: "image", props: {} } },
      }),
    ];
    // Override convertSpy to return a placeholder skeleton with taos_placeholder
    convertSpy.mockImplementation((skeletons) =>
      skeletons.map((s: any) => ({
        ...s,
        customData: { ...s.customData, taos_placeholder: true },
        label: { text: `tldraw image: not convertible (element ${s.id})` },
      })),
    );
    render(<ExcalidrawBoard elements={rows} />);
    const list = screen.getByRole("list", { name: /canvas elements/i });
    const items = list.getElementsByTagName("li");
    expect(items).toHaveLength(1);
    expect(items[0].textContent).toBe("not convertible, element legacy-1");
  });

  it("an image row is fetched once and handed to addFiles", async () => {
    const fileId = "file-123";
    const rows = [
      el({ id: "img-1", kind: "image", author_id: "user-1", payload: { file_id: fileId } }),
    ];
    const mockFetch = vi.fn((url: string) =>
      Promise.resolve({
        ok: true,
        blob: () => Promise.resolve(new Blob(["fake-image-data"], { type: "image/png" })),
      })
    );
    Object.defineProperty(globalThis, "fetch", { value: mockFetch, writable: true, configurable: true });

    // Capture the FileReader instance created by the component
    let capturedFileReader: { readAsDataURL: ReturnType<typeof vi.fn>; result: string; onload: ((ev: ProgressEvent<FileReader>) => void) | null; onerror: null } | null = null;
    class MockFileReader {
      readAsDataURL = vi.fn();
      result = "data:image/png;base64,fake";
      onload = null as ((ev: ProgressEvent<FileReader>) => void) | null;
      onerror = null;
      constructor() {
        capturedFileReader = this;
      }
    }
    vi.stubGlobal("FileReader", MockFileReader);

    const { rerender } = render(<ExcalidrawBoard elements={rows} projectSlug="test-slug" />);
    await act(async () => {});

    // Wait for the fetch to be called (effect runs after render)
    await waitFor(() => expect(mockFetch).toHaveBeenCalledTimes(1));
    expect(mockFetch).toHaveBeenCalledWith("/api/projects/test-slug/files/canvas/file-123");

    // Simulate FileReader onload on the captured instance
    act(() => { capturedFileReader?.onload?.({} as any); });

    await waitFor(() => expect(mockApi.addFiles).toHaveBeenCalledTimes(1));
    const addFilesCall = mockApi.addFiles.mock.calls[0][0];
    expect(addFilesCall).toHaveLength(1);
    expect(addFilesCall[0].id).toBe(fileId);
    expect(addFilesCall[0].dataURL).toBe("data:image/png;base64,fake");
    expect(addFilesCall[0].mimeType).toBe("image/png");
    expect(typeof addFilesCall[0].created).toBe("number");

    // Re-render with same elements - should not refetch
    mockFetch.mockClear();
    mockApi.addFiles.mockClear();
    await act(async () => {
      rerender(<ExcalidrawBoard elements={rows} projectSlug="test-slug" />);
    });
    await waitFor(() => expect(mockFetch).not.toHaveBeenCalled());
    expect(mockApi.addFiles).not.toHaveBeenCalled();
  });

  it("an image row retries fetch after a failed response", async () => {
    const fileId = "file-retry";
    const rows = [
      el({ id: "img-1", kind: "image", author_id: "user-1", payload: { file_id: fileId } }),
    ];
    let fetchCallCount = 0;
    const mockFetch = vi.fn((url: string) => {
      fetchCallCount++;
      if (fetchCallCount === 1) {
        // First call fails with ok: false
        return Promise.resolve({
          ok: false,
          status: 500,
          blob: () => Promise.resolve(new Blob(["error"], { type: "text/plain" })),
        });
      }
      // Second call succeeds
      return Promise.resolve({
        ok: true,
        blob: () => Promise.resolve(new Blob(["fake-image-data"], { type: "image/png" })),
      });
    });
    Object.defineProperty(globalThis, "fetch", { value: mockFetch, writable: true, configurable: true });

    // Capture the FileReader instance created by the component
    let capturedFileReader: { readAsDataURL: ReturnType<typeof vi.fn>; result: string; onload: ((ev: ProgressEvent<FileReader>) => void) | null; onerror: null } | null = null;
    class MockFileReader {
      readAsDataURL = vi.fn();
      result = "data:image/png;base64,fake";
      onload = null as ((ev: ProgressEvent<FileReader>) => void) | null;
      onerror = null;
      constructor() {
        capturedFileReader = this;
      }
    }
    vi.stubGlobal("FileReader", MockFileReader);

    const { rerender } = render(<ExcalidrawBoard elements={rows} projectSlug="test-slug" />);
    await act(async () => {});

    // Wait for the first fetch to be called and fail
    await waitFor(() => expect(mockFetch).toHaveBeenCalledTimes(1));
    expect(mockFetch).toHaveBeenCalledWith("/api/projects/test-slug/files/canvas/file-retry");
    expect(mockApi.addFiles).not.toHaveBeenCalled();

    // Re-render with same elements - should retry fetch
    mockFetch.mockClear();
    await act(async () => {
      rerender(<ExcalidrawBoard elements={[...rows]} projectSlug="test-slug" />);
    });
    await waitFor(() => expect(mockFetch).toHaveBeenCalledTimes(1));
    expect(mockFetch).toHaveBeenCalledWith("/api/projects/test-slug/files/canvas/file-retry");

    // Simulate FileReader onload on the captured instance
    act(() => { capturedFileReader?.onload?.({} as any); });

    await waitFor(() => expect(mockApi.addFiles).toHaveBeenCalledTimes(1));
    const addFilesCall = mockApi.addFiles.mock.calls[0][0];
    expect(addFilesCall).toHaveLength(1);
    expect(addFilesCall[0].id).toBe(fileId);
    expect(addFilesCall[0].dataURL).toBe("data:image/png;base64,fake");
    expect(addFilesCall[0].mimeType).toBe("image/png");
  });

  it("an image row with payload.alt appears in the accessible list", () => {
    const rows = [
      el({ id: "img-1", kind: "image", author_id: "user-1", payload: { file_id: "file-1", alt: "Sunset photo" } }),
    ];
    render(<ExcalidrawBoard elements={rows} />);
    const list = screen.getByRole("list", { name: /canvas elements/i });
    const items = list.getElementsByTagName("li");
    expect(items).toHaveLength(1);
    expect(items[0].textContent).toContain("Sunset photo");
    expect(items[0].textContent).toBe("image, user-1, Sunset photo");
  });
});
