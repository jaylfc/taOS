### Fixed

- Fixed a race condition in `useCanvasElements` where a stale `listElements` response from a superseded `elementId` scope could overwrite the current scope's rows, causing the canvas to show nothing after switching elements.