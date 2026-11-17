### Security
- Removed `allow-same-origin` from the iframe sandbox in `ServiceAppWindow` so container service apps get an opaque origin and cannot reach the shell's DOM, storage, or controller API.
- Added `Content-Security-Policy: sandbox allow-scripts allow-forms allow-popups` (no `allow-same-origin`) to `/apps/{app_id}/` proxy responses, isolating direct top-level navigations to the app URL as well.
