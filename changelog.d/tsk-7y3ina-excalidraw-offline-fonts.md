### Fixed

- Canvas: Excalidraw now loads all fonts (including CJK Xiaolai) from the same origin via a self-hosted `/excalidraw-assets/fonts/` tree, removing the external esm.sh dependency and the associated CSP block. The app is fully functional offline with zero CSP font violations.
- Dev: the Vite dev middleware now matches the `/desktop/` base path, strips the mount prefix, and rejects traversal, so font requests resolve correctly during `vite dev`.
- Fix URIError in `resolveExcalidrawFontPath` when malformed escapes like `%ZZ` appear in the font URL.
- Use Vite's `import.meta.env.BASE_URL` instead of `document.baseURI` for `EXCALIDRAW_ASSET_PATH` so the asset root resolves correctly relative to the app base.
- `check-excalidraw-assets.mjs` now compares the exact set of relative `.woff2` paths between node_modules and dist, failing with the first 5 missing paths when they diverge.
