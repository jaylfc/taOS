### Fixed

- `check-excalidraw-assets.mjs` now fails when the built excalidraw-assets/fonts dist holds a .woff2 that node_modules/@excalidraw/excalidraw/dist/prod/fonts does not, closing the stale-extra-font gap after Excalidraw upgrades; the two font roots are also overridable via the EXCALIDRAW_NODE_FONTS and EXCALIDRAW_DIST_FONTS env vars for testing.
