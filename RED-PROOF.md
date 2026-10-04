# RED-PROOF

Failing run before the fix was applied:

```
 FAIL  src/__tests__/excalidraw-assets.test.ts > excalidraw offline assets > EXCALIDRAW_ASSET_PATH is same-origin after importing excalidraw-assets
 Error: Cannot find module '/tmp/exec-tsk-7y3ina/desktop/src/apps/ProjectsApp/canvas/excalidraw-assets.ts'

 FAIL  src/__tests__/excalidraw-assets.test.ts > excalidraw offline assets > ExcalidrawBoard.tsx and mermaid-to-elements.ts import ./excalidraw-assets before @excalidraw
 AssertionError: expected null not to be null

 Test Files  1 failed (1)
      Tests  2 failed (2)
```

Green run after the fix:

```
 Test Files  1 passed (1)
      Tests  2 passed (2)
```
