### Fixed

- Canvas: Excalidraw now loads all fonts (including CJK Xiaolai) from the same origin via a self-hosted `/excalidraw-assets/fonts/` tree, removing the external esm.sh dependency and the associated CSP block. The app is fully functional offline with zero CSP font violations.
- Dev: the Vite dev middleware now matches the `/desktop/` base path, strips the mount prefix, and rejects traversal, so font requests resolve correctly during `vite dev`.
