// Sets the global asset path so Excalidraw can resolve self-hosted fonts
// and other static assets (translations, etc.) from the same origin.
// This import MUST run before "@excalidraw/excalidraw" is loaded.

(window as unknown as Record<string, string>).EXCALIDRAW_ASSET_PATH = new URL(
  "excalidraw-assets/",
  document.baseURI,
).href;
