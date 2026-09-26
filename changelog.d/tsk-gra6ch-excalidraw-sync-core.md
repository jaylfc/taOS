### Added

- Added the Excalidraw scene-to-CanvasElement sync core (`desktop/src/apps/ProjectsApp/canvas/excalidraw-sync.ts`): diffs scenes by element version, suppresses echoes of remote and own writes, debounces and merges PATCHes per row, drops a stale local write when a newer remote change lands first, simplifies oversized freedraw strokes under the payload cap, and never sends a `user_shape` payload that drops `tldraw_shape`. Pure module with type-only Excalidraw imports; the interactive board wires it in a later slice.
