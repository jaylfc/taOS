### Fixed

- Copy buttons now work on plain-HTTP LAN origins by falling back to `document.execCommand` when `navigator.clipboard` is unavailable
- Promoted clipboard logic from `InstallHelperPanel` into `desktop/src/lib/clipboard.ts` and pointed all 20 call sites at the shared helper
- Failed copies now surface an error to the user instead of being swallowed by bare `.catch()` blocks
- Dropped `window.isSecureContext` guard from `copyText`; `navigator.clipboard` is already unavailable outside secure contexts, so the conjunct only blocked working clips
- `fallbackCopy` now catches `execCommand` errors and returns `false` instead of rejecting, restoring previously focused element after copy
- Removed duplicate `desktop/src/components/CodeBlock.test.tsx`; `__tests__/CodeBlock.test.tsx` is now the single source of truth
