### Fixed

- Copy buttons now work on plain-HTTP LAN origins by falling back to `document.execCommand` when `navigator.clipboard` is unavailable
- Promoted clipboard logic from `InstallHelperPanel` into `desktop/src/lib/clipboard.ts` and pointed all 20 call sites at the shared helper
- Failed copies now surface an error to the user instead of being swallowed by bare `.catch()` blocks
