### Fixed

- Canvas: Excalidraw now loads all fonts (including CJK Xiaolai) from the same origin via a self-hosted `/excalidraw-assets/fonts/` tree, removing the external esm.sh dependency and the associated CSP block. The app is fully functional offline with zero CSP font violations.
