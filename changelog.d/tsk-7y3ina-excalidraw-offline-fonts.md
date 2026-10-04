### Added
- Excalidraw canvas is fully offline-capable: the Vite build now copies the entire `@excalidraw/excalidraw` font set (including the CJK Xiaolai family) into `dist/excalidraw-assets/fonts/`, and `window.EXCALIDRAW_ASSET_PATH` is set same-origin before the library loads.
