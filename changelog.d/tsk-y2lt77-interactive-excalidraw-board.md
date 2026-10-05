### Added
- Canvas: `ExcalidrawBoard` becomes the full interactive replacement for `CanvasBoard`, wired to the
  canvas REST API and SSE stream. It converts all non-diagram skeletons in a single `convertToExcalidrawElements`
  batch (so `mindmap_edge` arrow bindings resolve), lazily splices in ready mermaid/flowchart diagrams
  (grouped and locked), stamps `customData.taos_id`/`taos_kind`/`taos_updated_at` on containers and their
  bound labels, uploads canvas images once via `addFiles`, and pushes scene changes through `createSceneSync`
  (markRemote + onChange -> onLocalChange, with deletions of an empty scene still reported). A visually-hidden
  `<ul aria-label="Canvas elements">` lists every live row so agents and e2e probes can see the scene.
- `canvas/canvas-engine.ts`: `getCanvasEngine()` with precedence URL query `?canvas=`, then
  `localStorage` "taos.canvas.engine", then `DEFAULT_ENGINE = "tldraw"` — a development convenience only
  (header comment: deleted by the flip+removal card), never user-facing. `CanvasView.tsx` lazy-loads both
  engines and renders `ExcalidrawBoard` when the engine is "excalidraw", else `CanvasBoard`;
  `AppErrorBoundary` and the backup menu stay outside both.

### Fixed
- `canvas/canvas-api.ts`: `addFiles` now converts the data URL to a `Blob` directly (base64 decode) instead
  of `await (await fetch(data)).blob()`, which fails in non-browser test environments.
- `canvas/ExcalidrawBoard.tsx`: fixed the mermaid-diagram splicing to add the ready array once per
  diagram instead of once per part; fixed the `taos_id` stamping to look up rows by the skeleton's
  `customData.taos_original_element_id` instead of the reassigned Excalidraw-generated `id`; removed the
  `onChange` early return so an empty scene still reaches the sync core; moved `createSceneSync` off the
  `boardState` dependency to a `[projectId, elementId]`-only effect with a ref-backed getter; scoped
  `sceneElements` and the image-fetch effect to their specific sub-arrays instead of the whole `boardState`.
