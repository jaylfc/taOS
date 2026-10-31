### Added
- Excalidraw canvas now renders a visually-hidden accessible element list (`<ul aria-label="Canvas elements">`) with one entry per live row showing kind, author, and text/title/label.
- Image elements are fetched once per file ID via `/api/projects/{slug}/files/canvas/{file_id}`, converted to data URLs, and registered with Excalidraw via `api.addFiles()`.
- Excalidraw toolbar image tool disabled via `UIOptions={{ tools: { image: false } }}`.
- ExcalidrawCanvas now reads the desktop theme (light/dark) from `useThemeStore` and passes it to the board.